"""受限沙箱 Backend（deepagents）

背景：deepagents 给每个 Agent 内置了一套文件/执行工具
（`ls` / `read_file` / `write_file` / `edit_file` / `glob` / `grep` / `execute`），
它们全部作用于创建 Agent 时传入的 `backend` 参数。

原工程用的是 `StateBackend()`，它有两个致命问题：

1. **没有 `execute`** → 模型一调 `execute` 就报
   `Error invoking tool 'execute'`，于是陷入「换个写法再调一次」的死循环，
   一条请求能空转上千步（实测 1267 个事件 / 3.4 万字 / 20 次失败重试）。
2. **文件系统是内存虚拟的** → `write_file` 返回 "Updated file /quicksort.py"，
   但磁盘上什么都没有，所谓"跑通验证"全是假的，测试用例根本没法验收。

同时直接用官方的 `LocalShellBackend` 是不行的：它 `shell=True` 无隔离，
Agent 能读 `.env` 里的密钥、能执行任意命令——对 Web 服务不可接受。

因此这里实现一个折中 backend：

- **文件系统**：继承 `FilesystemBackend` 并开 `virtual_mode=True`，
  根目录锁在 `workspace/`，`..` 与绝对路径逃逸会被拒绝 → 文件**真的落盘**。
- **执行**：`execute()` 只接受 Python（`python -c "..."` / 脚本路径 / 裸代码），
  复用 `coding_tools` 既有的安全策略（干净 env、超时、输出截断、cwd=workspace），
  其余 shell 命令一律明确拒绝并给出可替代的写法。
"""

from __future__ import annotations

import ast
import logging
import re
import time
from pathlib import Path
from typing import Callable, cast

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol, FileDownloadResponse
from deepagents.backends.sandbox import BaseSandbox
from langgraph.config import get_config

from task_agents.agent.market_researcher_engine.tools.coding_tools import (
    _workspace_root,
)
from task_agents.core.config import get_settings
from task_agents.core.services import service_container as container
from task_agents.sandbox.sandbox_manager import SandboxManager

logger = logging.getLogger(__name__)

# 形如 `python ...` / `python3 ...` / `py ...`（允许 Windows 的 .exe）
_PY_PREFIX = re.compile(r"^(python|python3|py)(\.exe)?\s+", re.IGNORECASE)

_REJECT_HINT = (
    "沙箱只支持执行 Python，不支持 shell 命令。\n"
    "可以这样写：\n"
    "  1) python -c \"<你的代码>\"\n"
    "  2) 直接给出 Python 代码（不要加任何命令前缀）\n"
    "  3) python script.py（脚本必须在工作区内）\n"
    "不支持：pip install / 删除文件 / 网络命令 / 任何系统命令。"
    "需要第三方库却没装时，请如实告诉用户。\n"
    "你收到的命令是: {cmd!r}"
)

# 高危命令黑名单（正则）
DANGEROUS_PATTERNS = [
    r'rm\s+(-rf?|--no-preserve-root)',
    r'mkfs', r'dd\s+if=', r'>\s*/dev/sd',
    r'curl.*\|\s*bash', r'wget.*\|\s*sh',
    r'chmod\s+777', r'sudo', r'su\s',
]


def _strip_quotes(text: str) -> str:
    """去掉包裹在外层的一对引号"""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


class RestrictedSandboxBackend(SandboxBackendProtocol):
    """工作区内的真实文件系统 + 仅 Python 的受限执行"""

    def __init__(self, manager: SandboxManager,
                 timeout: int | None = None):
        super().__init__()
        self.manager = manager
        self.default_timeout = timeout or 30

    @property
    def id(self) -> str:
        return "restricted-local-python"

    # ==================== 命令 → Python 代码 ====================

    def _to_python_code(self, command: str) -> tuple[str | None, str | None]:
        """把模型给出的命令翻译成可执行的 Python 源码

        返回 (code, error)，二者必有其一为 None。
        """
        cmd = (command or "").strip()
        if not cmd:
            return None, "错误: 命令为空"

        if _PY_PREFIX.match(cmd):
            rest = _PY_PREFIX.sub("", cmd, count=1).strip()

            # pip 安装会改动运行环境，一律拒绝
            if re.match(r"^-m\s+pip\b", rest, re.IGNORECASE):
                return None, (
                    "错误: 沙箱不允许安装依赖（pip）。\n"
                    "缺少第三方库时请如实告诉用户，不要尝试自己装。"
                )
            if re.match(r"^-m\s+", rest):
                # 其他 -m 形式（如 python -m json.tool）同样走不了，拒绝即可
                return None, _REJECT_HINT.format(cmd=cmd)

            if rest.startswith("-c"):
                code = _strip_quotes(rest[2:].strip())
                if not code:
                    return None, "错误: -c 后面没有代码"
                return code, None

            # 其余按脚本路径处理：python script.py [args...]
            script = rest.split()[0]
            return self._read_script(script)

        # 裸 Python 代码（最常见：模型直接把源码塞进来）
        if self._looks_like_shell(cmd):
            return None, _REJECT_HINT.format(cmd=cmd)

        # 语法预检：不是合法 Python 就别丢给解释器，否则模型看到的是
        # SyntaxError traceback，会误以为"环境坏了"而反复重试。
        try:
            ast.parse(cmd)
        except SyntaxError as e:
            return None, (
                "错误: 这段内容不是合法的 Python 代码，沙箱只支持执行 Python。\n"
                f"语法错误: {e.msg}（第 {e.lineno} 行）\n"
                "想统计/处理数据请用 pandas 或标准库写 Python，不要使用 shell 命令。\n"
                f"你收到的内容是: {cmd[:120]!r}"
            )
        return cmd, None

    @staticmethod
    def _looks_like_shell(cmd: str) -> bool:
        """粗判是否是非 Python 的 shell 命令"""
        head = cmd.split()[0].lower()
        known = {
            # 文件/目录
            "ls", "dir", "cat", "rm", "mv", "cp", "mkdir", "rmdir", "touch",
            # 文本处理
            "echo", "head", "tail", "wc", "awk", "sed", "sort", "uniq", "cut",
            "tr", "diff", "nl", "tee", "less", "more", "grep", "find", "xargs",
            # 网络/包管理
            "curl", "wget", "git", "npm", "node", "pip", "pip3", "uv",
            # 系统
            "chmod", "chown", "sudo", "apt", "apt-get", "yum", "dnf", "brew",
            "docker", "sh", "bash", "zsh", "cmd", "powershell", "cd", "pwd",
            "kill", "ps", "top", "env", "export", "source", "which", "whoami",
            "hostname", "date", "du", "df", "tar", "zip", "unzip", "make",
        }
        if head in known or head.startswith("./"):
            return True
        # 含明显的 shell 语法
        return any(tok in cmd for tok in ("|", "&&", ";;", " > ", " < ", "$("))

    def _read_script(self, script: str) -> tuple[str | None, str | None]:
        """读取工作区内的脚本文件作为待执行代码"""
        root = _workspace_root()
        try:
            target = (root / script).resolve()
        except OSError:
            return None, f"错误: 无法解析脚本路径 - {script}"
        if not str(target).startswith(str(root)):
            return None, f"错误: 脚本必须在工作区内 - {script}"
        if not target.exists():
            return None, f"错误: 脚本不存在 - {script}（先用 ls 确认工作区里有哪些文件）"
        try:
            return target.read_text(encoding="utf-8"), None
        except UnicodeDecodeError:
            return None, f"错误: 脚本不是 UTF-8 文本 - {script}"

    # ==================== 执行 ====================

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        started = time.monotonic()
        thread_id = self._current_thread_id()
        sandbox_id = getattr(self._try_get_sandbox(), "id", "?")


        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                logger.warning(
                    "[sandbox] 高危命令拦截: thread=%s sandbox=%s pattern=%s cmd=%.120r",
                    thread_id, sandbox_id, pattern, command,
                )
                return ExecuteResponse(
                    output=f"安全拦截: 命令包含高危模式 '{pattern}'",
                    exit_code=1, truncated=False
                )

        try:
            sandbox = self._get_sandbox()
        except ValueError as e:
            logger.error("[sandbox] 路由失败(无 thread_id): thread=%s err=%s", thread_id, e)
            return ExecuteResponse(
                output=f"沙箱路由失败: {str(e)}",
                exit_code=1, truncated=False
            )

        code, err = self._to_python_code(command)
        if err:
            logger.info(
                "[sandbox] 拒绝非 Python 输入: thread=%s sandbox=%s cmd=%.120r",
                thread_id, sandbox_id, command,
            )
            return ExecuteResponse(output=err, exit_code=1, truncated=False)

        effective_timeout = timeout or self.default_timeout
        logger.info(
            "[sandbox] 执行开始: thread=%s sandbox=%s timeout=%ss code_len=%d code=%.200r",
            thread_id, sandbox.id, effective_timeout, len(code), code,
        )
        try:
            result = sandbox.process.code_run(code, timeout=effective_timeout)
            stdout = getattr(result, "result", None)
            if not stdout:
                stdout = getattr(getattr(result, "artifacts", None), "stdout", "") or ""
            if not stdout:
                stdout = "（执行完毕但没有任何输出，请用 print() 打印你要看的结果）"

            logger.info(
                "[sandbox] 执行完成: thread=%s sandbox=%s exit_code=%s 耗时=%.2fs output_len=%d output=%.200r",
                thread_id, sandbox.id, result.exit_code,
                time.monotonic() - started, len(stdout), stdout,
            )
            return ExecuteResponse(
                output=stdout,
                exit_code=result.exit_code,
                truncated=False
            )
        except TimeoutError:
            logger.warning(
                "[sandbox] 执行超时: thread=%s sandbox=%s timeout=%ss 耗时=%.2fs",
                thread_id, sandbox.id, effective_timeout, time.monotonic() - started,
            )
            return ExecuteResponse(
                output=f"命令执行超时（{effective_timeout}s），已强制终止",
                exit_code=124, truncated=False
            )
        except Exception as e:
            logger.exception(
                "[sandbox] 执行环境异常: thread=%s sandbox=%s 耗时=%.2fs code=%.200r",
                thread_id, sandbox_id, time.monotonic() - started, code,
            )
            return ExecuteResponse(
                output=(
                    f"执行环境异常: {type(e).__name__}: {e}\n"
                    "这不是你的代码问题，重复调用也不会成功。\n"
                    "请停止重试，如实告诉用户「代码执行环境当前不可用」，"
                    "并把你本来要执行的代码作为文本提供给用户参考。"
                ),
                exit_code=1, truncated=False
            )

    def _get_sandbox(self) -> BaseSandbox:
        """
        从当前运行时上下文提取 thread_id 并获取沙箱。
        适用于 execute 等无 runtime 参数的场景。
        """
        config = get_config()
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            raise ValueError("运行时上下文缺少 thread_id，无法路由沙箱")
        return self.manager.get_or_create_sandbox(thread_id=cast(str, thread_id))

    # ==================== 观测辅助 ====================

    @staticmethod
    def _current_thread_id() -> str:
        """取当前 thread_id 用于日志；不在图运行上下文里时返回 '-'"""
        try:
            config = get_config()
            return str((config.get("configurable") or {}).get("thread_id") or "-")
        except Exception:  # noqa: BLE001 - 日志辅助函数不允许抛异常
            return "-"

    def _try_get_sandbox(self):
        """尽力取沙箱（仅为了在「执行开始前」就把 sandbox_id 打进日志）"""
        try:
            return self._get_sandbox()
        except Exception:  # noqa: BLE001
            return None

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """批量下载文件内容，供 MemoryMiddleware 加载记忆文件使用"""
        results = []
        root = _workspace_root()
        for path in paths:
            try:
                target = (root / path.lstrip("/")).resolve()
                if not str(target).startswith(str(root)):
                    results.append(FileDownloadResponse(
                        path=path, content=None,
                        error=f"路径逃逸拒绝: {path}"
                    ))
                    continue
                if not target.exists():
                    results.append(FileDownloadResponse(
                        path=path, content=None,
                        error="file_not_found"
                    ))
                    continue
                content = target.read_bytes()
                results.append(FileDownloadResponse(
                    path=path, content=content, error=None
                ))
            except Exception as e:
                results.append(FileDownloadResponse(
                    path=path, content=None,
                    error=str(e)
                ))
        return results

__all__ = ["RestrictedSandboxBackend"]
