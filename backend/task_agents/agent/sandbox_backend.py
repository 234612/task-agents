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
import subprocess
import sys
from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from task_agents.agent.market_researcher_engine.tools.coding_tools import (
    _safe_env,
    _truncate,
    _workspace_root,
)
from task_agents.core.config import get_settings

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


def _strip_quotes(text: str) -> str:
    """去掉包裹在外层的一对引号"""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


class RestrictedSandboxBackend(FilesystemBackend, SandboxBackendProtocol):
    """工作区内的真实文件系统 + 仅 Python 的受限执行"""

    def __init__(
        self,
        root_dir: str | Path | None = None,
        *,
        timeout: int | None = None,
    ) -> None:
        settings = get_settings()
        super().__init__(
            root_dir=root_dir or settings.coder_workspace_dir,
            virtual_mode=True,  # 关键：把 root_dir 当虚拟根，封锁 .. 逃逸
            max_file_size_mb=10,
        )
        self._timeout = timeout or settings.CODER_EXEC_TIMEOUT_SECONDS
        # 目录不存在时补建，避免 Agent 第一次写文件就失败
        Path(self.cwd).mkdir(parents=True, exist_ok=True)

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
        #SyntaxError traceback，会误以为"环境坏了"而反复重试。
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
        """在受限环境中执行 Python（拒绝一切 shell 命令）"""
        code, error = self._to_python_code(command)
        if error:
            logger.info("沙箱拒绝执行: %s", error.splitlines()[0])
            return ExecuteResponse(output=error, exit_code=1, truncated=False)

        effective_timeout = timeout or self._timeout
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", code],
                cwd=str(self.cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                env=_safe_env(),
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as e:
            partial = (e.stdout or "") if isinstance(e.stdout, str) else ""
            return ExecuteResponse(
                output=(
                    f"错误: 执行超时（超过 {effective_timeout} 秒），已终止。\n"
                    f"检查是否存在死循环、无限递归或等待输入。\n"
                    f"超时前输出:\n{_truncate(partial)}"
                ),
                exit_code=124,
                truncated=False,
            )
        except OSError as e:
            return ExecuteResponse(
                output=f"错误: 无法启动 Python 解释器 - {e}",
                exit_code=1,
                truncated=False,
            )

        stdout = _truncate(completed.stdout or "")
        stderr = _truncate(completed.stderr or "")
        parts = [f"退出码: {completed.returncode}", f"--- stdout ---\n{stdout or '(空)'}"]
        if stderr:
            parts.append(f"--- stderr ---\n{stderr}")
        if completed.returncode != 0:
            parts.append("提示: 非零退出码表示执行失败，请依据 stderr 修正代码后重试。")

        output = "\n\n".join(parts)
        truncated = len(output) > get_settings().CODER_MAX_OUTPUT_CHARS
        return ExecuteResponse(output=output, exit_code=completed.returncode, truncated=truncated)


__all__ = ["RestrictedSandboxBackend"]
