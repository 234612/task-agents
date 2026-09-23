from __future__ import annotations

import asyncio
import logging
from typing import Optional

from task_agents.sandbox.sandbox_manager import SandboxManager

logger = logging.getLogger(__name__)

"""
异步事件对沙箱进行管理
"""
class SandboxPool:
    """按 thread_id（= session_id）分配与回收 Daytona 沙箱的池"""

    def __init__(self, manager: SandboxManager):
        self._manager = manager
        self._lock = asyncio.Lock()

    async def acquire(self, thread_id: str) -> None:
        """为该会话准备沙箱（已存在则直接复用）"""
        try:
            async with self._lock:
                await asyncio.to_thread(
                    self._manager.get_or_create_sandbox, thread_id
                )
        except Exception as e:  # noqa: BLE001 - 预热失败不该中断对话
            logger.warning("沙箱预建失败（将按需重试）: thread_id=%s err=%s", thread_id, e)

    async def release(self, thread_id: str) -> None:
        """销毁该会话的沙箱"""
        try:
            async with self._lock:
                await asyncio.to_thread(self._manager.delete_sandbox, thread_id)
        except Exception as e:  # noqa: BLE001 - 清理失败不影响已产出的回复
            logger.warning("沙箱销毁失败: thread_id=%s err=%s", thread_id, e)


_pool: Optional[SandboxPool] = None


async def ensure_pool() -> SandboxPool:
    """获取进程级唯一的沙箱池（首次调用时惰性构造）"""
    global _pool
    if _pool is None:
        # 延迟导入：ServiceContainer 在 import 期就实例化，直接顶层 import
        # 会把容器拉起来，破坏 lifespan 的初始化顺序。
        from task_agents.core.services import service_container as container

        manager = container.get_client("sandbox_manager")
        _pool = SandboxPool(manager)
        logger.info("沙箱池已初始化: manager=%s", type(manager).__name__)
    return _pool


def reset_pool() -> None:
    """清空池单例（测试/容器重启用）"""
    global _pool
    _pool = None


__all__ = ["SandboxPool", "ensure_pool", "reset_pool"]
