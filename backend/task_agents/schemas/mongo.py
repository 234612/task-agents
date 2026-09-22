"""MongoDB 会话文档定义

分层存储中 MongoDB 承担「对话历史详情」职责：文档模型的嵌套结构让
一次查询即可取回整条会话的全部消息，无需多表 JOIN；messages 数组的
追加写（$push）天然契合高频写入场景。

集合名由配置 MONGO_SESSION_COLLECTION 指定，默认 chat_sessions。

文档结构：
{
  "_id":       "会话 UUID，与 MySQL chat_sessions.session_id 一致",
  "user_id":   "所属用户 ID（冗余存储，便于按用户做数据治理）",
  "agent_key": "使用的 agent 标识",
  "messages": [
    {"seq": 1, "role": "user", "content": "...", "tool_calls": [], "ts": ISODate}
  ],
  "created_at": ISODate,
  "updated_at": ISODate
}
"""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from task_agents.core.timeutils import utcnow

# 会话中出现的角色。tool 角色用于承载工具执行结果
MessageRole = Literal["user", "assistant", "system", "tool"]


class ToolCall(BaseModel):
    """单次工具调用记录

    与 LangChain/LangGraph 的 tool_call 结构对齐，便于把 Agent 产出的
    工具调用原样落库，不做有损转换。
    """
    model_config = ConfigDict(extra="allow")

    id: Optional[str] = Field(default=None, description="工具调用 ID")
    name: Optional[str] = Field(default=None, description="工具名称")
    args: dict[str, Any] = Field(default_factory=dict, description="工具入参")


class ThinkingStep(BaseModel):
    """Agent 思考步骤

    记录节点级的执行过程（进入某个图节点、子 Agent 委派等），
    供前端展示思考链。内容做截断，避免大对象撑爆文档。
    """
    model_config = ConfigDict(extra="allow")

    node: str = Field(description="产生该步骤的图节点名")
    summary: str = Field(default="", description="步骤摘要（截断后的可读文本）")
    ts: datetime = Field(default_factory=utcnow, description="步骤产生时间（UTC）")


class Citation(BaseModel):
    """引用来源

    为联网检索类回答预留的结构化引用位：搜索引擎结果、网页内容等
    可整理为 {title, url, snippet} 落库，前端渲染为参考链接。
    """
    model_config = ConfigDict(extra="allow")

    title: str = Field(default="", description="来源标题")
    url: str = Field(default="", description="来源链接")
    snippet: str = Field(default="", description="引用片段")


class StoredMessage(BaseModel):
    """messages 数组中的单条消息

    seq 为会话内单调递增序号，从 1 开始，用于：
    - 保证消息展示顺序稳定（不依赖 ts 的精度或时钟漂移）
    - 支持「从第 N 条之后增量拉取」的分页场景

    thinking_steps / citations 是长期记忆层为后续扩展预留的字段：
    用户画像、跨会话记忆等可继续以文档内嵌结构追加，无需迁移 Schema。
    """
    model_config = ConfigDict(extra="allow")

    seq: int = Field(description="会话内消息序号，从 1 开始")
    role: MessageRole = Field(description="消息角色")
    content: str = Field(default="", description="消息正文")
    tool_calls: list[ToolCall] = Field(default_factory=list, description="工具调用记录")
    thinking_steps: list[ThinkingStep] = Field(default_factory=list, description="思考步骤")
    citations: list[Citation] = Field(default_factory=list, description="引用来源")
    ts: datetime = Field(default_factory=utcnow, description="消息产生时间（UTC）")


class SessionDocument(BaseModel):
    """完整的会话文档（MongoDB 中一行）"""
    model_config = ConfigDict(extra="allow")

    id: str = Field(description="会话 UUID，落库时映射为 _id")
    user_id: str = Field(description="所属用户 ID")
    agent_key: str = Field(default="", description="使用的 agent 标识")
    messages: list[StoredMessage] = Field(default_factory=list, description="消息列表")
    created_at: datetime = Field(default_factory=utcnow, description="创建时间（UTC）")
    updated_at: datetime = Field(default_factory=utcnow, description="最后更新时间（UTC）")

    def to_mongo(self) -> dict[str, Any]:
        """转换为可直接写入 MongoDB 的字典

        Pydantic 模型统一用 id 字段表达标识，写库时映射回 _id；
        datetime 交给 motor/BSON 自行编码，无需手动转 ISO 字符串。
        """
        return {
            "_id": self.id,
            "user_id": self.user_id,
            "agent_key": self.agent_key,
            "messages": [m.model_dump() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mongo(cls, doc: dict[str, Any]) -> "SessionDocument":
        """把 MongoDB 查询结果还原为 Pydantic 模型"""
        data = dict(doc)
        data["id"] = str(data.pop("_id"))
        return cls.model_validate(data)
