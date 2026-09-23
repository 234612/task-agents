import logging
import threading
from typing import Dict, Optional

from daytona import Daytona, CreateSandboxFromSnapshotParams, CodeLanguage

logger = logging.getLogger(__name__)


class SandboxManager:
    """沙箱管理器：按 thread_id 路由，复用沙箱实例"""

    def __init__(self, daytona: Daytona):
        self.daytona = daytona
        # 内存缓存：thread_id -> sandbox_id
        self._cache: Dict[str, str] = {}
        self._lock = threading.Lock()

    def get_or_create_sandbox(
        self,
        thread_id: str,
        snapshot: Optional[str] = None,
        language: Optional[CodeLanguage] = CodeLanguage.PYTHON,  # 2. 修改类型并给默认值
    ):
        """根据 thread_id 获取或创建沙箱

        Args:
            thread_id:  会话线程 ID，用于路由和缓存
            snapshot:   指定快照名；不传则按语言走默认快照（走预热池，≈90ms 启动）
            language:   语言标识（如 "python"），仅在 snapshot 为空时生效
        """
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
                        return sandbox
                except Exception:
                    pass  # 沙箱可能已被自动停止/删除
                del self._cache[thread_id]

            # 2. 缓存未命中，创建新沙箱
            params = CreateSandboxFromSnapshotParams(
                snapshot=snapshot,
                language=language,
                labels={"thread_id": thread_id},
                auto_stop_interval=0,
            )
            sandbox = self.daytona.create(params)
            self._cache[thread_id] = sandbox.id
            logger.info(
                "[sandbox] 新建沙箱: thread=%s sandbox=%s language=%s snapshot=%s",
                thread_id, sandbox.id, language, snapshot or "<default>",
            )
            return sandbox

    def delete_sandbox(self, thread_id: str):
        """会话结束时主动清理沙箱"""
        with self._lock:
            if thread_id not in self._cache:
                return
            sandbox_id = self._cache[thread_id]
            try:
                sandbox = self.daytona.get(sandbox_id)
                if sandbox:
                    self.daytona.delete(sandbox)  # 传 Sandbox 实例，不是传 id
                    logger.info("[sandbox] 销毁沙箱: thread=%s sandbox=%s", thread_id, sandbox_id)
            except Exception:
                logger.warning("[sandbox] 销毁沙箱失败(忽略): thread=%s sandbox=%s", thread_id, sandbox_id)
            finally:
                del self._cache[thread_id]