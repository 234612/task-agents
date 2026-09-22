"""SQLAlchemy ORM 模型定义

分层存储中 MySQL 只承担「会话业务」职责，服务于左侧会话列表的
排序、分页与权限校验。对话正文（messages 数组）不落在 MySQL，见
task_agents/schemas/mongo.py。

存储分工（对齐三层记忆架构）：
- MySQL   chat_sessions 会话元数据（业务层）
- Redis   短期记忆（当前会话上下文滑动窗口 + LangGraph checkpointer）
- MongoDB 长期记忆（消息内容、思考步骤、工具调用、引用来源）
"""
from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase

from task_agents.core.timeutils import utcnow


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


class SessionStatusValue:
    """会话状态常量

    使用整型而非字符串枚举：与业务层惯例（1-活跃 / 2-已结束）对齐，
    索引存储更紧凑，前端无需再做枚举映射。

    刻意不做软删除：status=2 表示「已结束」，会话仍出现在列表里
    （由前端灰显等），只有数据治理任务才会物理清理。
    """
    ACTIVE = 1      # 活跃：默认状态
    ENDED = 2       # 已结束：用户主动结束会话，停止追加消息

    CHOICES = (ACTIVE, ENDED)

    @classmethod
    def label(cls, value: int) -> str:
        return {cls.ACTIVE: "active", cls.ENDED: "ended"}.get(value, "unknown")


class ChatSession(Base):
    """会话元数据表

    写入特征：低频、强一致（依赖 ACID 事务）
    读取特征：按 (user_id, updated_at) 倒序分页，命中联合索引
    """
    __tablename__ = "chat_sessions"

    # 主键：UUID v4（36 字符），跨存储引擎（MySQL / MongoDB / Redis）共用同一标识
    session_id = Column(
        String(36),
        primary_key=True,
        comment="会话唯一标识（UUID v4，36 字符）",
    )

    # 用户 ID：会话隔离的边界
    user_id = Column(
        String(64),
        nullable=False,
        comment="所属用户 ID",
    )

    # 会话标题：首句前 50 字（服务端截取，不调 LLM）
    title = Column(
        String(256),
        nullable=False,
        comment="会话标题（首句前 50 字）",
    )

    # Agent 标识：沿用既有前端契约，如 market_researcher
    agent_key = Column(
        String(64),
        nullable=False,
        comment="使用的 agent 标识",
    )

    # 状态：1-活跃 2-已结束（Integer + 应用层常量校验，见 SessionStatusValue）
    status = Column(
        Integer,
        nullable=False,
        default=SessionStatusValue.ACTIVE,
        server_default=str(SessionStatusValue.ACTIVE),
        comment="会话状态：1-活跃 2-已结束",
    )

    # 消息条数：由聊天接口在每次落库时原子自增，避免 COUNT(*) 扫 MongoDB
    message_count = Column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
        comment="累计消息条数（冗余字段，避免 COUNT）",
    )

    # 时间戳：updated_at 即「最后活跃时间」，是列表排序依据
    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        comment="创建时间",
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        comment="最后活跃时间",
    )

    # 联合索引：覆盖「按用户查会话 + 按活跃时间倒序分页」这一主查询路径。
    # 列顺序必须是 (user_id, updated_at)——等值列在前、排序列在后，
    # 否则无法用于消除 filesort。
    __table_args__ = (
        Index(
            "idx_chat_sessions_user_updated",
            "user_id",
            "updated_at",
        ),
    )

    def to_dict(self) -> dict:
        """转换为字典格式（时间序列化为 ISO 8601）"""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "title": self.title,
            "agent_key": self.agent_key,
            "status": int(self.status),
            "message_count": self.message_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
