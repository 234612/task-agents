"""会话管理路由

对外路径统一挂 /api 前缀（见 main.py 的 include_router）。
本层只做 HTTP 语义转换：参数校验、领域异常 → HTTP 状态码、模型组装。
业务编排全部在 Service 层。
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from task_agents.schemas.api import (
    CreateSessionRequest,
    MessageListResponse,
    MessageResponse,
    PaginationMeta,
    SessionListResponse,
    SessionResponse,
    UpdateTitleRequest,
    UserScopedRequest,
)
from task_agents.service.dependencies import SessionServiceDep
from task_agents.service.session_service import (
    SessionAccessDeniedError,
    SessionNotFoundError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


# ==================== 异常转换 ====================


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


# ==================== 路由处理器 ====================


@router.get("", response_model=SessionListResponse, summary="分页查询用户会话列表")
async def list_sessions(
    service: SessionServiceDep,
    user_id: str = Query(min_length=1, max_length=64, description="用户 ID"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: Optional[int] = Query(None, ge=1, le=100, description="每页条数，留空用服务端默认值"),
):
    """按最后活跃时间倒序分页返回用户的会话列表

    查询走 (user_id, updated_at) 联合索引，避免 filesort。
    """
    result = await service.list_sessions(user_id=user_id, page=page, page_size=page_size)

    return SessionListResponse(
        items=[SessionResponse.model_validate(s.to_dict()) for s in result.items],
        pagination=PaginationMeta(
            page=result.page,
            page_size=result.page_size,
            total=result.total,
            total_pages=result.total_pages,
        ),
    )


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建新会话",
)
async def create_session(
    payload: CreateSessionRequest,
    service: SessionServiceDep,
):
    """创建会话：初始化 MySQL 元数据、MongoDB 文档骨架与 Redis 上下文"""
    session = await service.create_session(
        user_id=payload.user_id,
        agent_key=payload.agent_key,
        title=payload.title,
    )
    return SessionResponse.model_validate(session.to_dict())


@router.get(
    "/{session_id}/messages",
    response_model=MessageListResponse,
    summary="加载会话完整历史消息",
)
async def list_messages(
    session_id: str,
    service: SessionServiceDep,
    user_id: str = Query(min_length=1, max_length=64, description="用户 ID，用于归属校验"),
    skip: int = Query(0, ge=0, description="跳过前 N 条消息"),
    limit: int = Query(0, ge=0, description="最多返回 N 条，0 表示不限制"),
):
    """从 MongoDB 读取指定会话的历史消息，按 seq 正序返回

    会校验会话归属，非本人会话返回 403（不泄露会话是否存在）。
    """
    try:
        messages = await service.load_history(
            session_id=session_id,
            user_id=user_id,
            skip=skip,
            limit=limit,
        )
    except SessionNotFoundError as e:
        raise _not_found(str(e)) from e
    except SessionAccessDeniedError as e:
        raise _forbidden(str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    return MessageListResponse(
        session_id=session_id,
        total=len(messages),
        messages=[MessageResponse.model_validate(m.model_dump()) for m in messages],
    )


@router.patch("/{session_id}/title", response_model=SessionResponse, summary="更新会话标题")
async def update_title(
    session_id: str,
    payload: UpdateTitleRequest,
    service: SessionServiceDep,
):
    """手动更新标题（LLM 自动生成走后台任务，不经此接口）"""
    try:
        session = await service.update_title(
            session_id=session_id,
            user_id=payload.user_id,
            title=payload.title,
        )
    except SessionNotFoundError as e:
        raise _not_found(str(e)) from e
    except SessionAccessDeniedError as e:
        raise _forbidden(str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    return SessionResponse.model_validate(session.to_dict())


@router.post("/{session_id}/archive", response_model=SessionResponse, summary="归档会话")
async def archive_session(
    session_id: str,
    payload: UserScopedRequest,
    service: SessionServiceDep,
):
    """归档会话：不再出现在默认列表，数据保留，同时释放 Redis 上下文"""
    try:
        session = await service.archive_session(session_id, user_id=payload.user_id)
    except SessionNotFoundError as e:
        raise _not_found(str(e)) from e
    except SessionAccessDeniedError as e:
        raise _forbidden(str(e)) from e

    return SessionResponse.model_validate(session.to_dict())


@router.delete("/{session_id}", summary="删除会话（软删除）")
async def delete_session(
    session_id: str,
    service: SessionServiceDep,
    user_id: str = Query(min_length=1, max_length=64, description="用户 ID，用于归属校验"),
):
    """软删除会话：MySQL 标记 status=deleted，Redis 上下文立即释放

    MongoDB 历史文档默认保留，便于审计与恢复。
    """
    try:
        await service.delete_session(session_id, user_id=user_id)
    except SessionNotFoundError as e:
        raise _not_found(str(e)) from e
    except SessionAccessDeniedError as e:
        raise _forbidden(str(e)) from e

    return {"message": "会话已删除", "session_id": session_id}
