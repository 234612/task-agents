"""受限沙箱 Backend（deepagents）

背景：deepagents 给每个 Agent 内置了一套文件/执行工具
（`ls` / `read_file` / `write_file` / `edit_file` / `glob` / `grep` / `execute`），
它们全部作用于创建 Agent 时传入的 `backend` 参数。

历史问题：
1. 曾用 `StateBackend()`——**没有 `execute`**，模型一调就报
   `Error invoking tool 'execute'`，于是陷入「换个写法再调一次」的死循环，
   一条请求能空转上千步。
2. 文件系统是**内存虚拟**的——`write_file` 返回成功，磁盘上什么都没有，
   "跑通验证"全是假的。
3. 改成 `SandboxBackendProtocol` 之后文件操作又变成了 `NotImplementedError`：
   文件工具根本不可用，而 `execute` 跑在远端云沙箱——写的文件和执行的环境
   还是两个世界。

现在的实现：**文件与执行同环境，全部走 Daytona 云沙箱**
- 文件：`sandbox.fs`（list_files / download_file / upload_file / search_files ...）
- 执行：`sandbox.process.code_run`，只接受 Python

同时直接用官方的 `LocalShellBackend` 是不行的：它 `shell=True` 无隔离，
Agent 能读 `.env` 里的密钥、能执行任意命令——对 Web 服务不可接受。

路径语义（重要）：
模型给的路径一律按**沙箱工作目录的相对路径**处理，前导 "/" 会被剥掉。
这样 `write_file("/a.py")` 与 `execute` 里 `open("a.py")` 指向同一个文件——
否则就会出现"写进去了但执行时找不到"的老问题。
"""

from __future__ import annotations

import ast
import logging
import posixpath
import re
import threading
import time
from typing import cast

from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileData,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from langgraph.config import get_config

from task_agents.agent.middleware.observability import run_observer
from task_agents.core.config import get_settings
from task_agents.sandbox.sandbox_manager import SandboxCapacityError, SandboxManager

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

# Daytona 默认工作目录；拿不到真实值时回落到这里
_FALLBACK_SANDBOX_ROOT = "/home/daytona"

# grep 最多扫多少个文件、多少行：防止一次 grep 把沙箱里的文件全下载一遍
_GREP_MAX_FILES = 50
_GREP_MAX_LINES = 5000


def _strip_quotes(text: str) -> str:
    """去掉包裹在外层的一对引号"""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


class RestrictedSandboxBackend(SandboxBackendProtocol):
    """云沙箱内的真实文件系统 + 仅 Python 的受限执行"""

    def __init__(self, manager: SandboxManager,
                 timeout: int | None = None):
        super().__init__()
        self.manager = manager
        self.default_timeout = timeout or get_settings().CODER_EXEC_TIMEOUT_SECONDS
        # sandbox_id -> 工作目录（get_work_dir 是一次网络调用，按沙箱缓存）
        self._roots: dict[str, str] = {}
        self._roots_lock = threading.Lock()

    @property
    def id(self) -> str:
        return "restricted-daytona-python"

    # ==================== 路径 ====================

    def _sandbox_root(self, sandbox) -> str:
        """取沙箱工作目录（带缓存与回落）"""
        key = getattr(sandbox, "id", "") or ""
        with self._roots_lock:
            cached = self._roots.get(key)
        if cached:
            return cached
        root = _FALLBACK_SANDBOX_ROOT
        try:
            work_dir = sandbox.get_work_dir()
            if work_dir:
                root = str(work_dir).rstrip("/") or root
        except Exception:  # noqa: BLE001 - 拿不到就用默认值，不该让工具挂掉
            logger.debug("[sandbox] 获取工作目录失败，回落 %s", root)
        with self._roots_lock:
            self._roots[key] = root
        return root

    @staticmethod
    def _resolve(root: str, path: str) -> tuple[str, None] | tuple[None, str]:
        """把模型给的路径解析成沙箱内的绝对路径

        返回 (resolved, None) 或 (None, error_message)。

        前导 "/" 会被剥掉，路径一律挂在工作目录下：这样 write 与 execute
        看到的是同一棵树。同时拒绝 `..` 逃逸。
        """
        raw = (path or "").strip()
        if not raw:
            return root, None
        # 统一成正斜杠，剥掉前导斜杠后拼到工作目录
        cleaned = raw.replace("\\", "/").lstrip("/")
        target = posixpath.normpath(posixpath.join(root, cleaned))
        # 逃逸检查：归一化后必须仍在工作目录内
        if target != root and not target.startswith(root + "/"):
            return None, f"路径逃逸拒绝: {path}"
        return target, None

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
                return None, _REJECT_HINT.format(cmd=cmd)

            if rest.startswith("-c"):
                code = _strip_quotes(rest[2:].strip())
                if not code:
                    return None, "错误: -c 后面没有代码"
                return code, None

            # 其余按脚本路径处理：python script.py [args...]
            return self._read_script_source(cmd, rest.split()[0])

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

    def _read_script_source(self, command: str, script: str) -> tuple[str | None, str | None]:
        """从**沙箱内**读取脚本内容（脚本必须已经在工作区里）"""
        try:
            sandbox = self._get_sandbox()
        except Exception as e:  # noqa: BLE001
            return None, f"错误: 无法读取脚本 - {e}"

        root = self._sandbox_root(sandbox)
        target, err = self._resolve(root, script)
        if err:
            return None, f"错误: {err}"
        try:
            data = sandbox.fs.download_file(cast(str, target))
        except Exception:  # noqa: BLE001
            return None, f"错误: 脚本不存在或不可读 - {script}（先用 ls 确认工作区里有哪些文件）"
        if not data:
            return None, f"错误: 脚本不存在或为空 - {script}"
        try:
            return data.decode("utf-8"), None
        except UnicodeDecodeError:
            return None, f"错误: 脚本不是 UTF-8 文本 - {script}"

    @staticmethod
    def _looks_like_shell(cmd: str) -> bool:
        """粗判是否是非 Python 的 shell 命令"""
        head = cmd.split()[0].lower()
        known = {
            "ls", "dir", "cat", "rm", "mv", "cp", "mkdir", "rmdir", "touch",
            "echo", "head", "tail", "wc", "awk", "sed", "sort", "uniq", "cut",
            "tr", "diff", "nl", "tee", "less", "more", "grep", "find", "xargs",
            "curl", "wget", "git", "npm", "node", "pip", "pip3", "uv",
            "chmod", "chown", "sudo", "apt", "apt-get", "yum", "dnf", "brew",
            "docker", "sh", "bash", "zsh", "cmd", "powershell", "cd", "pwd",
            "kill", "ps", "top", "env", "export", "source", "which", "whoami",
            "hostname", "date", "du", "df", "tar", "zip", "unzip", "make",
        }
        if head in known or head.startswith("./"):
            return True
        return any(tok in cmd for tok in ("|", "&&", ";;", " > ", " < ", "$("))

    # ==================== 执行 ====================

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        started = time.monotonic()
        thread_id = self._current_thread_id()

        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                logger.warning(
                    "[sandbox] 高危命令拦截: thread=%s pattern=%s cmd=%.120r",
                    thread_id, pattern, command,
                )
                return ExecuteResponse(
                    output=f"安全拦截: 命令包含高危模式 '{pattern}'",
                    exit_code=1, truncated=False
                )

        try:
            sandbox = self._get_sandbox()
        except SandboxCapacityError as e:
            # 容量闸门的降级文案：必须让模型明白"重试也没用"，否则会空转到熔断
            logger.warning("[sandbox] 沙箱额度不足: thread=%s err=%s", thread_id, e)
            return ExecuteResponse(
                output=(
                    f"执行环境繁忙: {e}\n"
                    "这不是你的代码问题，等待或重试都不会让它变好。\n"
                    "请停止重试，如实告诉用户「执行环境当前繁忙，请稍后再试」。"
                ),
                exit_code=1, truncated=False
            )
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
                thread_id, sandbox.id, command,
            )
            return ExecuteResponse(output=err, exit_code=1, truncated=False)

        effective_timeout = timeout or self.default_timeout
        logger.info(
            "[sandbox] 执行开始: thread=%s sandbox=%s timeout=%ss code_len=%d code=%.200r",
            thread_id, sandbox.id, effective_timeout, len(code), code,
        )
        ok = False
        try:
            result = sandbox.process.code_run(code, timeout=effective_timeout)
            stdout = getattr(result, "result", None)
            if not stdout:
                stdout = getattr(getattr(result, "artifacts", None), "stdout", "") or ""
            if not stdout:
                stdout = "（执行完毕但没有任何输出，请用 print() 打印你要看的结果）"

            ok = result.exit_code == 0
            elapsed_ms = (time.monotonic() - started) * 1000.0
            logger.info(
                "[sandbox] 执行完成: thread=%s sandbox=%s exit_code=%s 耗时=%.2fs output_len=%d output=%.200r",
                thread_id, sandbox.id, result.exit_code,
                time.monotonic() - started, len(stdout), stdout,
            )
            # 观测：把沙箱耗时汇总进当前 run（中间件拿不到 sandbox uuid）
            run_observer.record_sandbox(
                thread_id, elapsed_ms, ok=ok, sandbox_id=str(sandbox.id)
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
                thread_id, sandbox.id, time.monotonic() - started, code,
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

    def _get_sandbox(self):
        """
        从当前运行时上下文提取 thread_id 并获取沙箱。
        适用于 execute 等无 runtime 参数的场景。

        Raises:
            SandboxCapacityError: 超出全局/单用户沙箱上限（由 manager 抛出）
            ValueError: 运行时上下文缺少 thread_id
        """
        config = get_config()
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            raise ValueError("运行时上下文缺少 thread_id，无法路由沙箱")
        return self.manager.get_or_create_sandbox(thread_id=cast(str, thread_id))

    # ==================== 文件操作（全部走云沙箱） ====================

    def ls(self, path: str) -> LsResult:
        try:
            sandbox = self._get_sandbox()
        except Exception as e:  # noqa: BLE001
            return LsResult(error=f"沙箱不可用: {e}", entries=None)

        root = self._sandbox_root(sandbox)
        target, err = self._resolve(root, path or "/")
        if err:
            return LsResult(error=err, entries=None)
        try:
            infos = sandbox.fs.list_files(cast(str, target))
        except Exception as e:  # noqa: BLE001
            # 目录不存在是最常见的情况，给一句模型看得懂的提示
            return LsResult(error=f"无法列目录 {path}: {type(e).__name__}: {e}", entries=None)

        entries = [
            FileInfo(
                path=_rel(root, getattr(i, "path", "") or ""),
                is_dir=bool(getattr(i, "is_dir", False)),
                size=int(getattr(i, "size", 0) or 0),
                modified_at=str(
                    getattr(i, "modified_at", None) or getattr(i, "mod_time", "") or ""
                ),
            )
            for i in infos or []
        ]
        return LsResult(error=None, entries=entries)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            sandbox = self._get_sandbox()
        except Exception as e:  # noqa: BLE001
            return ReadResult(error=f"沙箱不可用: {e}", file_data=None)

        root = self._sandbox_root(sandbox)
        target, err = self._resolve(root, file_path)
        if err:
            return ReadResult(error=err, file_data=None)
        try:
            data = sandbox.fs.download_file(cast(str, target))
        except Exception as e:  # noqa: BLE001
            return ReadResult(error=f"读取失败 {file_path}: {type(e).__name__}: {e}", file_data=None)
        if not data:
            return ReadResult(error="file_not_found", file_data=None)

        max_bytes = get_settings().CODER_MAX_FILE_BYTES
        if len(data) > max_bytes:
            return ReadResult(
                error=f"文件过大（{len(data)} 字节，上限 {max_bytes}）", file_data=None
            )

        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        start = max(0, int(offset or 0))
        line_limit = 2000 if limit is None else int(limit)
        end = total if line_limit <= 0 else min(total, start + line_limit)
        content = "\n".join(lines[start:end])

        return ReadResult(
            error=None,
            file_data=FileData(content=content, encoding="utf-8", created_at="", modified_at=""),
            total_lines=total,
            start_line=start + 1,
            end_line=end,
            next_offset=end if end < total else None,
            no_lines_requested=line_limit <= 0,
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            sandbox = self._get_sandbox()
        except Exception as e:  # noqa: BLE001
            return WriteResult(error=f"沙箱不可用: {e}", path=None)

        root = self._sandbox_root(sandbox)
        target, err = self._resolve(root, file_path)
        if err:
            return WriteResult(error=err, path=None)
        try:
            self._ensure_parent(sandbox, cast(str, target))
            sandbox.fs.upload_file(content.encode("utf-8"), cast(str, target))
        except Exception as e:  # noqa: BLE001
            return WriteResult(
                error=f"写入失败 {file_path}: {type(e).__name__}: {e}", path=None
            )
        logger.info("[sandbox] 写文件: thread=%s path=%s bytes=%d",
                    self._current_thread_id(), target, len(content))
        return WriteResult(error=None, path=file_path)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        try:
            sandbox = self._get_sandbox()
        except Exception as e:  # noqa: BLE001
            return EditResult(error=f"沙箱不可用: {e}", path=None)

        root = self._sandbox_root(sandbox)
        target, err = self._resolve(root, file_path)
        if err:
            return EditResult(error=err, path=None)

        try:
            data = sandbox.fs.download_file(cast(str, target))
        except Exception as e:  # noqa: BLE001
            return EditResult(error=f"读取失败 {file_path}: {e}", path=None)
        if not data:
            return EditResult(error="file_not_found", path=None)

        text = data.decode("utf-8", errors="replace")
        occurrences = text.count(old_string)
        if occurrences == 0:
            return EditResult(
                error=f"未在 {file_path} 中找到待替换的内容", path=None, occurrences=0
            )
        updated = (
            text.replace(old_string, new_string)
            if replace_all
            else text.replace(old_string, new_string, 1)
        )
        try:
            sandbox.fs.upload_file(updated.encode("utf-8"), cast(str, target))
        except Exception as e:  # noqa: BLE001
            return EditResult(error=f"写回失败 {file_path}: {e}", path=None)

        applied = occurrences if replace_all else 1
        return EditResult(error=None, path=file_path, occurrences=applied)

    def delete(self, file_path: str) -> DeleteResult:
        try:
            sandbox = self._get_sandbox()
        except Exception as e:  # noqa: BLE001
            return DeleteResult(error=f"沙箱不可用: {e}", path=None)

        root = self._sandbox_root(sandbox)
        target, err = self._resolve(root, file_path)
        if err:
            return DeleteResult(error=err, path=None)
        try:
            sandbox.fs.delete_file(cast(str, target), recursive=False)
        except Exception as e:  # noqa: BLE001
            return DeleteResult(
                error=f"删除失败 {file_path}: {type(e).__name__}: {e}", path=None
            )
        return DeleteResult(error=None, path=file_path)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        try:
            sandbox = self._get_sandbox()
        except Exception as e:  # noqa: BLE001
            return GlobResult(error=f"沙箱不可用: {e}", matches=None)

        root = self._sandbox_root(sandbox)
        target, err = self._resolve(root, path or "/")
        if err:
            return GlobResult(error=err, matches=None)
        try:
            found = sandbox.fs.find_files(cast(str, target), pattern or "*")
        except Exception as e:  # noqa: BLE001
            return GlobResult(
                error=f"glob 失败: {type(e).__name__}: {e}", matches=None
            )

        seen: set[str] = set()
        matches: list[FileInfo] = []
        for item in found or []:
            p = getattr(item, "file", None) or getattr(item, "path", "")
            if not p or p in seen:
                continue
            seen.add(p)
            matches.append(
                FileInfo(path=_rel(root, p), is_dir=False, size=0, modified_at="")
            )
        return GlobResult(error=None, matches=matches)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        try:
            sandbox = self._get_sandbox()
        except Exception as e:  # noqa: BLE001
            return GrepResult(error=f"沙箱不可用: {e}", matches=None)

        root = self._sandbox_root(sandbox)
        target, err = self._resolve(root, path or "/")
        if err:
            return GrepResult(error=err, matches=None)

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return GrepResult(error=f"无效的正则: {e}", matches=None)

        # Daytona 的 search_files 只返回命中的**文件路径**，行号与文本仍需自己读。
        # 先服务端筛一遍文件，再把候选文件拉回来扫行，避免全量下载。
        try:
            candidates = sandbox.fs.search_files(cast(str, target), pattern).files or []
        except Exception as e:  # noqa: BLE001
            logger.warning("[sandbox] search_files 失败，退回全量列举: %s", e)
            candidates = []
            try:
                for info in sandbox.fs.list_files(cast(str, target)) or []:
                    if not getattr(info, "is_dir", False):
                        candidates.append(getattr(info, "path", ""))
            except Exception:  # noqa: BLE001
                return GrepResult(error=f"无法列举目录: {e}", matches=None)

        limit = int(max_count or 100)
        matches: list[GrepMatch] = []
        for file_path in candidates[:_GREP_MAX_FILES]:
            if len(matches) >= limit:
                break
            if glob and not _glob_match(file_path, glob):
                continue
            try:
                data = sandbox.fs.download_file(file_path)
            except Exception:  # noqa: BLE001
                continue
            if not data:
                continue
            for lineno, line in enumerate(
                data.decode("utf-8", errors="replace").splitlines()[:_GREP_MAX_LINES],
                start=1,
            ):
                if regex.search(line):
                    matches.append(
                        GrepMatch(
                            path=_rel(root, file_path),
                            line=lineno,
                            text=line[:500],
                            context_before=[],
                            context_after=[],
                        )
                    )
                    if len(matches) >= limit:
                        break

        return GrepResult(error=None, matches=matches)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        results: list[FileUploadResponse] = []
        try:
            sandbox = self._get_sandbox()
        except Exception as e:  # noqa: BLE001
            return [
                FileUploadResponse(path=p, error=f"沙箱不可用: {e}") for p, _ in files
            ]

        root = self._sandbox_root(sandbox)
        for path, content in files:
            target, err = self._resolve(root, path)
            if err:
                results.append(FileUploadResponse(path=path, error=err))
                continue
            try:
                self._ensure_parent(sandbox, cast(str, target))
                sandbox.fs.upload_file(content, cast(str, target))
                results.append(FileUploadResponse(path=path, error=None))
            except Exception as e:  # noqa: BLE001
                results.append(
                    FileUploadResponse(path=path, error=f"上传失败: {e}")
                )
        return results

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """批量下载文件内容，供 MemoryMiddleware 加载记忆文件使用

        现在走云沙箱：记忆文件同样存在于沙箱工作目录下（如 /memories/...）。
        """
        results: list[FileDownloadResponse] = []
        try:
            sandbox = self._get_sandbox()
        except Exception as e:  # noqa: BLE001
            return [
                FileDownloadResponse(path=p, content=None, error=f"沙箱不可用: {e}")
                for p in paths
            ]

        root = self._sandbox_root(sandbox)
        for path in paths:
            target, err = self._resolve(root, path)
            if err:
                results.append(FileDownloadResponse(path=path, content=None, error=err))
                continue
            try:
                data = sandbox.fs.download_file(cast(str, target))
            except Exception:  # noqa: BLE001
                data = None
            if not data:
                results.append(
                    FileDownloadResponse(path=path, content=None, error="file_not_found")
                )
                continue
            results.append(FileDownloadResponse(path=path, content=data, error=None))
        return results

    # ==================== 内部辅助 ====================

    @staticmethod
    def _ensure_parent(sandbox, target: str) -> None:
        """写文件前确保父目录存在（沙箱里目录不会自动创建）"""
        parent = posixpath.dirname(target)
        if not parent or parent == "/":
            return
        try:
            sandbox.fs.create_folder(parent, "755")
        except Exception:  # noqa: BLE001 - 已存在时会抛，忽略即可
            pass

    # ==================== 观测辅助 ====================

    @staticmethod
    def _current_thread_id() -> str:
        """取当前 thread_id 用于日志；不在图运行上下文里时返回 '-'"""
        try:
            config = get_config()
            return str((config.get("configurable") or {}).get("thread_id") or "-")
        except Exception:  # noqa: BLE001 - 日志辅助函数不允许抛异常
            return "-"


def _rel(root: str, path: str) -> str:
    """把沙箱绝对路径还原成模型视角的相对路径（带前导 /）"""
    path = str(path or "")
    if path.startswith(root):
        path = path[len(root):]
    if not path.startswith("/"):
        path = "/" + path
    return path


def _glob_match(path: str, pattern: str) -> bool:
    """极简 glob 匹配（只支持 * 与 ?），用于 grep 的过滤参数"""
    import fnmatch

    return fnmatch.fnmatch(posixpath.basename(path), pattern)


__all__ = ["RestrictedSandboxBackend"]
