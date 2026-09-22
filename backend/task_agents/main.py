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
    # config = get_settings()
    container.initialize()

    # 创建 Agent（传入容器）
    agents = build_agents()
    app.state.agents = agents
    logger.info("🎉 服务启动完成，开始接收请求")
    yield
    # ========== 关闭阶段 ==========
    await container.shutdown()
    logger.info("👋 服务已关闭")

app = FastAPI(
    title="Task Agents RAG 中台",
    lifespan=lifespan
)
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
