"""Redis 客户端

使用 redis.asyncio。客户端内部自带连接池，应在应用生命周期内创建一次并
复用，关闭时调用 aclose() 释放。

decode_responses 保持默认 False（返回 bytes），因为仓储层已显式处理
bytes/str 两种形态；这样在写入端可以用 ensure_ascii=False 精确控制编码。
"""
import logging

from redis.asyncio import ConnectionPool, Redis

from task_agents.core.config import Settings

logger = logging.getLogger(__name__)


def create_redis_client(settings: Settings) -> Redis:
    """创建 Redis 异步客户端

    socket_timeout / socket_connect_timeout：Redis 属于加速层，故障时应
    快速失败并降级到 MongoDB，不能让请求长时间挂住。
    """
    pool = ConnectionPool.from_url(
        settings.redis_url,
        max_connections=50,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
        health_check_interval=30,
    )
    client = Redis(connection_pool=pool)
    logger.info("Redis 客户端已创建: db=%s", settings.REDIS_DB)
    return client


async def ping_redis(client: Redis) -> bool:
    """连通性探测，用于启动阶段与健康检查"""
    try:
        return bool(await client.ping())
    except Exception as e:
        logger.error("Redis 连通性探测失败: %s", e)
        return False
