"""MongoDB 客户端与索引初始化

使用 motor 的 AsyncIOMotorClient。该客户端内部自带连接池，应当在应用
生命周期内创建一次并复用，关闭时调用 close() 释放。
"""
import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from task_agents.core.config import Settings

logger = logging.getLogger(__name__)


def create_mongo_client(settings: Settings) -> AsyncIOMotorClient:
    """创建 MongoDB 异步客户端

    serverSelectionTimeoutMS=5000：连接不可用时 5 秒内快速失败，
    避免默认 30 秒阻塞请求线程。
    """
    client = AsyncIOMotorClient(
        settings.MONGO_URI,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        # 对话历史属于高频追加写，放宽连接池上限
        maxPoolSize=100,
        minPoolSize=10,
    )
    logger.info("MongoDB 客户端已创建: database=%s", settings.MONGO_DATABASE)
    return client


def get_mongo_database(client: AsyncIOMotorClient, settings: Settings) -> AsyncIOMotorDatabase:
    """获取业务数据库句柄"""
    return client[settings.MONGO_DATABASE]


async def ping_mongo(client: AsyncIOMotorClient) -> bool:
    """连通性探测，用于启动阶段与健康检查"""
    try:
        await client.admin.command("ping")
        return True
    except Exception as e:
        logger.error("MongoDB 连通性探测失败: %s", e)
        return False
