"""依赖注入 — 按请求装配 Repository 与 Service

FastAPI 的 Depends 体系：
- get_db_session: 每个请求一个 AsyncSession，请求结束自动关闭
- get_session_service / get_message_service / get_chat_service: 基于连接装配服务

所有底层客户端（engine / motor client / redis client）由 main.py 的 lifespan
创建并挂在 app.state 上，全应用生命周期复用，不在请求内重复建连。

注意：凡是被 FastAPI 当作依赖调用的函数，其参数必须显式用 Depends 标注，
否则 FastAPI 会把复杂类型（如 Settings / AsyncSession）误判为请求体模型。
"""
import logging
from typing import Annotated, AsyncIterator

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from task_agents.agent.factory import get_agent_by_key
from task_agents.core.config import Settings, get_settings
from task_agents.database.redis_keys import turns_to_messages
from task_agents.repository.mongo_message_repository import MongoMessageRepository
from task_agents.repository.mysql_session_repository import MySQLSessionRepository
from task_agents.repository.redis_context_repository import RedisContextRepository
from task_agents.service.chat_service import ChatService
from task_agents.service.message_service import MessageWriteService
from task_agents.service.session_service import SessionService

logger = logging.getLogger(__name__)


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """每个请求一个数据库会话，退出时自动关闭

    使用 async with 保证即使处理函数抛异常也会释放连接回池。
    """
    factory = request.app.state.db_session_factory
    async with factory() as session:
        yield session


# 供路由使用的依赖别名（Annotated 写法便于类型检查与复用）
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def get_mongo_repo(request: Request) -> MongoMessageRepository:
    """装配 MongoDB 会话文档仓储"""
    settings: Settings = get_settings()
    return MongoMessageRepository(
        db=request.app.state.mongo_db,
        collection_name=settings.MONGO_SESSION_COLLECTION,
    )


def get_redis_repo(request: Request) -> RedisContextRepository:
    """装配 Redis 上下文仓储

    窗口大小由「轮数 × 2」换算为消息条数，配置变更无需改代码。
    """
    settings: Settings = get_settings()
    client: Redis = request.app.state.redis_client
    return RedisContextRepository(
        client=client,
        max_messages=turns_to_messages(settings.REDIS_CONTEXT_MAX_TURNS),
        ttl_seconds=settings.REDIS_CONTEXT_TTL_SECONDS,
    )


def get_session_repo(db: DbSession) -> MySQLSessionRepository:
    """装配 MySQL 会话元数据仓储"""
    return MySQLSessionRepository(db)


def get_message_service(
    request: Request,
    db: DbSession,
) -> MessageWriteService:
    """装配消息写入编排服务"""
    return MessageWriteService(
        session_repo=MySQLSessionRepository(db),
        mongo_repo=get_mongo_repo(request),
        redis_repo=get_redis_repo(request),
    )


def get_session_service(
    request: Request,
    db: DbSession,
) -> SessionService:
    """装配会话服务"""
    return SessionService(
        session_repo=MySQLSessionRepository(db),
        mongo_repo=get_mongo_repo(request),
        redis_repo=get_redis_repo(request),
        settings=get_settings(),
    )


def get_chat_service(request: Request, db: DbSession) -> ChatService:
    """装配聊天服务

    agent_getter 直接复用 factory 的注册表查询（未注册时抛 KeyError，
    由 ChatService.resolve_agent 转成 AgentNotFoundError）。
    session_service 用于在写入前校验会话归属，防止越权写他人会话。

    注：标题不再由 LLM 生成（改为首句前 50 字，见 core/titleutils.py），
    因此这里不再注入 title_generator。
    """
    return ChatService(
        message_service=get_message_service(request, db),
        agent_getter=get_agent_by_key,
        session_service=get_session_service(request, db),
    )


# 服务层依赖别名，供路由签名使用
SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
MessageServiceDep = Annotated[MessageWriteService, Depends(get_message_service)]


__all__ = [
    "ChatServiceDep",
    "DbSession",
    "MessageServiceDep",
    "SessionServiceDep",
    "get_chat_service",
    "get_db_session",
    "get_message_service",
    "get_mongo_repo",
    "get_redis_repo",
    "get_session_repo",
    "get_session_service",
]
