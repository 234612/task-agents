"""API 请求 / 响应模型（Pydantic v2）

集中定义 HTTP 边界的契约，与内部领域模型（database/models.py、
schemas/mongo.py）解耦：对外只暴露需要的字段，内部结构变更不影响 API。
"""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ==================== 通用 ====================


class PaginationMeta(BaseModel):
    """分页元信息"""
    page: int = Field(description="当前页码，从 1 开始")
    page_size: int = Field(description="每页条数")
    total: int = Field(description="符合条件的总条数")
    total_pages: int = Field(description="总页数")


# ==================== 会话 ====================


class CreateSessionRequest(BaseModel):
    """创建会话请求"""
    user_id: str = Field(min_length=1, max_length=64, description="用户 ID")
    agent_key: str = Field(min_length=1, max_length=64, description="agent 标识")
    title: Optional[str] = Field(
        default=None,
        max_length=256,
        description="会话标题；留空则由服务端用占位标题，后续由 LLM 生成",
    )

    @field_validator("user_id", "agent_key")
    @classmethod
    def _strip_required(cls, v: str) -> str:
        """去除首尾空白后再校验非空，避免传入全空格字符串"""
        v = v.strip()
        if not v:
            raise ValueError("不能为空或纯空白字符")
        return v


class SessionResponse(BaseModel):
    """会话响应（列表项与创建结果共用）"""
    session_id: str = Field(description="会话唯一标识")
    user_id: str = Field(description="所属用户 ID")
    title: str = Field(description="会话标题")
    agent_key: str = Field(description="agent 标识")
    status: str = Field(description="会话状态")
    message_count: int = Field(description="累计消息条数")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="最后活跃时间")

    @classmethod
    def from_orm_dict(cls, data: dict[str, Any]) -> "SessionResponse":
        """从 ChatSession.to_dict() 的结果构建响应"""
        return cls.model_validate(data)


class SessionListResponse(BaseModel):
    """会话列表响应"""
    items: list[SessionResponse] = Field(default_factory=list, description="会话列表")
    pagination: PaginationMeta = Field(description="分页信息")


class UpdateTitleRequest(BaseModel):
    """更新标题请求"""
    user_id: str = Field(min_length=1, max_length=64, description="用户 ID，用于归属校验")
    title: str = Field(min_length=1, max_length=256, description="新标题")

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("标题不能为空或纯空白字符")
        return v


class UserScopedRequest(BaseModel):
    """仅需用户身份的操作请求（如归档）"""
    user_id: str = Field(min_length=1, max_length=64, description="用户 ID，用于归属校验")


# ==================== 消息 ====================


class ToolCallResponse(BaseModel):
    """工具调用响应"""
    model_config = ConfigDict(extra="allow")

    id: Optional[str] = Field(default=None, description="工具调用 ID")
    name: Optional[str] = Field(default=None, description="工具名称")
    args: dict[str, Any] = Field(default_factory=dict, description="工具入参")


class MessageResponse(BaseModel):
    """历史消息响应"""
    model_config = ConfigDict(extra="allow")

    seq: int = Field(description="会话内消息序号，从 1 开始")
    role: Literal["user", "assistant", "system", "tool"] = Field(description="消息角色")
    content: str = Field(default="", description="消息正文")
    tool_calls: list[ToolCallResponse] = Field(default_factory=list, description="工具调用记录")
    ts: datetime = Field(description="消息产生时间")


class MessageListResponse(BaseModel):
    """历史消息列表响应"""
    session_id: str = Field(description="会话唯一标识")
    total: int = Field(description="返回的消息条数")
    messages: list[MessageResponse] = Field(default_factory=list, description="消息列表")


# ==================== 聊天 ====================


class ChatRequest(BaseModel):
    """发送消息请求"""
    session_id: str = Field(min_length=1, max_length=36, description="会话 ID")
    user_id: str = Field(min_length=1, max_length=64, description="用户 ID")
    agent_key: str = Field(min_length=1, max_length=64, description="agent 标识")
    content: str = Field(min_length=1, description="用户输入内容")

    @field_validator("content")
    @classmethod
    def _strip_content(cls, v: str) -> str:
        """正文允许内部空白，但不能是纯空白"""
        v = v.strip()
        if not v:
            raise ValueError("消息内容不能为空")
        return v


class ChatMessageResponse(BaseModel):
    """单条消息的回执（含服务端分配的 seq）"""
    seq: int = Field(description="会话内消息序号")
    role: Literal["user", "assistant"] = Field(description="消息角色")
    content: str = Field(description="消息正文")
    tool_calls: list[ToolCallResponse] = Field(default_factory=list, description="工具调用记录")
    ts: datetime = Field(description="消息产生时间")


class ChatResponse(BaseModel):
    """发送消息响应"""
    session_id: str = Field(description="会话唯一标识")
    user_message: ChatMessageResponse = Field(description="用户消息回执")
    assistant_message: ChatMessageResponse = Field(description="助手回复")
    message_count: int = Field(description="会话当前累计消息条数")
