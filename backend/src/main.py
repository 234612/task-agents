"""
FastAPI 主入口
- lifespan 中初始化 MySQL + MongoDB + Agent 注册表
- 通过 DI 从 app.state 中按 agent_key 获取 agent 实例
- ChatService 封装 runtime 配置（user_id + session_id）
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import get_settings
from .database.engine import create_engine, create_session_factory, init_database
from .agent.factory import build_agents, get_agent_by_key, get_llm_info, verify_llm
from .service.chat_service import ChatService
from .routers.session import router as session_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ==================== 请求/响应模型 ====================
class ChatRequest(BaseModel):
    content: str                    # 用户输入内容
    agent_key: str                  # agent 标识，如 "market_researcher"
    session_id: str                 # 会话 ID，用于 checkpoint 隔离
    user_id: str                    # 用户 ID


# ==================== Lifespan：启动时初始化所有资源 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # —— 1. 初始化 MySQL ——
    print("🔧 正在初始化 MySQL...")
    engine = create_engine(settings)
    await init_database(engine)
    app.state.db_engine = engine
    app.state.db_session_factory = create_session_factory(engine)
    print("✅ MySQL 初始化完成，表结构已就绪")

    # —— 2. 构建 Agent 实例 ——
    print("🚀 正在构建 Agent 实例...")
    agent_registry = build_agents(settings)
    app.state.agents = agent_registry
    print(f"✅ Agent 构建完成，已注册: {list(agent_registry.keys())}")

    yield

    # —— 关闭阶段：释放数据库连接池 ——
    print("👋 正在关闭数据库连接...")
    await engine.dispose()
    print("👋 应用关闭")


# ==================== FastAPI 应用初始化 ====================
app = FastAPI(lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册会话路由
app.include_router(session_router)


# ==================== 路由 ====================
@app.post("/chat")
async def chat_stream(request: Request, chat_req: ChatRequest):
    """
    SSE 流式聊天接口。
    前端携带 agent_key、user_id、session_id、content。
    """
    try:
        agent = get_agent_by_key(chat_req.agent_key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    service = ChatService(user_id=chat_req.user_id)

    return StreamingResponse(
        service.stream_chat(
            agent=agent,
            content=chat_req.content,
            session_id=chat_req.session_id,
        ),
        media_type="text/event-stream",
    )


@app.get("/health")
async def health_check():
    """健康检查接口：包含服务状态 + LLM 配置校验"""
    return {
        "status": "ok",
        "llm": get_llm_info(),
    }


@app.get("/health/llm/verify")
async def verify_llm_endpoint():
    """
    LLM 连通性校验接口（会消耗少量 Token）
    """
    result = verify_llm()
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result)
    return result


@app.get("/agents")
async def list_agents():
    """列出所有已注册的 agent key"""
    return {"agents": list(app.state.agents.keys())}


# ==================== 启动入口 ====================
if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host="0.0.0.0", port=settings.APP_PORT if hasattr(settings, 'APP_PORT') else 8000)
