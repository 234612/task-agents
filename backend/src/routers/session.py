"""会话管理路由"""
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.models import Session


router = APIRouter(prefix="/sessions", tags=["sessions"])


# ==================== 请求/响应模型 ====================
class CreateSessionRequest(BaseModel):
    """创建会话请求"""
    user_id: str
    title: str
    agent_key: str


class UpdateTitleRequest(BaseModel):
    """更新标题请求"""
    title: str


class SessionResponse(BaseModel):
    """会话响应"""
    id: str
    user_id: str
    title: str
    agent_key: str
    last_message_at: str
    created_at: str


def _get_db(request: Request) -> AsyncSession:
    """从 app.state 获取数据库会话工厂，创建新会话"""
    return request.app.state.db_session_factory()


# ==================== 路由处理器 ====================
@router.get("/", response_model=List[SessionResponse])
async def list_sessions(user_id: str, request: Request):
    """获取用户的所有会话列表（按最后消息时间倒序）"""
    async with _get_db(request) as db:
        stmt = (
            select(Session)
            .where(Session.user_id == user_id)
            .order_by(Session.last_message_at.desc())
        )
        result = await db.execute(stmt)
        sessions = result.scalars().all()
        return [session.to_dict() for session in sessions]


@router.post("/", response_model=SessionResponse)
async def create_session(req: CreateSessionRequest, request: Request):
    """创建新会话"""
    async with _get_db(request) as db:
        session_id = str(uuid.uuid4())
        now = datetime.utcnow()

        session = Session(
            id=session_id,
            user_id=req.user_id,
            title=req.title,
            agent_key=req.agent_key,
            last_message_at=now,
            created_at=now,
        )

        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session.to_dict()


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    """删除会话"""
    async with _get_db(request) as db:
        stmt = select(Session).where(Session.id == session_id)
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()

        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        await db.delete(session)
        await db.commit()

        # TODO: 清理 MongoDB 中对应的 checkpoint 数据
        return {"message": "会话已删除", "session_id": session_id}


@router.patch("/{session_id}/title")
async def update_session_title(
    session_id: str,
    req: UpdateTitleRequest,
    request: Request,
):
    """更新会话标题"""
    async with _get_db(request) as db:
        stmt = select(Session).where(Session.id == session_id)
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()

        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        session.title = req.title
        await db.commit()
        await db.refresh(session)
        return session.to_dict()
