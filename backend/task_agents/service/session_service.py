"""会话服务层 — 会话生命周期与查询编排

职责边界：
- 编排 MySQL / MongoDB / Redis 三个仓储完成会话的创建与查询
- 处理权限校验（会话归属）、分页参数收敛
- 不含任何 HTTP 概念（Request / HTTPException），由 Router 层做转换

会话隔离：session_id 为 UUID v4（36 字符）。首次对话前端不传
session_id，由后端自动创建会话（ensure_session）；续聊传入时校验归属。

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
from task_agents.database.models import ChatSession, SessionStatusValue
from task_agents.repository.mongo_message_repository import MongoMessageRepository
from task_agents.repository.mysql_session_repository import MySQLSessionRepository
from task_agents.repository.redis_context_repository import RedisContextRepository
from task_agents.core.titleutils import DEFAULT_TITLE, extract_title
from task_agents.schemas.mongo import SessionDocument, StoredMessage

logger = logging.getLogger(__name__)


class SessionNotFoundError(Exception):
    """会话不存在"""


class SessionAccessDeniedError(Exception):
    """会话不属于当前用户"""


class SessionEndedError(Exception):
    """会话已结束，不允许再追加消息"""


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
        status: Optional[int] = None,
    ) -> SessionPage:
        """分页查询用户会话列表，按最后活跃时间倒序

        status=None 返回全部状态；传 1/2 精确过滤。
        """
        page = self._normalize_page(page)
        size = self._normalize_page_size(
            page_size if page_size is not None else self.settings.SESSION_PAGE_SIZE
        )

        rows, total = await self.session_repo.list_by_user(
            user_id=user_id,
            page=page,
            page_size=size,
            status=status,
        )
        logger.info(
            "查询会话列表: user_id=%s page=%s page_size=%s status=%s 返回=%s 总数=%s",
            user_id, page, size, status, len(rows), total,
        )
        return SessionPage(items=rows, page=page, page_size=size, total=total)

    async def get_session_for_user(self, session_id: str, user_id: str) -> ChatSession:
        """获取会话并校验归属

        Raises:
            SessionNotFoundError: 会话不存在
            SessionAccessDeniedError: 会话属于其他用户
        """
        session = await self.session_repo.get_by_id(session_id)
        if session is None:
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
        session_id: Optional[str] = None,
        first_message: Optional[str] = None,
    ) -> ChatSession:
        """创建新会话：初始化 MySQL 记录、MongoDB 文档与 Redis 上下文

        Args:
            session_id: 显式指定会话 ID；为空自动生成 UUID v4
            first_message: 触发创建的首条用户消息，用于按「首句前 50 字」
                           生成标题；无则先用占位标题，首轮写入时回填
        """
        final_session_id = session_id or str(uuid.uuid4())
        now = utcnow()

        # 标题优先级：显式传入 > 首句提取 > 占位标题
        final_title = (title or "").strip()
        if not final_title and first_message:
            final_title = extract_title(
                first_message, self.settings.SESSION_TITLE_MAX_CHARS
            )
        final_title = final_title or DEFAULT_TITLE

        session = ChatSession(
            session_id=final_session_id,
            user_id=user_id,
            title=final_title,
            agent_key=agent_key,
            status=SessionStatusValue.ACTIVE,
            message_count=0,
            created_at=now,
            updated_at=now,
        )

        # —— 1. MySQL：权威元数据 ——
        await self.session_repo.create(session)
        await self.session_repo.commit()

        # —— 2. MongoDB：文档骨架。失败则回滚 MySQL，避免孤儿会话 ——
        document = SessionDocument(
            id=final_session_id,
            user_id=user_id,
            agent_key=agent_key,
            messages=[],
            created_at=now,
            updated_at=now,
        )
        try:
            await self.mongo_repo.create_document(document)
        except Exception:
            logger.exception("MongoDB 文档创建失败，回滚 MySQL 会话记录: session_id=%s", final_session_id)
            await self.session_repo.rollback()
            raise

        # —— 3. Redis：上下文初始化。失败仅降级 ——
        try:
            await self.redis_repo.init_context(final_session_id, user_id=user_id, agent_key=agent_key)
        except Exception as e:
            logger.warning("Redis 上下文初始化失败，会话仍可用: session_id=%s error=%s", final_session_id, e)

        logger.info("会话创建完成: session_id=%s user_id=%s agent_key=%s title=%s",
                    final_session_id, user_id, agent_key, final_title)
        return session

    async def ensure_session(
        self,
        user_id: str,
        agent_key: str,
        session_id: Optional[str],
        first_message: str,
    ) -> ChatSession:
        """会话隔离入口：校验已有会话，或为首次对话自动创建

        - session_id 为空 → 自动生成 UUID v4 并创建三存储记录
        - session_id 非空 → 校验存在与归属；已结束的会话拒绝追加消息

        自动创建时直接用首条消息生成标题（首句前 50 字），
        省去「先建占位标题再回填」的一次写放大。

        Returns:
            ChatSession 记录（含最终 session_id，供响应回传前端）
        """
        if not session_id:
            return await self.create_session(
                user_id=user_id,
                agent_key=agent_key,
                first_message=first_message,
            )

        session = await self.get_session_for_user(session_id, user_id)
        if int(session.status) == SessionStatusValue.ENDED:
            raise SessionEndedError(f"会话已结束，无法继续对话: {session_id}")
        return session

    # ==================== 更新 ====================

    async def update_title(self, session_id: str, user_id: str, title: str) -> ChatSession:
        """更新会话标题（含占位标题回填）"""
        await self.get_session_for_user(session_id, user_id)

        cleaned = extract_title(title, self.settings.SESSION_TITLE_MAX_CHARS)
        if not cleaned:
            raise ValueError("标题不能为空")

        session = await self.session_repo.update_title(session_id, cleaned)
        if session is None:
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        await self.session_repo.commit()
        return session

    async def end_session(self, session_id: str, user_id: str) -> ChatSession:
        """结束会话（status: 1 → 2）

        业务语义：用户主动结束，会话转为只读——历史仍可查看，
        但 ensure_session 会拒绝继续追加消息。
        同时释放 Redis 短期记忆（会话不再活跃，上下文无用），
        MongoDB 长期记忆保留。
        """
        await self.get_session_for_user(session_id, user_id)

        session = await self.session_repo.update_status(session_id, SessionStatusValue.ENDED)
        if session is None:
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        await self.session_repo.commit()

        try:
            await self.redis_repo.delete_context(session_id)
        except Exception as e:
            logger.warning("会话已结束但 Redis 上下文清理失败: session_id=%s error=%s", session_id, e)

        logger.info("会话已结束: session_id=%s user_id=%s", session_id, user_id)
        return session

    async def archive_session(self, session_id: str, user_id: str) -> ChatSession:
        """归档会话：映射到 status=2（已结束）

        当前状态模型只有 1-活跃 / 2-已结束两态，归档即「结束会话」：
        不再追加消息、释放 Redis 短期记忆，历史仍可查看。
        """
        return await self.end_session(session_id, user_id)

    async def delete_session(self, session_id: str, user_id: str) -> None:
        """删除会话：三存储一并清理

        顺序：MySQL 权威元数据 → MongoDB 历史文档 → Redis 上下文。
        后两者属于派生数据，清理失败只记日志不阻断——删除请求已经
        生效，留着孤儿文档反而会在 MongoDB 里越积越多。
        """
        await self.get_session_for_user(session_id, user_id)

        await self.session_repo.delete(session_id)
        await self.session_repo.commit()

        try:
            await self.mongo_repo.delete_document(session_id)
        except Exception as e:  # noqa: BLE001 — 派生存储失败不影响删除结果
            logger.warning("删除 MongoDB 历史文档失败: session_id=%s error=%s", session_id, e)

        try:
            await self.redis_repo.delete_context(session_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("删除 Redis 上下文失败: session_id=%s error=%s", session_id, e)

        logger.info("会话已删除: session_id=%s user_id=%s", session_id, user_id)

    # ==================== 短期记忆（Redis） ====================

    async def load_context(self, session_id: str) -> list[StoredMessage]:
        """读取当前会话的短期记忆（滑动窗口内最近 N 条消息）

        Redis 属于可失效的加速层：Key 过期（TTL 1 小时）或故障时返回
        空列表，调用方（聊天服务）据此回源 MongoDB 补齐上下文，
        再写回 Redis 续期。
        """
        try:
            return await self.redis_repo.get_context(session_id)
        except Exception as e:
            logger.warning("读取 Redis 上下文失败，将回源 MongoDB: session_id=%s error=%s", session_id, e)
            return []


__all__ = [
    "DEFAULT_TITLE",
    "SessionAccessDeniedError",
    "SessionEndedError",
    "SessionNotFoundError",
    "SessionPage",
    "SessionService",
]
