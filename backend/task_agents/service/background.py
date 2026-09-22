"""后台任务 — MongoDB 异步落库与流式路径的三存储写入

时序约束（重要）：
FastAPI 中带 yield 的依赖（如 get_db_session）其清理代码在响应发送后、
后台任务执行前就会运行。因此后台任务**不能**复用请求级的 AsyncSession，
必须自行取客户端 / 会话工厂新建连接。

同理，SSE 流式响应（StreamingResponse）的生成器在执行期间，请求级依赖
的生命周期已不可依赖。故本模块所有函数只接收 session_factory 等裸客户端
与纯数据参数，一律自建连接，保证在请求上下文销毁后仍可安全执行。

标题策略：按业务规则「首句前 50 字」生成，不再调用 LLM。
自动建会话路径在创建时就带上首句标题；显式创建的会话（title 为占位
「新会话」）在首轮消息落库时回填。
"""
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from task_agents.core.config import get_settings
from task_agents.core.titleutils import DEFAULT_TITLE, extract_title
from task_agents.database.models import SessionStatusValue
from task_agents.database.redis_keys import turns_to_messages
from task_agents.repository.mongo_message_repository import MongoMessageRepository
from task_agents.repository.mysql_session_repository import MySQLSessionRepository
from task_agents.repository.redis_context_repository import RedisContextRepository
from task_agents.schemas.mongo import Citation, StoredMessage, ThinkingStep, ToolCall
from task_agents.service.message_service import MessageWriteService

logger = logging.getLogger(__name__)


def build_repos(app: Any) -> tuple[MongoMessageRepository, RedisContextRepository]:
    """从应用容器构建 Mongo / Redis 仓储

    同时兼容两种接线来源：app.state（历史约定）与 core.services 容器
    （lifespan 中统一初始化）。优先 app.state，缺失时回落容器。
    """
    settings = get_settings()

    mongo_db = getattr(app.state, "mongo_db", None)
    if mongo_db is None:
        from task_agents.core.services import service_container
        mongo_client = service_container.get_client("mongo_client")
        mongo_db = mongo_client[settings.MONGO_DATABASE]

    redis_client = getattr(app.state, "redis_client", None)
    if redis_client is None:
        from task_agents.core.services import service_container
        redis_client = service_container.get_client("redis_client")

    mongo_repo = MongoMessageRepository(
        db=mongo_db,
        collection_name=settings.MONGO_SESSION_COLLECTION,
    )
    redis_repo = RedisContextRepository(
        client=redis_client,
        max_messages=turns_to_messages(settings.REDIS_CONTEXT_MAX_TURNS),
        ttl_seconds=settings.REDIS_CONTEXT_TTL_SECONDS,
    )
    return mongo_repo, redis_repo


def _db_factory(app: Any) -> async_sessionmaker:
    """取 MySQL 会话工厂：优先 app.state，缺失时回落服务容器"""
    factory = getattr(app.state, "db_session_factory", None)
    if factory is not None:
        return factory
    from task_agents.core.services import service_container
    return service_container.get_client("db_session_factory")


async def get_message_count(app: Any, session_id: str) -> int:
    """读取会话当前消息数（MySQL 权威值）

    用于在写入前判定是否首轮对话。自建连接，可在流式生成器中安全调用。
    """
    factory = _db_factory(app)
    async with factory() as db:
        repo = MySQLSessionRepository(db)
        return await repo.get_message_count(session_id) or 0


async def backfill_title(
    app: Any,
    session_id: str,
    first_user_content: str,
) -> None:
    """占位标题回填：会话仍叫「新会话」时，按首句前 50 字更新

    显式创建会话（POST /api/sessions）时没有消息可提取标题，占位创建；
    首轮消息落库后在此回填真实标题。失败只记日志，不影响对话。
    """
    try:
        factory = _db_factory(app)
        async with factory() as db:
            repo = MySQLSessionRepository(db)
            session = await repo.get_by_id(session_id)
            if session is None or session.title != DEFAULT_TITLE:
                return
            title = extract_title(
                first_user_content, get_settings().SESSION_TITLE_MAX_CHARS
            )
            if not title:
                return
            await repo.update_title(session_id, title)
            await repo.commit()
        logger.info("占位标题已回填: session_id=%s title=%s", session_id, title)
    except Exception:
        logger.exception("标题回填失败，保留占位标题: session_id=%s", session_id)


async def persist_turn(
    app: Any,
    session_id: str,
    user_id: str,
    user_content: str,
    assistant_content: str,
    assistant_tool_calls: Optional[list[ToolCall]] = None,
    assistant_thinking_steps: Optional[list[ThinkingStep]] = None,
    assistant_citations: Optional[list[Citation]] = None,
) -> Optional[int]:
    """持久化一轮对话：Redis 同步 + MySQL 同步 + MongoDB 落库

    供 SSE 流式路径调用。此时回复正文已完整推送给用户，落库不再阻塞
    用户可见的主流程；且这里**同步 await** MongoDB 写入而非交给后台任务，
    是为了让前端在收到 done 事件后立刻刷新历史时，一定能读到本轮消息
    （异步落库会与该刷新产生竞态，导致刚发的消息看不到）。

    自建数据库会话，不复用请求级 AsyncSession。

    Returns:
        最新的 message_count；失败返回 None（异常只记日志不外抛，
        因为流式响应已开始，无法再更改 HTTP 状态码）。
    """
    factory = _db_factory(app)
    try:
        mongo_repo, redis_repo = build_repos(app)
        async with factory() as db:
            service = MessageWriteService(
                session_repo=MySQLSessionRepository(db),
                mongo_repo=mongo_repo,
                redis_repo=redis_repo,
            )
            turn = await service.record_turn(
                session_id=session_id,
                user_id=user_id,
                user_content=user_content,
                assistant_content=assistant_content,
                assistant_tool_calls=assistant_tool_calls,
                assistant_thinking_steps=assistant_thinking_steps,
                assistant_citations=assistant_citations,
            )
            await service.write_messages_to_mongo(session_id, turn.messages)
            return turn.message_count
    except Exception:
        logger.exception(
            "流式对话持久化失败: session_id=%s user_id=%s", session_id, user_id
        )
        return None


async def persist_messages(app: Any, session_id: str, messages: list[StoredMessage]) -> None:
    """把一轮对话的消息异步追加到 MongoDB

    供 POST /api/chat（非流式）的 BackgroundTasks 调用。
    任何异常都只记日志不外抛：响应已经发出，抛出无法告知客户端，
    只会污染日志。失败依赖日志告警与 count 对账补偿。
    """
    if not messages:
        return

    try:
        mongo_repo, _ = build_repos(app)
        ok = await mongo_repo.append_messages(session_id, messages)
        if ok:
            logger.info(
                "MongoDB 异步落库完成: session_id=%s count=%s seqs=%s",
                session_id, len(messages), [m.seq for m in messages],
            )
        else:
            logger.error(
                "MongoDB 异步落库未生效: session_id=%s count=%s",
                session_id, len(messages),
            )
    except Exception:
        logger.exception(
            "MongoDB 异步落库异常，等待对账补偿: session_id=%s seqs=%s",
            session_id, [m.seq for m in messages],
        )


__all__ = [
    "backfill_title",
    "build_repos",
    "get_message_count",
    "persist_messages",
    "persist_turn",
]
