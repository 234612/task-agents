"""沙箱池：按 thread_id 分配、复用与回收 Daytona 沙箱（进程级单例）

职责（异步生命周期编排）：
- 池满时按 `SANDBOX_ACQUIRE_TIMEOUT_SECONDS` **排队等待**，超时则降级；
- `SANDBOX_IDLE_TTL_SECONDS > 0` 时启用会话级复用：release 只标记空闲，
  由后台回收器到期真正销毁（跨轮文件得以保留）；
- 进程退出时全量清理；可选择在启动时回收孤儿沙箱。

**容量闸门不在这里**，而在 `SandboxManager`（同步层）——因为 backend 的文件与
执行方法是同步的，会绕开本池直接调 manager。这里只负责"等到有额度为止"。

约定：所有治理项 `0 = 关闭/保持现状`，加了配置不改变默认行为。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from task_agents.core.config import get_settings
from task_agents.sandbox.sandbox_manager import (
    SandboxCapacityError,
    SandboxManager,
)

logger = logging.getLogger(__name__)

# 池满排队时的轮询间隔（秒）
_ACQUIRE_POLL_INTERVAL = 0.5


class SandboxUnavailable(RuntimeError):
    """沙箱在超时窗口内始终拿不到额度（调用方应降级而非重试）"""


@dataclass
class _Entry:
    """一个会话持有的沙箱记录"""

    thread_id: str
    user_id: str
    created_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)
    # 非 None 表示已 release 但还在空闲 TTL 窗口内，等待回收器销毁
    idle_since: Optional[float] = None


class SandboxPool:
    """按 thread_id（= session_id）分配与回收 Daytona 沙箱的池"""

    def __init__(self, manager: SandboxManager):
        self._manager = manager
        self._lock = asyncio.Lock()
        self._entries: Dict[str, _Entry] = {}
        self._reaper: Optional[asyncio.Task] = None

    # ==================== 获取 ====================

    async def acquire(self, thread_id: str, user_id: str | None = None) -> None:
        """为该会话准备沙箱（已存在则直接复用）

        池满时的行为由 `SANDBOX_ACQUIRE_TIMEOUT_SECONDS` 决定：
        - 0（默认）：不等待，直接放弃预热。后续真正执行时由 manager 抛
          `SandboxCapacityError`，backend 会把它翻译成"执行环境繁忙"告诉模型。
        - 非 0：最多等待这么久，期间轮询直到有额度释放。

        拿不到沙箱**不抛异常打断对话**——预热失败是可降级的，真正不可用时
        错误会在 execute 处以模型能理解的文案暴露。
        """
        settings = get_settings()
        uid = str(user_id or "-")
        timeout = settings.SANDBOX_ACQUIRE_TIMEOUT_SECONDS
        deadline = time.monotonic() + timeout if timeout > 0 else None

        while True:
            try:
                await asyncio.to_thread(
                    self._manager.get_or_create_sandbox, thread_id, None, None, uid
                )
            except SandboxCapacityError as e:
                if deadline is None or time.monotonic() >= deadline:
                    logger.warning(
                        "[sandbox] 沙箱额度不足，放弃预热: thread=%s user=%s err=%s",
                        thread_id, uid, e,
                    )
                    return
                await asyncio.sleep(_ACQUIRE_POLL_INTERVAL)
                continue
            except Exception as e:  # noqa: BLE001 - 预热失败不该中断对话
                logger.warning(
                    "[sandbox] 沙箱预建失败（将按需重试）: thread=%s err=%s", thread_id, e
                )
                return

            async with self._lock:
                entry = self._entries.get(thread_id)
                now = time.monotonic()
                if entry is None:
                    self._entries[thread_id] = _Entry(
                        thread_id=thread_id, user_id=uid, created_at=now, last_used_at=now
                    )
                else:
                    entry.user_id = uid
                    entry.last_used_at = now
                    entry.idle_since = None  # 空闲中被重新启用
            return

    # ==================== 释放 ====================

    async def release(self, thread_id: str) -> None:
        """释放该会话的沙箱

        - `SANDBOX_IDLE_TTL_SECONDS = 0`（默认）：立即销毁，保持现状；
        - 非 0：只标记空闲，交给回收器到期销毁（会话级复用）。
        """
        settings = get_settings()
        ttl = settings.SANDBOX_IDLE_TTL_SECONDS

        async with self._lock:
            entry = self._entries.get(thread_id)
            if entry is None:
                # 没走 acquire 也要保证沙箱被清理（例如 acquire 失败后仍执行了）
                await asyncio.to_thread(self._manager.delete_sandbox, thread_id)
                return
            if ttl <= 0:
                self._entries.pop(thread_id, None)
            else:
                entry.idle_since = time.monotonic()
                self._ensure_reaper_locked(settings)

        if ttl <= 0:
            try:
                await asyncio.to_thread(self._manager.delete_sandbox, thread_id)
            except Exception as e:  # noqa: BLE001 - 清理失败不影响已产出的回复
                logger.warning("[sandbox] 沙箱销毁失败: thread=%s err=%s", thread_id, e)

    # ==================== 空闲回收器 ====================

    def _ensure_reaper_locked(self, settings) -> None:
        """启动回收器（调用方持锁）。TTL<=0 时不需要。"""
        if self._reaper is not None and not self._reaper.done():
            return
        if settings.SANDBOX_IDLE_TTL_SECONDS <= 0:
            return
        interval = max(5, settings.SANDBOX_REAPER_INTERVAL_SECONDS)
        self._reaper = asyncio.create_task(self._reap_loop(interval))
        logger.info(
            "[sandbox] 空闲回收器已启动: ttl=%ss 扫描间隔=%ss",
            settings.SANDBOX_IDLE_TTL_SECONDS, interval,
        )

    async def _reap_loop(self, interval: int) -> None:
        """定期销毁空闲超时的沙箱"""
        try:
            while True:
                await asyncio.sleep(interval)
                await self.reap_idle()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 回收器自身异常不能打死进程
            logger.exception("[sandbox] 空闲回收器异常退出")

    async def reap_idle(self) -> int:
        """销毁所有空闲超过 TTL 的沙箱，返回销毁数量"""
        settings = get_settings()
        ttl = settings.SANDBOX_IDLE_TTL_SECONDS
        if ttl <= 0:
            return 0

        now = time.monotonic()
        expired: list[tuple[str, float]] = []
        async with self._lock:
            for tid, entry in list(self._entries.items()):
                if entry.idle_since is not None and (now - entry.idle_since) >= ttl:
                    expired.append((tid, now - entry.idle_since))
                    del self._entries[tid]

        if not expired:
            return 0

        count = 0
        for tid, idle_for in expired:
            try:
                await asyncio.to_thread(self._manager.delete_sandbox, tid)
                count += 1
                logger.info("[sandbox] 空闲超时销毁: thread=%s 空闲=%.0fs", tid, idle_for)
            except Exception:  # noqa: BLE001
                logger.warning("[sandbox] 空闲超时销毁失败(忽略): thread=%s", tid)
        return count

    # ==================== 生命周期 ====================

    async def reclaim_orphans(self) -> int:
        """启动时回收上次进程残留的沙箱

        默认关闭（SANDBOX_RECLAIM_ORPHANS=0）：多实例部署时本进程无法区分
        "其它实例的在用沙箱"和"真孤儿"，误删代价太高。单实例部署可开启。
        """
        settings = get_settings()
        if settings.SANDBOX_RECLAIM_ORPHANS == 0:
            return 0

        max_idle = settings.SANDBOX_ORPHAN_MAX_IDLE_SECONDS
        orphans = await asyncio.to_thread(self._manager.list_orphans, max_idle)
        if not orphans:
            return 0

        count = 0
        for sandbox_id in orphans:
            if await asyncio.to_thread(self._manager.delete_by_id, sandbox_id):
                count += 1
        logger.info("[sandbox] 孤儿回收完成: 共 %s 个", count)
        return count

    async def shutdown(self) -> int:
        """进程退出：停回收器 + 全量销毁"""
        if self._reaper is not None and not self._reaper.done():
            self._reaper.cancel()
            try:
                await self._reaper
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._reaper = None

        async with self._lock:
            self._entries.clear()

        count = await asyncio.to_thread(self._manager.delete_all)
        logger.info("[sandbox] 沙箱池已关闭，销毁 %s 个沙箱", count)
        return count

    def stats(self) -> dict:
        """池状态一览（排障 / 健康检查用）"""
        base = self._manager.stats()
        base.update(
            {
                "tracked": len(self._entries),
                "idle": sum(1 for e in self._entries.values() if e.idle_since is not None),
                "reaper_running": self._reaper is not None and not self._reaper.done(),
            }
        )
        return base


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


async def shutdown_pool() -> int:
    """关闭沙箱池（lifespan 关闭阶段调用）。池未初始化时安全返回 0。"""
    global _pool
    if _pool is None:
        return 0
    try:
        return await _pool.shutdown()
    finally:
        _pool = None


def reset_pool() -> None:
    """清空池单例（测试/容器重启用）"""
    global _pool
    _pool = None


__all__ = [
    "SandboxPool",
    "SandboxUnavailable",
    "ensure_pool",
    "reset_pool",
    "shutdown_pool",
]
