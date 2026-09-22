"""
FastAPI 主入口

lifespan 中通过 core.services.ServiceContainer 统一初始化外部客户端：
- MySQL   engine + session_factory，并建表
- MongoDB motor（仓储层）+ pymongo（MongoDBStore 长期记忆）
- Redis   同步/异步客户端 + RedisSaver checkpointer（短期记忆）
- Agent   注册表

路由统一挂 /api 前缀，走分层架构（router -> service -> repository）：
- /api/sessions*      会话列表、创建、历史加载、标题、结束会话
- /api/chat           发送消息，一次性返回完整回复
- /api/chat/stream    发送消息，SSE 流式推送（前端主链路）
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from task_agents.agent.factory import build_agents
from task_agents.core.config import get_settings
from task_agents.core.services import service_container as container
from task_agents.routers.chat import router as chat_router
from task_agents.routers.session import router as session_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ==================== Lifespan：启动时初始化所有资源 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ========== 启动阶段 ==========
    await container.initialize()

    # 把容器里的客户端镜像到 app.state，保持既有代码的取用方式兼容
    app.state.db_session_factory = container.get_client("db_session_factory")
    app.state.mongo_db = container.get_client("mongo_db")
    app.state.redis_client = container.get_client("redis_async_client")

    # 创建 Agent（传入容器）
    agents = build_agents()
    app.state.agents = agents
    logger.info("🎉 服务启动完成，开始接收请求")
    yield
    # ========== 关闭阶段 ==========
    await container.shutdown()
    logger.info("👋 服务已关闭")


app = FastAPI(
    title="task-agents",
    description="AI Agent 对话助手系统 — 会话隔离 + SSE 流式 + 三层记忆存储（Redis 短期 / MySQL 业务 / MongoDB 长期）",
    version="0.3.0",
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

app.include_router(session_router, prefix="/api")
app.include_router(chat_router, prefix="/api")


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


@app.get("/agents", summary="列出已注册的 Agent")
async def list_agents(request: Request):
    return {"agents": list(request.app.state.agents.keys())}


# ==================== 启动入口 ====================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
