"""MySQL 会话元数据仓储

只负责 chat_sessions 表的读写，不含业务编排。所有方法接收外部传入的
AsyncSession，由 Service 层控制事务边界（便于跨仓储操作时统一提交）。
"""
import logging
from typing import Optional

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from task_agents.core.timeutils import utcnow
from task_agents.database.models import ChatSession, SessionStatus

logger = logging.getLogger(__name__)


class MySQLSessionRepository:
    """会话元数据仓储"""

    def __init__(self, db: AsyncSession):
        self._db = db

    # ==================== 查询 ====================

    async def list_by_user(
        self,
        user_id: str,
        page: int,
        page_size: int,
        status: SessionStatus = SessionStatus.ACTIVE,
    ) -> tuple[list[ChatSession], int]:
        """按用户分页查询会话列表，按最后活跃时间倒序

        返回 (当前页记录, 总条数)。

        排序走 idx_chat_sessions_user_updated 联合索引：
        WHERE user_id = ? AND status = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?
        """
        if page < 1:
            raise ValueError("page 必须 >= 1")
        if page_size < 1:
            raise ValueError("page_size 必须 >= 1")

        conditions = [
            ChatSession.user_id == user_id,
            ChatSession.status == status,
        ]

        total = await self._count(conditions)

        stmt: Select = (
            select(ChatSession)
            .where(*conditions)
            .order_by(ChatSession.updated_at.desc(), ChatSession.session_id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        result = await self._db.execute(stmt)
        rows = list(result.scalars().all())

        logger.debug(
            "查询会话列表: user_id=%s page=%s page_size=%s 命中=%s 总数=%s",
            user_id, page, page_size, len(rows), total,
        )
        return rows, total

    async def get_by_id(self, session_id: str) -> Optional[ChatSession]:
        """按主键查询单个会话"""
        stmt = select(ChatSession).where(ChatSession.session_id == session_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_for_user(self, session_id: str, user_id: str) -> bool:
        """判断会话是否存在且属于指定用户

        用于权限校验，避免越权读取他人会话的历史消息。
        """
        stmt = (
            select(ChatSession.session_id)
            .where(
                ChatSession.session_id == session_id,
                ChatSession.user_id == user_id,
                ChatSession.status != SessionStatus.DELETED,
            )
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_message_count(self, session_id: str) -> Optional[int]:
        """读取会话当前消息数（标量查询）

        刻意只 SELECT 单列而非 ORM 实体：实体查询会命中 identity map 返回
        内存中的旧对象，导致刚 UPDATE 完读到的仍是过期计数。标量查询绕过
        身份映射，始终拿到数据库当前值。
        """
        stmt = select(ChatSession.message_count).where(ChatSession.session_id == session_id)
        result = await self._db.execute(stmt)
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None

    async def _count(self, conditions: list) -> int:
        """统计符合条件的会话总数"""
        stmt = select(func.count()).select_from(ChatSession).where(*conditions)
        result = await self._db.execute(stmt)
        return int(result.scalar_one() or 0)

    # ==================== 写入 ====================

    async def create(self, session: ChatSession) -> ChatSession:
        """插入新会话记录

        不在内部提交事务，由调用方决定提交时机。
        """
        self._db.add(session)
        await self._db.flush()
        await self._db.refresh(session)
        logger.info("创建会话记录: session_id=%s user_id=%s", session.session_id, session.user_id)
        return session

    async def touch(
        self,
        session_id: str,
        message_delta: int = 0,
    ) -> int:
        """原子更新最后活跃时间与消息计数

        用单条 UPDATE ... SET message_count = message_count + ? 交给数据库做
        自增，避免「读出来 +1 再写回」在并发下丢失更新。

        synchronize_session=False：这是 Core 风格的批量 UPDATE，表达式
        message_count + delta 无法在 Python 侧求值，让 SQLAlchemy 尝试同步
        身份映射会报错；后续用 get_message_count 标量查询读取最新值。

        返回受影响行数（0 表示会话不存在）。
        """
        stmt = (
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(
                message_count=ChatSession.message_count + message_delta,
                updated_at=utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._db.execute(stmt)
        affected = result.rowcount or 0
        logger.debug(
            "更新会话活跃状态: session_id=%s delta=%s affected=%s",
            session_id, message_delta, affected,
        )
        return affected

    async def update_title(self, session_id: str, title: str) -> Optional[ChatSession]:
        """更新会话标题（LLM 生成标题后回写）"""
        stmt = (
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(title=title, updated_at=utcnow())
        )
        result = await self._db.execute(stmt)
        if not result.rowcount:
            return None
        return await self.get_by_id(session_id)

    async def update_status(
        self,
        session_id: str,
        status: SessionStatus,
    ) -> Optional[ChatSession]:
        """更新会话状态（归档 / 软删除）"""
        stmt = (
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(status=status, updated_at=utcnow())
        )
        result = await self._db.execute(stmt)
        if not result.rowcount:
            return None
        return await self.get_by_id(session_id)

    # ==================== 事务控制 ====================
    # 显式暴露提交/回滚，避免 Service 层跨层访问私有 _db 属性。

    async def commit(self) -> None:
        """提交当前事务"""
        await self._db.commit()

    async def rollback(self) -> None:
        """回滚当前事务"""
        await self._db.rollback()
