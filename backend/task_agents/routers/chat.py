"""聊天路由

两个发送消息接口：
- POST /api/chat        一次性返回完整回复，MongoDB 经 BackgroundTasks 异步落库
- POST /api/chat/stream SSE 流式推送（打字机效果），推送完毕后同步完成三存储写入

三存储双写口径：
1. 调用 Agent 生成回复
2. 同步写 Redis（Prompt 上下文即时生效）+ 同步更新 MySQL（updated_at、message_count）
3. 写 MongoDB（stream 同步 await / chat 异步后台）
4. 首轮对话按「首句前 50 字」回填会话标题（不再调用 LLM）

请求体字段是 message（见 schemas/api.py 的 ChatRequest），不是 content。

持久化逻辑只接收 app 与纯数据参数，不复用请求级 AsyncSession——因为带 yield
的依赖会在后台任务/流式生成器执行前完成清理（详见 service/background.py）。
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from task_agents.schemas.api import (
    ChatMessageResponse,
    ChatRequest,
    ChatResponse,
)
from task_agents.schemas.mongo import Citation, ThinkingStep, ToolCall
from task_agents.service.background import (
    backfill_title,
    get_message_count,
    persist_messages,
    persist_turn,
)
from task_agents.service.chat_service import AgentNotFoundError
from task_agents.service.dependencies import ChatServiceDep
from task_agents.service.session_service import (
    SessionAccessDeniedError,
    SessionEndedError,
    SessionNotFoundError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/stream",
    summary="发送消息（SSE 流式 + 三存储持久化）",
    response_class=StreamingResponse,
)
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    service: ChatServiceDep,
):
    """流式聊天接口 — 前端主链路

    与 POST /api/chat 的区别：本接口以 SSE 增量推送回复（打字机效果），
    推送完毕后执行三存储持久化，并在 done 事件中回传 message_count。

    错误处理（顺序敏感）：
    会话隔离（404/403/409）与 agent 解析必须在返回 StreamingResponse
    **之前**完成。SSE 响应一旦开始发送，状态码已固定为 200，此后只能靠
    data 帧里的 error 事件传递失败信息，前端无法再用 HTTP 状态码判断。

    自动建会话时，新 session_id 由首帧 meta 事件回传前端。
    """
    app = request.app

    # —— 1. 会话隔离：续聊校验归属与状态，首次对话自动建会话 ——
    try:
        session_id, created = await service.prepare_session(
            user_id=payload.user_id,
            agent_key=payload.agent_key,
            session_id=payload.session_id,
            first_message=payload.message,
        )
    except SessionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except SessionAccessDeniedError as e:
        logger.warning(
            "越权流式聊天被拒绝: session_id=%s user_id=%s",
            payload.session_id, payload.user_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except SessionEndedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    # —— 2. 前置校验：agent 是否注册 ——
    try:
        agent = service.resolve_agent(payload.agent_key)
    except AgentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    # —— 3. 首轮判定：用于决定是否在流结束后回填标题 ——
    is_first_turn = (await get_message_count(app, session_id)) == 0

    # —— 4. 持久化回调：流式生成器内部调用，自建连接不复用请求会话 ——
    async def on_complete(
        assistant_text: str,
        tool_calls: list[ToolCall],
        thinking_steps: list[ThinkingStep],
        citations: list[Citation],
    ) -> Optional[int]:
        count = await persist_turn(
            app,
            session_id=session_id,
            user_id=payload.user_id,
            user_content=payload.message,
            assistant_content=assistant_text,
            assistant_tool_calls=tool_calls,
            assistant_thinking_steps=thinking_steps,
            assistant_citations=citations,
        )
        if is_first_turn and count is not None:
            # 占位标题回填（首句前 50 字，不调 LLM），放到独立任务里不拖慢 done 事件
            asyncio.create_task(
                backfill_title(app, session_id, payload.message)
            )
        return count

    return StreamingResponse(
        service.stream_chat_persist(
            agent=agent,
            content=payload.message,
            session_id=session_id,
            user_id=payload.user_id,
            created=created,
            on_complete=on_complete,
        ),
        media_type="text/event-stream",
        headers={
            # 禁用代理缓冲，否则 Nginx 等会攒够缓冲区才转发，打字机效果失效
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("", response_model=ChatResponse, summary="发送消息（三存储双写）")
async def chat(
    payload: ChatRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    service: ChatServiceDep,
):
    """发送用户消息并返回助手回复

    错误处理：
    - 会话不存在 → 404
    - 会话属于其他用户 → 403（越权写入被拒绝）
    - 会话已结束（status=2）→ 409
    - agent_key 未注册 → 404
    - Agent 调用失败 → 502（上游模型异常，不写入任何存储）

    顺序敏感：归属/状态异常必须排在宽泛的 Exception 之前，否则会被兜底分支
    吞掉并误报成「模型调用失败」，掩盖真实的权限问题。
    """
    app = request.app

    try:
        result = await service.invoke_chat(
            session_id=payload.session_id,
            user_id=payload.user_id,
            agent_key=payload.agent_key,
            content=payload.message,
        )
    except SessionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except SessionAccessDeniedError as e:
        logger.warning(
            "越权发送消息被拒绝: session_id=%s user_id=%s",
            payload.session_id, payload.user_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except SessionEndedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except AgentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        # Agent / LLM 侧异常：此时未写任何存储，可安全重试
        logger.exception(
            "聊天处理失败: session_id=%s user_id=%s agent_key=%s",
            payload.session_id, payload.user_id, payload.agent_key,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"模型调用失败: {type(e).__name__}",
        ) from e

    # —— 异步写 MongoDB：不阻塞响应 ——
    background_tasks.add_task(
        persist_messages,
        app,
        result.session_id,
        result.pending_messages,
    )

    # —— 首轮对话：回填真实标题（占位「新会话」时按首句前 50 字更新） ——
    if result.is_first_turn:
        background_tasks.add_task(
            backfill_title,
            app,
            result.session_id,
            payload.message,
        )

    logger.info(
        "聊天请求完成: session_id=%s 计数=%s 首轮=%s",
        result.session_id, result.message_count, result.is_first_turn,
    )

    return ChatResponse(
        session_id=result.session_id,
        created=result.created,
        user_message=ChatMessageResponse.model_validate(result.user_message.model_dump()),
        assistant_message=ChatMessageResponse.model_validate(
            result.assistant_message.model_dump()
        ),
        message_count=result.message_count or 0,
    )
