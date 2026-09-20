"""SQLAlchemy ORM 模型定义"""
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Index
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


class Session(Base):
    """会话表模型
    
    用于存储用户的聊天会话元数据
    """
    __tablename__ = "sessions"
    
    # 主键：UUID 格式的会话 ID
    id = Column(String(36), primary_key=True, comment="会话唯一标识（UUID）")
    
    # 用户 ID：用于会话隔离
    user_id = Column(String(64), nullable=False, index=True, comment="所属用户 ID")
    
    # 会话标题：取自用户第一条消息的前 50 个字符
    title = Column(String(256), nullable=False, comment="会话标题")
    
    # Agent Key：使用的 agent 标识（如 market_researcher）
    agent_key = Column(String(64), nullable=False, comment="使用的 agent 标识")
    
    # 时间戳
    last_message_at = Column(
        DateTime, 
        nullable=False, 
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        comment="最后消息时间"
    )
    created_at = Column(
        DateTime, 
        nullable=False, 
        default=datetime.utcnow,
        comment="创建时间"
    )
    
    # 索引：优化按用户查询会话列表的性能
    __table_args__ = (
        Index('idx_user_id_last_message', 'user_id', 'last_message_at'),
    )
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "agent_key": self.agent_key,
            "last_message_at": self.last_message_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }
