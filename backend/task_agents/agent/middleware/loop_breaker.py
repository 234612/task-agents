"""工具失败熔断中间件（防止小模型空转烧 token）

背景：recursion_limit 是「兜底闸门」，但它只在**步数**用完时才生效。
真正的烧钱场景是：某个工具因为环境问题（沙箱属性写错、网络、权限）**每次**
都失败，小模型看不懂，就换个写法再调一次——同一个错重试几十次，几十万 token
就这么没了。步数闸门管不住，因为每次重试都是合法的一步。

所以再加一层「熔断器」：同一个工具、同一组参数连续失败 N 次就直接拉闸，
在**下一次模型调用之前**抛异常终止整轮运行（不是返回错误消息让它继续试）。

为什么在 model call 阶段抛而不是在 tool call 阶段抛：
- tool call 阶段抛的异常会被 ToolNode 的 handle_tool_errors 捕获成一条
  ToolMessage，等于又喂了一条错误给模型，模型会再试一次；
- model call 阶段抛异常时，这一轮还没有产生任何 LLM 调用，直接终止，
  零额外 token。chat_service 会把异常转成 error 事件返回给前端。

使用：ChatService 在每轮请求开始调用 `reset(session_id)` 清掉该会话的计数，
否则一次熔断会把整个会话永久卡死。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.config import get_config

logger = logging.getLogger(__name__)

# 判定「工具失败」的显式标记。刻意收窄：脚本自己 print 出「错误」不算失败，
# 只有后端/框架明确返回的失败信号才算，避免误熔断正常任务。
_ERROR_MARKERS = (
    "执行环境异常",
    "安全拦截",
    "沙箱路由失败",
    "沙箱只支持执行",
    "沙箱不可用",
    "Error invoking tool",
    "Error:",
    "Traceback (most recent call last)",
)


class ToolLoopBreaker(RuntimeError):
    """熔断信号：向上抛出以终止整轮 Agent 运行"""


def _tool_call_name(request: Any) -> str:
    tc = getattr(request, "tool_call", None) or {}
    return str(tc.get("name") or "unknown")


def _args_fingerprint(request: Any) -> str:
    """对工具入参做指纹：参数完全一样才算「重复失败」"""
    tc = getattr(request, "tool_call", None) or {}
    try:
        raw = json.dumps(tc.get("args") or {}, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 - 指纹算不出来就退化为整串
        raw = str(tc.get("args") or "")
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _thread_id(request: Any) -> str:
    """取当前运行的 thread_id（= session_id）

    - 工具调用：ToolRuntime 直接带 config；
    - 模型调用：langgraph 的 Runtime 不带 config，退回上下文变量 get_config()。
      拿不到时返回 "-"，此时熔断仍按「全局」统计，只是粒度变粗。
    """
    runtime = getattr(request, "runtime", None)
    config = getattr(runtime, "config", None) or getattr(request, "config", None)
    if not config:
        try:
            config = get_config()
        except Exception:  # noqa: BLE001 - 不在图运行上下文里
            config = {}
    return str((config.get("configurable") or {}).get("thread_id") or "-")


def _is_failure(result: Any) -> bool:
    """判断一次工具调用是否失败"""
    if isinstance(result, ToolMessage):
        if getattr(result, "status", None) == "error":
            return True
        content = result.content
        text = content if isinstance(content, str) else str(content)
        return any(marker in text[:300] for marker in _ERROR_MARKERS)
    if isinstance(result, str):
        return any(marker in result[:300] for marker in _ERROR_MARKERS)
    return False


class ToolFailureCircuitBreaker(AgentMiddleware):
    """同一工具调用连续失败 N 次即熔断

    Args:
        threshold: 允许的连续失败次数（达到即拉闸）
    """

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        self._failures: dict[tuple[str, str, str], int] = {}
        self._tripped: dict[str, str] = {}
        self._lock = threading.Lock()

    # ==================== 对外：每轮请求开始时重置 ====================

    def reset(self, thread_id: str) -> None:
        """清空某会话的失败计数与熔断状态（新一轮对话调用）"""
        tid = str(thread_id)
        with self._lock:
            for key in [k for k in self._failures if k[0] == tid]:
                del self._failures[key]
            self._tripped.pop(tid, None)

    # ==================== 核心：计数 / 拉闸 ====================

    def _record(self, request: Any, result: Any) -> None:
        tid = _thread_id(request)
        key = (tid, _tool_call_name(request), _args_fingerprint(request))

        with self._lock:
            if not _is_failure(result):
                # 成功一次就把这个「工具+参数」的计数清零，只认连续失败
                self._failures.pop(key, None)
                return
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            if count >= self.threshold:
                reason = (
                    f"工具 {key[1]} 连续失败 {count} 次，已熔断。"
                    f"这通常是执行环境/工具本身的问题，不是任务本身的问题，"
                    f"继续重试也不会成功。"
                )
                self._tripped[tid] = reason
                self._failures.clear()
                logger.error("触发工具熔断: thread_id=%s 原因=%s", tid, reason)

    def _check_tripped(self, request: Any) -> None:
        reason = self._tripped.get(_thread_id(request))
        if reason:
            raise ToolLoopBreaker(reason)

    # ==================== 中间件钩子 ====================

    def wrap_tool_call(self, request, handler):  # noqa: ANN001, ANN201
        result = handler(request)
        try:
            self._record(request, result)
        except Exception:
            logger.exception("记录工具失败状态异常")
        return result

    async def awrap_tool_call(self, request, handler):  # noqa: ANN001, ANN201
        result = await handler(request)
        try:
            self._record(request, result)
        except Exception:
            logger.exception("记录工具失败状态异常")
        return result

    def wrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        self._check_tripped(request)
        return handler(request)

    async def awrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        self._check_tripped(request)
        return await handler(request)

tool_failure_breaker = ToolFailureCircuitBreaker()

__all__ = [
    "ToolFailureCircuitBreaker",
    "ToolLoopBreaker",
    "tool_failure_breaker",
]
