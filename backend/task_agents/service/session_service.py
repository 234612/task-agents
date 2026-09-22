"""会话服务层 — 会话生命周期与查询编排

职责边界：
- 编排 MySQL / MongoDB / Redis 三个仓储完成会话的创建与查询
- 处理权限校验（会话归属）、分页参数收敛
- 不含任何 HTTP 概念（Request / HTTPException），由 Router 层做转换

会话创建的写入顺序与补偿：
1. MySQL 建元数据（权威记录，失败则整体失败）
2. MongoDB 建文档骨架（失败则回滚 MySQL，避免产生读不到历史的孤儿会话）
3. Redis 初始化上下文（失败仅降级，不阻断创建）
"""
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from task_agents.core.config import Settings
from task_agents.core.timeutils import utcnow
from task_agents.database.models import ChatSession, SessionStatus
from task_agents.repository.mongo_message_repository import MongoMessageRepository
from task_agents.repository.mysql_session_repository import MySQLSessionRepository
from task_agents.repository.redis_context_repository import RedisContextRepository
from task_agents.schemas.mongo import SessionDocument, StoredMessage

logger = logging.getLogger(__name__)

# 新会话在 LLM 生成标题前的占位标题
DEFAULT_TITLE = "新会话"


class SessionNotFoundError(Exception):
    """会话不存在"""


class SessionAccessDeniedError(Exception):
    """会话不属于当前用户"""


@dataclass
class SessionPage:
    """分页结果"""
    items: list[ChatSession]
    page: int
    page_size: int
    total: int

    @property
    def total_pages(self) -> int:
        """总页数（至少为 1，便于前端渲染分页器）"""
        if self.page_size <= 0:
            return 1
        return max(1, (self.total + self.page_size - 1) // self.page_size)


@dataclass
class SessionService:
    """会话服务"""

    session_repo: MySQLSessionRepository
    mongo_repo: MongoMessageRepository
    redis_repo: RedisContextRepository
    settings: Settings

    # ==================== 参数收敛 ====================

    def _normalize_page(self, page: int) -> int:
        """页码下限收敛为 1"""
        return page if page >= 1 else 1

    def _normalize_page_size(self, page_size: int) -> int:
        """每页条数收敛到 [1, SESSION_PAGE_SIZE_MAX]

        上限来自配置而非硬编码，防止前端传入超大 page_size 拖垮查询。
        """
        upper = self.settings.SESSION_PAGE_SIZE_MAX
        if page_size < 1:
            return 1
        return min(page_size, upper)

    # ==================== 查询 ====================

    async def list_sessions(
        self,
        user_id: str,
        page: int = 1,
        page_size: Optional[int] = None,
    ) -> SessionPage:
        """分页查询用户会话列表，按最后活跃时间倒序"""
        page = self._normalize_page(page)
        size = self._normalize_page_size(
            page_size if page_size is not None else self.settings.SESSION_PAGE_SIZE
        )

        rows, total = await self.session_repo.list_by_user(
            user_id=user_id,
            page=page,
            page_size=size,
        )
        logger.info(
            "查询会话列表: user_id=%s page=%s page_size=%s 返回=%s 总数=%s",
            user_id, page, size, len(rows), total,
        )
        return SessionPage(items=rows, page=page, page_size=size, total=total)

    async def get_session_for_user(self, session_id: str, user_id: str) -> ChatSession:
        """获取会话并校验归属

        Raises:
            SessionNotFoundError: 会话不存在或已删除
            SessionAccessDeniedError: 会话属于其他用户
        """
        session = await self.session_repo.get_by_id(session_id)
        if session is None or session.status == SessionStatus.DELETED:
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        if session.user_id != user_id:
            # 不泄露会话是否真实存在，只报无权限
            logger.warning(
                "越权访问会话被拒绝: session_id=%s 请求用户=%s 实际归属=%s",
                session_id, user_id, session.user_id,
            )
            raise SessionAccessDeniedError(f"无权访问该会话: {session_id}")
        return session

    async def load_history(
        self,
        session_id: str,
        user_id: str,
        skip: int = 0,
        limit: int = 0,
    ) -> list[StoredMessage]:
        """加载会话的完整历史消息（从 MongoDB 读取）

        先校验归属，再取文档。limit <= 0 表示返回全部消息。
        """
        await self.get_session_for_user(session_id, user_id)

        if skip < 0:
            raise ValueError("skip 不能为负数")
        if limit < 0:
            raise ValueError("limit 不能为负数")

        messages = await self.mongo_repo.list_messages(session_id, skip=skip, limit=limit)
        logger.info(
            "加载会话历史: session_id=%s skip=%s limit=%s 返回=%s",
            session_id, skip, limit, len(messages),
        )
        return messages

    # ==================== 创建 ====================

    async def create_session(
        self,
        user_id: str,
        agent_key: str,
        title: Optional[str] = None,
    ) -> ChatSession:
        """创建新会话：初始化 MySQL 记录、MongoDB 文档与 Redis 上下文"""
        session_id = str(uuid.uuid4())
        now = utcnow()
        final_title = (title or "").strip() or DEFAULT_TITLE

        session = ChatSession(
            session_id=session_id,
            user_id=user_id,
            title=final_title,
            agent_key=agent_key,
            status=SessionStatus.ACTIVE,
            message_count=0,
            created_at=now,
            updated_at=now,
        )

        # —— 1. MySQL：权威元数据 ——
        await self.session_repo.create(session)
        await self.session_repo.commit()

        # —— 2. MongoDB：文档骨架。失败则回滚 MySQL，避免孤儿会话 ——
        document = SessionDocument(
            id=session_id,
            user_id=user_id,
            agent_key=agent_key,
            messages=[],
            created_at=now,
            updated_at=now,
        )
        try:
            await self.mongo_repo.create_document(document)
        except Exception:
            logger.exception("MongoDB 文档创建失败，回滚 MySQL 会话记录: session_id=%s", session_id)
            await self.session_repo.rollback()
            raise

        # —— 3. Redis：上下文初始化。失败仅降级 ——
        try:
            await self.redis_repo.init_context(session_id, user_id=user_id, agent_key=agent_key)
        except Exception as e:
            logger.warning("Redis 上下文初始化失败，会话仍可用: session_id=%s error=%s", session_id, e)

        logger.info("会话创建完成: session_id=%s user_id=%s agent_key=%s", session_id, user_id, agent_key)
        return session

    # ==================== 更新与删除 ====================

    async def update_title(self, session_id: str, user_id: str, title: str) -> ChatSession:
        """更新会话标题（LLM 生成标题后回写）"""
        await self.get_session_for_user(session_id, user_id)

        cleaned = title.strip()
        if not cleaned:
            raise ValueError("标题不能为空")

        session = await self.session_repo.update_title(session_id, cleaned)
        if session is None:
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        await self.session_repo.commit()
        return session

    async def archive_session(self, session_id: str, user_id: str) -> ChatSession:
        """归档会话（不再出现在默认列表，数据保留）"""
        await self.get_session_for_user(session_id, user_id)

        session = await self.session_repo.update_status(session_id, SessionStatus.ARCHIVED)
        if session is None:
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        await self.session_repo.commit()

        await self._drop_context(session_id, reason="会话已归档")
        return session

    async def delete_session(self, session_id: str, user_id: str) -> None:
        """软删除会话并清理 Redis 上下文

        MySQL 侧标记 status=deleted 而非物理删行，保留审计与恢复能力；
        MongoDB 历史文档默认保留（数据保留策略可另行配置定期清理）。
        Redis 上下文立即释放，避免无用内存占用。
        """
        await self.get_session_for_user(session_id, user_id)

        session = await self.session_repo.update_status(session_id, SessionStatus.DELETED)
        if session is None:
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        await self.session_repo.commit()

        await self._drop_context(session_id, reason="会话已删除")
        logger.info("会话已软删除: session_id=%s user_id=%s", session_id, user_id)

    async def _drop_context(self, session_id: str, reason: str) -> None:
        """释放 Redis 上下文，失败仅记日志"""
        try:
            await self.redis_repo.delete_context(session_id)
        except Exception as e:
            logger.warning("%s，但 Redis 上下文清理失败: session_id=%s error=%s", reason, session_id, e)


__all__ = [
    "DEFAULT_TITLE",
    "SessionAccessDeniedError",
    "SessionNotFoundError",
    "SessionPage",
    "SessionService",
]
