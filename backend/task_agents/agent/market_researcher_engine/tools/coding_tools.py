"""程序员子 Agent 的工具集

设计前提：LLM 生成的文件路径与代码都属于**不可信输入**，本模块对每一项
操作施加硬性边界，而不是信任模型会自觉守规矩。

安全边界：
1. 路径隔离：所有文件操作都限制在工作区目录内，`resolve()` 后校验前缀，
   可拦截 `../../etc/passwd`、绝对路径逃逸与符号链接穿越。
2. 子进程执行：代码在独立进程中运行，带超时强制终止，主服务不会被
   死循环或阻塞调用挂住。
3. 环境隔离：子进程只继承白名单环境变量，LLM_API_KEY、数据库口令等
   凭据不会泄露给被执行的代码。
4. 输出限长：所有工具返回都截断到配置上限，防止海量输出撑爆上下文。

已知局限（诚实说明）：这是**进程级约束，不是容器级沙箱**。被执行的代码
仍拥有运行后端的那个操作系统用户的权限，能发起网络请求、读取工作区外的
文件。若要用于不可信的多租户生产环境，必须再套一层容器/gVisor 隔离。
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

from langchain_core.tools import tool

from task_agents.core.config import get_settings

logger = logging.getLogger(__name__)

# 子进程环境变量白名单：只给运行 Python 所必需的最小集合。
# 刻意不包含 LLM_API_KEY / MYSQL_PASSWORD / REDIS_PASSWORD 等任何凭据。
_ENV_ALLOWLIST = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "LANG", "LC_ALL", "PATHEXT")

# 文本类扩展名：只有这些才允许按文本读写，避免误碰二进制文件
_TEXT_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt", ".csv",
    ".yaml", ".yml", ".toml", ".html", ".css", ".sql", ".sh", ".env.example",
}


# ==================== 内部工具函数 ====================


def _workspace_root() -> Path:
    """获取工作区根目录，不存在则创建"""
    root = get_settings().coder_workspace_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_safe(relative_path: str) -> tuple[Path | None, str | None]:
    """把用户给的路径安全地解析到工作区内

    Returns:
        (目标路径, None) 表示合法；(None, 错误信息) 表示被拒绝。

    防护要点：
    - 先 resolve() 消除 `..` 与符号链接，再校验是否仍在工作区内。
      顺序不能反：若先判断字符串前缀，`workspace/../secret` 会绕过检查。
    - 拒绝空路径与指向工作区根目录本身的写操作。
    """
    if not relative_path or not relative_path.strip():
        return None, "路径不能为空"

    root = _workspace_root()
    candidate = (root / relative_path.strip()).resolve()

    # is_relative_to 是 Python 3.9+ 提供的语义化判断，比字符串 startswith 可靠
    if not candidate.is_relative_to(root):
        logger.warning("路径越界被拒绝: %r -> %s", relative_path, candidate)
        return None, (
            f"路径越界：所有文件操作必须限制在工作区 {root} 内，"
            f"拒绝访问 {candidate}"
        )

    return candidate, None


def _truncate(text: str) -> str:
    """按配置上限截断输出，并明确告知已截断（不静默丢数据）"""
    limit = get_settings().CODER_MAX_OUTPUT_CHARS
    if len(text) <= limit:
        return text
    return (
        text[:limit]
        + f"\n\n... [输出已截断，共 {len(text)} 字符，仅显示前 {limit} 字符]"
    )


def _safe_env() -> dict[str, str]:
    """构建子进程环境：只透传白名单变量，剥离所有凭据"""
    env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}
    # Python 需要这些才能正常启动与定位解释器
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _suffix_allowed(path: Path) -> bool:
    """判断扩展名是否属于允许按文本处理的类型"""
    return path.suffix.lower() in _TEXT_SUFFIXES


# ==================== 工具定义 ====================


@tool
def list_workspace_files(subdirectory: str = "") -> str:
    """列出工作区中的文件，用于了解当前有哪些代码文件。

    在读写文件之前先用它确认目标是否存在、路径该怎么写。

    参数:
        subdirectory: 相对工作区的子目录，留空表示列出工作区根目录。

    返回:
        文件清单（含相对路径与大小），或错误说明。
    """
    target, error = _resolve_safe(subdirectory or ".")
    if error:
        return f"错误: {error}"
    if target is None:
        return "错误: 无法解析目录"

    if not target.exists():
        return f"目录不存在: {subdirectory or '(工作区根目录)'}"
    if not target.is_dir():
        return f"不是目录: {subdirectory}"

    root = _workspace_root()
    lines: list[str] = []
    try:
        for path in sorted(target.rglob("*")):
            # 跳过缓存目录，噪音大且无意义
            if any(part in {"__pycache__", ".git", "node_modules", ".venv"} for part in path.parts):
                continue
            relative = path.relative_to(root).as_posix()
            if path.is_dir():
                lines.append(f"[目录] {relative}/")
            else:
                lines.append(f"{relative}  ({path.stat().st_size} bytes)")
    except OSError as e:
        return f"错误: 读取目录失败 - {e}"

    if not lines:
        return "工作区为空，还没有任何文件。"
    return _truncate(f"工作区: {root}\n\n" + "\n".join(lines[:500]))


@tool
def read_code_file(file_path: str) -> str:
    """读取工作区内某个代码文件的内容。

    修改文件前应先读取，了解现有实现再动手。

    参数:
        file_path: 相对工作区的文件路径，如 "solution.py" 或 "src/main.py"。

    返回:
        文件文本内容（带行号，便于定位），或错误说明。
    """
    target, error = _resolve_safe(file_path)
    if error:
        return f"错误: {error}"
    if target is None:
        return "错误: 无法解析路径"

    if not target.exists():
        return f"错误: 文件不存在 - {file_path}"
    if target.is_dir():
        return f"错误: 这是一个目录，请用 list_workspace_files 查看 - {file_path}"
    if not _suffix_allowed(target):
        return (
            f"错误: 不支持读取 {target.suffix or '(无扩展名)'} 类型的文件，"
            f"仅支持文本类文件: {', '.join(sorted(_TEXT_SUFFIXES))}"
        )

    max_bytes = get_settings().CODER_MAX_FILE_BYTES
    size = target.stat().st_size
    if size > max_bytes:
        return f"错误: 文件过大（{size} bytes），超过上限 {max_bytes} bytes"

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"错误: 文件不是 UTF-8 文本，无法读取 - {file_path}"
    except OSError as e:
        return f"错误: 读取失败 - {e}"

    # 带行号返回，让模型在后续讨论中能精确定位
    numbered = "\n".join(f"{i:4d} | {line}" for i, line in enumerate(content.splitlines(), 1))
    return _truncate(f"文件: {file_path} ({size} bytes)\n{'-' * 40}\n{numbered}")


@tool
def write_code_file(file_path: str, content: str) -> str:
    """在工作区内创建或覆盖一个代码文件。

    覆盖已有文件前请确认确实要替换；若只是想局部修改，先 read_code_file
    看清内容再写回完整版本。

    参数:
        file_path: 相对工作区的路径，父目录不存在会自动创建。
        content: 要写入的完整文件内容。

    返回:
        写入结果说明，或错误说明。
    """
    target, error = _resolve_safe(file_path)
    if error:
        return f"错误: {error}"
    if target is None:
        return "错误: 无法解析路径"

    root = _workspace_root()
    if target == root:
        return "错误: 不能把工作区根目录当作文件写入"
    if target.is_dir():
        return f"错误: 目标是一个已存在的目录 - {file_path}"
    if not _suffix_allowed(target):
        return (
            f"错误: 不支持写入 {target.suffix or '(无扩展名)'} 类型的文件，"
            f"仅支持文本类文件: {', '.join(sorted(_TEXT_SUFFIXES))}"
        )

    encoded = content.encode("utf-8")
    max_bytes = get_settings().CODER_MAX_FILE_BYTES
    if len(encoded) > max_bytes:
        return f"错误: 内容过大（{len(encoded)} bytes），超过上限 {max_bytes} bytes"

    existed = target.exists()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
    except OSError as e:
        return f"错误: 写入失败 - {e}"

    action = "已覆盖" if existed else "已创建"
    logger.info("程序员工具写入文件: %s (%s, %d bytes)", target, action, len(encoded))
    return f"{action}文件: {file_path} ({len(encoded)} bytes)"


@tool
def run_python_code(code: str) -> str:
    """在工作区内执行一段 Python 代码，用于验证实现是否真的能跑通。

    写完代码后应当用它实际运行一次，而不是仅凭推断宣称代码正确。
    若代码需要读取文件，请使用相对路径（执行的当前目录就是工作区）。

    安全约束：
        - 执行有超时限制，超时会被强制终止
        - 代码运行在独立子进程中，无法访问本服务的密钥与数据库凭据
        - 输出超长会被截断

    参数:
        code: 要执行的 Python 源代码。

    返回:
        标准输出、标准错误与退出码；异常退出时一并给出诊断信息。
    """
    if not code or not code.strip():
        return "错误: 代码不能为空"

    root = _workspace_root()
    timeout = get_settings().CODER_EXEC_TIMEOUT_SECONDS

    try:
        # 用 -c 直接传源码，避免在磁盘上留下临时脚本文件
        completed = subprocess.run(
            [sys.executable, "-I", "-c", code],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_safe_env(),
        )
    except subprocess.TimeoutExpired as e:
        partial = (e.stdout or "") if isinstance(e.stdout, str) else ""
        logger.warning("代码执行超时（%ss）被终止", timeout)
        return (
            f"错误: 执行超时（超过 {timeout} 秒），进程已被强制终止。\n"
            f"请检查是否存在死循环、无限递归或阻塞式等待输入。\n"
            f"超时前的部分输出:\n{_truncate(partial)}"
        )
    except OSError as e:
        return f"错误: 无法启动 Python 解释器 - {e}"

    stdout = _truncate(completed.stdout or "")
    stderr = _truncate(completed.stderr or "")

    parts = [f"退出码: {completed.returncode}"]
    parts.append(f"--- stdout ---\n{stdout or '(空)'}")
    if stderr:
        parts.append(f"--- stderr ---\n{stderr}")
    if completed.returncode != 0:
        parts.append("提示: 非零退出码表示执行失败，请依据 stderr 修正代码后重试。")

    return "\n\n".join(parts)


# --- 工具集合导出 ---
# 子 Agent 直接导入这个列表
CODING_TOOLS = [
    list_workspace_files,
    read_code_file,
    write_code_file,
    run_python_code,
]
