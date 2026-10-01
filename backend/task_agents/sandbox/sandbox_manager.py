"""沙箱管理器：按 thread_id 路由、复用 Daytona 沙箱，并守住容量上限

分层职责（重要）：

- **本模块（同步、线程安全）**：缓存 + **容量闸门** + 创建/删除/列举。
  容量上限必须放在这里，而不是只放在异步池里——因为 `RestrictedSandboxBackend`
  的文件与执行方法是**同步**的，它们直接调 `get_or_create_sandbox`，会绕开
  异步池。闸门放在 manager 才能做到"不管从哪条路进来都被管住"。
- **sandbox_registry.SandboxPool（异步）**：生命周期编排——空闲 TTL 标记、
  回收器、池满排队等待、进程退出全量清理、孤儿回收。

容量超限时不静默等待，而是抛 `SandboxCapacityError`：调用方（backend 的 execute）
据此明确告诉模型"执行环境繁忙"，而不是让它无限等或反复重试烧 token。
"""
import logging
import threading
import time
from typing import Dict, List, Optional

from daytona import Daytona, CreateSandboxFromSnapshotParams, CodeLanguage

from task_agents.core.config import get_settings

logger = logging.getLogger(__name__)

# 用于孤儿回收的识别标签。只有带这个标签的沙箱才会被本服务回收，
# 避免误删同一 Daytona 组织下其它应用的沙箱。
_APP_LABEL = "task-agents"


class SandboxCapacityError(RuntimeError):
    """沙箱容量超限（全局上限或单用户上限）"""


class SandboxManager:
    """沙箱管理器：按 thread_id 路由，复用沙箱实例"""

    def __init__(self, daytona: Daytona):
        self.daytona = daytona
        # 内存缓存：thread_id -> sandbox_id
        self._cache: Dict[str, str] = {}
        # thread_id -> user_id，用于单用户配额统计
        self._owners: Dict[str, str] = {}
        self._lock = threading.Lock()

    # ==================== 容量统计 ====================

    def active_count(self) -> int:
        """当前存活（已缓存）的沙箱数"""
        with self._lock:
            return len(self._cache)

    def count_for_user(self, user_id: str) -> int:
        """某个 user_id 当前持有的沙箱数"""
        uid = str(user_id or "-")
        with self._lock:
            return sum(1 for owner in self._owners.values() if owner == uid)

    def stats(self) -> dict:
        """供健康检查/排障用的一览"""
        settings = get_settings()
        with self._lock:
            return {
                "active": len(self._cache),
                "max_concurrent": settings.SANDBOX_MAX_CONCURRENT,
                "max_per_user": settings.SANDBOX_MAX_PER_USER,
                "idle_ttl_seconds": settings.SANDBOX_IDLE_TTL_SECONDS,
            }

    # ==================== 获取 / 创建 ====================

    def get_or_create_sandbox(
        self,
        thread_id: str,
        snapshot: Optional[str] = None,
        language: Optional[CodeLanguage] = CodeLanguage.PYTHON,
        user_id: Optional[str] = None,
    ):
        """根据 thread_id 获取或创建沙箱

        Args:
            thread_id:  会话线程 ID，用于路由和缓存
            snapshot:   指定快照名；不传则按语言走默认快照（走预热池，≈90ms 启动）
            language:   语言标识（如 "python"），仅在 snapshot 为空时生效
            user_id:    归属用户，用于单用户配额统计；不传按 "-" 计

        Raises:
            SandboxCapacityError: 超出全局或单用户上限
        """
        settings = get_settings()
        uid = str(user_id or "-")

        with self._lock:
            # 1. 先查缓存
            if thread_id in self._cache:
                sandbox_id = self._cache[thread_id]
                try:
                    sandbox = self.daytona.get(sandbox_id)
                    if sandbox:
                        logger.debug(
                            "[sandbox] 缓存命中: thread=%s sandbox=%s",
                            thread_id, sandbox_id,
                        )
                        self._owners[thread_id] = uid
                        return sandbox
                except Exception:
                    pass  # 沙箱可能已被自动停止/删除
                del self._cache[thread_id]
                self._owners.pop(thread_id, None)

            # 2. 容量闸门（新增沙箱前判定，复用缓存不算新占额度）
            self._check_capacity(settings, thread_id, uid)

            # 3. 缓存未命中，创建新沙箱
            params = CreateSandboxFromSnapshotParams(
                snapshot=snapshot,
                language=language,
                labels={"thread_id": thread_id, "app": _APP_LABEL},
                auto_stop_interval=settings.SANDBOX_AUTO_STOP_MINUTES,
            )
            sandbox = self.daytona.create(params)
            self._cache[thread_id] = sandbox.id
            self._owners[thread_id] = uid
            logger.info(
                "[sandbox] 新建沙箱: thread=%s sandbox=%s language=%s snapshot=%s 活跃=%s",
                thread_id, sandbox.id, language, snapshot or "<default>", len(self._cache),
            )
            return sandbox

    def _check_capacity(self, settings, thread_id: str, uid: str) -> None:
        """容量闸门：全局上限 + 单用户上限

        在持锁状态下调用。0 表示不限制（保持历史行为）。
        """
        limit = settings.SANDBOX_MAX_CONCURRENT
        if limit > 0 and len(self._cache) >= limit:
            logger.warning(
                "[sandbox] 全局沙箱上限已满，拒绝创建: thread=%s 活跃=%s 上限=%s",
                thread_id, len(self._cache), limit,
            )
            raise SandboxCapacityError(
                f"执行环境繁忙：同时存活的沙箱已达上限 {limit}。"
                "这不是代码或任务本身的问题，等待或重试都不会让它变好。"
            )

        per_user = settings.SANDBOX_MAX_PER_USER
        if per_user > 0:
            owned = sum(1 for owner in self._owners.values() if owner == uid)
            if owned >= per_user:
                logger.warning(
                    "[sandbox] 单用户沙箱上限已满，拒绝创建: thread=%s user=%s 持有=%s 上限=%s",
                    thread_id, uid, owned, per_user,
                )
                raise SandboxCapacityError(
                    f"执行环境繁忙：该用户同时持有的沙箱已达上限 {per_user}。"
                    "这不是代码或任务本身的问题，等待或重试都不会让它变好。"
                )

    # ==================== 删除 ====================

    def delete_sandbox(self, thread_id: str) -> bool:
        """会话结束时主动清理沙箱

        Returns:
            是否真的执行了删除（缓存里没有则 False）
        """
        with self._lock:
            if thread_id not in self._cache:
                return False
            sandbox_id = self._cache[thread_id]
            try:
                sandbox = self.daytona.get(sandbox_id)
                if sandbox:
                    self.daytona.delete(sandbox)  # 传 Sandbox 实例，不是传 id
                    logger.info("[sandbox] 销毁沙箱: thread=%s sandbox=%s", thread_id, sandbox_id)
            except Exception:
                logger.warning("[sandbox] 销毁沙箱失败(忽略): thread=%s sandbox=%s", thread_id, sandbox_id)
            finally:
                self._cache.pop(thread_id, None)
                self._owners.pop(thread_id, None)
        return True

    def delete_all(self) -> int:
        """全量清理（进程退出 / 优雅关闭时调用）

        Returns:
            清理的沙箱数
        """
        with self._lock:
            thread_ids = list(self._cache.keys())
        count = 0
        for tid in thread_ids:
            try:
                if self.delete_sandbox(tid):
                    count += 1
            except Exception:  # noqa: BLE001 - 关闭阶段不允许因个别失败中断
                logger.exception("[sandbox] 关闭清理异常: thread=%s", tid)
        logger.info("[sandbox] 全量清理完成: 共 %s 个", count)
        return count

    # ==================== 孤儿回收 ====================

    def list_orphans(self, max_idle_seconds: int) -> List[str]:
        """列出疑似残留的沙箱 id

        判定：带 app=task-agents 标签 **且** 不在本进程缓存里 **且**
        last_activity_at 早于 max_idle_seconds。

        ⚠️ 多实例部署时不要调用：其它实例的在用沙箱同样不在本进程缓存里，
        会被误判为孤儿。所以默认由 SANDBOX_RECLAIM_ORPHANS=0 关闭。
        """
        orphans: List[str] = []
        now = time.time()
        try:
            for sandbox in self.daytona.list():
                labels = getattr(sandbox, "labels", None) or {}
                if labels.get("app") != _APP_LABEL:
                    continue
                with self._lock:
                    known = sandbox.id in self._cache.values()
                if known:
                    continue
                if not self._is_idle_enough(sandbox, now, max_idle_seconds):
                    continue
                orphans.append(sandbox.id)
        except Exception:  # noqa: BLE001 - 列举失败不该阻断启动
            logger.exception("[sandbox] 列举沙箱失败，跳过孤儿回收")
        return orphans

    @staticmethod
    def _is_idle_enough(sandbox, now: float, max_idle_seconds: int) -> bool:
        """按 last_activity_at 判定是否够"老"；拿不到时间时保守地不算孤儿"""
        raw = getattr(sandbox, "last_activity_at", None) or getattr(
            sandbox, "updated_at", None
        )
        if not raw:
            return False
        try:
            if isinstance(raw, (int, float)):
                ts = float(raw)
                if ts > 1e12:  # 毫秒时间戳
                    ts /= 1000.0
            else:
                from datetime import datetime

                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except Exception:  # noqa: BLE001 - 时间格式不可预期
            return False
        return (now - ts) >= max_idle_seconds

    def delete_by_id(self, sandbox_id: str) -> bool:
        """按 sandbox_id 删除（孤儿回收用）"""
        try:
            sandbox = self.daytona.get(sandbox_id)
            if sandbox:
                self.daytona.delete(sandbox)
                logger.info("[sandbox] 回收孤儿沙箱: sandbox=%s", sandbox_id)
                return True
        except Exception:  # noqa: BLE001
            logger.warning("[sandbox] 回收孤儿沙箱失败(忽略): sandbox=%s", sandbox_id)
        return False


__all__ = ["SandboxManager", "SandboxCapacityError"]
