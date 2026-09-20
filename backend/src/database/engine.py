"""MySQL 数据库引擎和会话工厂"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from ..config import Settings
from .models import Base


def create_engine(settings: Settings):
    """创建 MySQL 异步引擎

    pool_pre_ping=True: 连接池健康检查，自动重连断开的连接
    pool_recycle=3600: 每小时回收连接，避免 MySQL 8 小时超时问题
    """
    return create_async_engine(
        settings.mysql_dsn,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=settings.APP_DEBUG,
    )


def create_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """创建异步会话工厂"""
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def init_database(engine):
    """初始化数据库：创建所有表"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

