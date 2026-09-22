"""后台任务 — MongoDB 异步落库、三存储写入与 LLM 标题生成

时序约束（重要）：
FastAPI 中带 yield 的依赖（如 get_db_session）其清理代码在响应发送后、
后台任务执行前就会运行。因此后台任务**不能**复用请求级的 AsyncSession，
必须自行从 app.state 取客户端 / 会话工厂新建连接。

同理，SSE 流式响应（StreamingResponse）的生成器在执行期间，请求级依赖
的生命周期已不可依赖。故本模块所有函数只接收 app 与纯数据参数，一律
自建连接，保证在请求上下文销毁后仍可安全执行。
"""
import logging
from typing import Any, Optional

from task_agents.core.config import get_settings
from task_agents.database.redis_keys import turns_to_messages
from task_agents.repository.mongo_message_repository import MongoMessageRepository
from task_agents.repository.mysql_session_repository import MySQLSessionRepository
from task_agents.repository.redis_context_repository import RedisContextRepository
from task_agents.schemas.mongo import StoredMessage, ToolCall
from task_agents.service.message_service import MessageWriteService

logger = logging.getLogger(__name__)


def _mongo_repo(app: Any) -> MongoMessageRepository:
    """从 app.state 构建 MongoDB 仓储（不依赖请求上下文）"""
    settings = get_settings()
    return MongoMessageRepository(
        db=app.state.mongo_db,
        collection_name=settings.MONGO_SESSION_COLLECTION,
    )


def _redis_repo(app: Any) -> RedisContextRepository:
    """从 app.state 构建 Redis 仓储（不依赖请求上下文）"""
    settings = get_settings()
    return RedisContextRepository(
        client=app.state.redis_client,
        max_messages=turns_to_messages(settings.REDIS_CONTEXT_MAX_TURNS),
        ttl_seconds=settings.REDIS_CONTEXT_TTL_SECONDS,
    )


async def get_message_count(app: Any, session_id: str) -> int:
    """读取会话当前消息数（MySQL 权威值）

    用于在写入前判定是否首轮对话。自建连接，可在流式生成器中安全调用。
    """
    factory = app.state.db_session_factory
    async with factory() as db:
        repo = MySQLSessionRepository(db)
        return await repo.get_message_count(session_id) or 0


async def persist_turn(
    app: Any,
    session_id: str,
    user_id: str,
    user_content: str,
    assistant_content: str,
    assistant_tool_calls: Optional[list[ToolCall]] = None,
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
    factory = app.state.db_session_factory
    try:
        async with factory() as db:
            service = MessageWriteService(
                session_repo=MySQLSessionRepository(db),
                mongo_repo=_mongo_repo(app),
                redis_repo=_redis_repo(app),
            )
            turn = await service.record_turn(
                session_id=session_id,
                user_id=user_id,
                user_content=user_content,
                assistant_content=assistant_content,
                assistant_tool_calls=assistant_tool_calls,
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
        repo = _mongo_repo(app)
        ok = await repo.append_messages(session_id, messages)
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


async def generate_and_save_title(
    app: Any,
    session_id: str,
    user_id: str,
    content: str,
) -> None:
    """首轮对话后由 LLM 生成标题并回写 MySQL

    自建数据库会话；失败时保留占位标题，不影响已完成的对话写入。
    """
    title_generator = getattr(app.state, "title_generator", None)
    if title_generator is None:
        logger.debug("未配置标题生成器，跳过: session_id=%s", session_id)
        return

    try:
        title = await title_generator(content)
    except Exception:
        logger.exception("LLM 生成标题失败，保留占位标题: session_id=%s", session_id)
        return

    if not title:
        logger.info("标题生成结果为空，保留占位标题: session_id=%s", session_id)
        return

    try:
        factory = app.state.db_session_factory
        async with factory() as db:
            repo = MySQLSessionRepository(db)
            updated = await repo.update_title(session_id, title)
            if updated is None:
                logger.warning("标题回写失败，会话不存在: session_id=%s", session_id)
                return
            await repo.commit()
        logger.info("标题已回写: session_id=%s user_id=%s title=%s", session_id, user_id, title)
    except Exception:
        logger.exception("标题回写 MySQL 异常: session_id=%s", session_id)


__all__ = ["generate_and_save_title", "get_message_count", "persist_messages", "persist_turn"]
