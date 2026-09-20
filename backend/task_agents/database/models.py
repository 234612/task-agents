"""SQLAlchemy ORM 模型定义

分层存储中 MySQL 只承担「会话元数据」职责，服务于侧边栏列表页的
排序、分页与筛选。对话正文（messages 数组）不落在 MySQL，见
task_agents/schemas/mongo.py。
"""
import enum

from sqlalchemy import BigInteger, Column, DateTime, Enum, Index, String
from sqlalchemy.orm import DeclarativeBase

from task_agents.core.timeutils import utcnow


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


class SessionStatus(str, enum.Enum):
    """会话状态

    继承 str 以便直接参与 JSON 序列化与 Pydantic 校验。
    """
    ACTIVE = "active"        # 进行中，默认状态
    ARCHIVED = "archived"    # 用户归档，不出现在默认列表
    DELETED = "deleted"      # 软删除，保留行以便审计与恢复


class ChatSession(Base):
    """会话元数据表

    写入特征：低频、强一致（依赖 ACID 事务）
    读取特征：按 (user_id, updated_at) 倒序分页，命中联合索引
    """
    __tablename__ = "chat_sessions"

    # 主键：UUID 字符串，跨存储引擎（MySQL / MongoDB / Redis）共用同一标识
    session_id = Column(
        String(36),
        primary_key=True,
        comment="会话唯一标识（UUID）",
    )

    # 用户 ID：会话隔离的边界
    user_id = Column(
        String(64),
        nullable=False,
        comment="所属用户 ID",
    )

    # 会话标题：由 LLM 依据首轮对话自动生成，生成前用占位标题
    title = Column(
        String(256),
        nullable=False,
        comment="会话标题（LLM 自动生成）",
    )

    # Agent 标识：沿用既有前端契约，如 market_researcher
    agent_key = Column(
        String(64),
        nullable=False,
        comment="使用的 agent 标识",
    )

    # 状态：默认 active；列表查询会过滤掉 deleted
    status = Column(
        Enum(SessionStatus, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=SessionStatus.ACTIVE,
        server_default=SessionStatus.ACTIVE.value,
        comment="会话状态",
    )

    # 消息条数：由聊天接口在每次落库时原子自增，避免 COUNT(*) 扫 MongoDB
    message_count = Column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
        comment="累计消息条数",
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
            "status": self.status.value if isinstance(self.status, SessionStatus) else self.status,
            "message_count": self.message_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
