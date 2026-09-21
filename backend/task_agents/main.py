"""
FastAPI 主入口

lifespan 中初始化四类资源并挂到 app.state（全应用生命周期复用，不在请求内建连）：
- MySQL   engine + session_factory，并建表
- MongoDB motor client + database，并建索引
- Redis   client
- Agent   注册表 + LLM 标题生成器

路由统一挂 /api 前缀，走分层架构（router -> service -> repository）：
- /api/sessions*      会话列表、创建、历史加载、标题、归档、删除
- /api/chat           发送消息，一次性返回完整回复
- /api/chat/stream    发送消息，SSE 流式推送（前端主链路）
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from task_agents.agent.factory import build_agents, get_llm_info, verify_llm
from task_agents.core.config import get_settings
from task_agents.database.engine import create_engine, create_session_factory, init_database
from task_agents.database.mongo import create_mongo_client, get_mongo_database, ping_mongo
from task_agents.database.redis import create_redis_client, ping_redis
from task_agents.repository.mongo_message_repository import MongoMessageRepository
from task_agents.routers.chat import router as chat_router
from task_agents.routers.session import router as session_router
from task_agents.service.title_service import build_title_generator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ==================== Lifespan：启动时初始化所有资源 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # —— 1. MySQL：会话元数据 ——
    logger.info("正在初始化 MySQL...")
    engine = create_engine(settings)
    await init_database(engine)
    app.state.db_engine = engine
    app.state.db_session_factory = create_session_factory(engine)
    logger.info("MySQL 初始化完成，表结构已就绪")

    # —— 2. MongoDB：对话历史详情 ——
    logger.info("正在初始化 MongoDB...")
    mongo_client = create_mongo_client(settings)
    mongo_db = get_mongo_database(mongo_client, settings)
    app.state.mongo_client = mongo_client
    app.state.mongo_db = mongo_db

    message_repo = MongoMessageRepository(mongo_db, settings.MONGO_SESSION_COLLECTION)
    await message_repo.ensure_indexes()

    if await ping_mongo(mongo_client):
        logger.info("MongoDB 连接正常")
    else:
        # 不阻断启动：MongoDB 不可用时聊天主流程仍可用（Redis + MySQL 可写），
        # 历史消息落库会在后台任务中重试并记录告警。
        logger.warning("MongoDB 暂不可用，历史消息落库将降级为后台重试")

    # —— 3. Redis：会话短期记忆 ——
    logger.info("正在初始化 Redis...")
    redis_client = create_redis_client(settings)
    app.state.redis_client = redis_client

    if await ping_redis(redis_client):
        logger.info("Redis 连接正常")
    else:
        logger.warning("Redis 暂不可用，上下文读写将降级（不影响消息落库）")

    # —— 4. Agent 实例 + 标题生成器 ——
    logger.info("正在构建 Agent 实例...")
    agent_registry = build_agents(settings)
    app.state.agents = agent_registry
    logger.info("Agent 构建完成，已注册: %s", list(agent_registry.keys()))

    try:
        app.state.title_generator = await build_title_generator(settings)
    except Exception:
        # 标题生成属于增强能力，失败时保留占位标题即可
        logger.exception("标题生成器构建失败，会话将保留占位标题")
        app.state.title_generator = None

    yield

    # —— 关闭阶段：释放所有连接 ——
    logger.info("正在关闭数据库连接...")
    try:
        await redis_client.aclose()
    except Exception:
        logger.exception("关闭 Redis 客户端异常")
    try:
        mongo_client.close()
    except Exception:
        logger.exception("关闭 MongoDB 客户端异常")
    await engine.dispose()
    logger.info("应用已关闭")


# ==================== FastAPI 应用初始化 ====================
app = FastAPI(
    title="task-agents",
    description="AI Agent 对话助手系统 — 用户会话管理（MySQL / MongoDB / Redis 分层存储）",
    version="0.2.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 新分层架构路由，统一挂 /api 前缀
app.include_router(session_router, prefix="/api")
app.include_router(chat_router, prefix="/api")


# ==================== 运维接口 ====================
@app.get("/health", summary="健康检查")
async def health_check(request: Request):
    """服务状态 + LLM 配置 + 三个存储引擎连通性"""
    mongo_ok = await ping_mongo(request.app.state.mongo_client)
    redis_ok = await ping_redis(request.app.state.redis_client)

    return {
        "status": "ok" if (mongo_ok and redis_ok) else "degraded",
        "llm": get_llm_info(),
        "storage": {
            "mysql": True,   # 能进到此处说明引擎已建立；细粒度探活见 /health/db
            "mongodb": mongo_ok,
            "redis": redis_ok,
        },
    }


@app.get("/health/db", summary="MySQL 连通性检查")
async def health_db(request: Request):
    """对 MySQL 执行一次轻量查询，验证连接池可用"""
    from sqlalchemy import text

    try:
        async with request.app.state.db_session_factory() as db:
            await db.execute(text("SELECT 1"))
        return {"mysql": "ok"}
    except Exception as e:
        logger.exception("MySQL 健康检查失败")
        raise HTTPException(status_code=503, detail=f"MySQL 不可用: {type(e).__name__}") from e


@app.get("/health/llm/verify", summary="LLM 连通性校验")
async def verify_llm_endpoint():
    """实际调用 LLM 验证连通性（会消耗少量 Token）"""
    result = verify_llm()
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result)
    return result


@app.get("/agents", summary="列出已注册的 Agent")
async def list_agents(request: Request):
    return {"agents": list(request.app.state.agents.keys())}


# ==================== 启动入口 ====================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
