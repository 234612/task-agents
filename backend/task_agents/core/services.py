"""全局服务容器

管理所有外部客户端的生命周期（lifespan 中初始化一次，全应用复用）：

- MySQL   engine + session_factory（SQLAlchemy async）
- MongoDB motor 异步客户端（会话消息 / 长期记忆的读写）
          pymongo 同步客户端（供 MongoDBStore / LangGraph 长期记忆）
- Redis   asyncio 客户端（短期记忆滑动窗口，仓储层使用）
          同步客户端（保留给通用场景）
          LangGraph checkpointer（短期记忆）：Redis 或内存，见
          Settings.CHECKPOINT_BACKEND
- LLM     ChatOpenAI 实例

设计要点与踩坑记录：
1. AsyncRedisSaver 必须持有**自己的连接**（走 redis_url 而非共享 client）。
   共享 client 开了 decode_responses=True，而 checkpointer 序列化是
   msgpack 二进制，UTF-8 解码会直接报 UnicodeDecodeError。
   同时必须用 Async 版：langgraph 异步路径只调 a* 方法，同步 RedisSaver
   未实现，会在 aget_tuple 抛 NotImplementedError。
2. AsyncRedisSaver 的 ttl.default_ttl 单位是**分钟**（不是秒）。
2. MongoDBStore 需要同步 pymongo Collection，不能复用 motor。
   两个客户端各自带连接池，指向同一实例。
3. create_engine 是协程函数，必须 await。
4. AsyncRedisSaver / MongoDBStore 没有 close()，shutdown 里用 hasattr 防御。
5. Redis 版 checkpointer 需要 RedisJSON + RediSearch；裸 Redis 会在写
   checkpoint 时报 unknown command 'JSON.SET'，故 auto 模式先探测能力。
"""
import logging
from typing import Any, Dict

import redis
import redis.asyncio as aioredis
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.store.mongodb import MongoDBStore
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from task_agents.core.config import Settings, get_settings
from task_agents.database.engine import create_engine, create_session_factory, init_database

logger = logging.getLogger(__name__)


class ServiceContainer:
    """全局服务容器，管理所有外部客户端的生命周期"""

    def __init__(self):
        self.clients: Dict[str, Any] = {}
        self.config: Settings = get_settings()

    async def initialize(self):
        """初始化所有客户端连接（lifespan 启动阶段调用，需 await）"""

        # —— MongoDB：同步客户端给 MongoDBStore，异步 motor 给仓储层 ——
        mongo_client = MongoClient(self.config.MONGO_URI)
        self.clients["mongo_client"] = mongo_client
        motor_client = AsyncIOMotorClient(self.config.MONGO_URI)
        self.clients["motor_client"] = motor_client
        self.clients["mongo_db"] = motor_client[self.config.MONGO_DATABASE]
        logger.info("MongoDB 客户端初始化成功: database=%s", self.config.MONGO_DATABASE)

        # —— Redis：同步 + 异步两个客户端 ——
        self.clients["redis_client"] = redis.Redis.from_url(
            self.config.redis_url, decode_responses=True
        )
        self.clients["redis_async_client"] = aioredis.from_url(self.config.redis_url)
        logger.info("Redis 客户端初始化成功: db=%s", self.config.REDIS_DB)

        # —— MySQL：engine 是协程，必须 await ——
        engine = await create_engine(self.config)
        self.clients["db_engine"] = engine
        self.clients["db_session_factory"] = create_session_factory(engine)
        await init_database(engine)
        logger.info("MySQL 引擎初始化成功，表结构已就绪")

        # —— LLM ——
        self.clients["llm"] = self._create_llm()
        logger.info("LLM 初始化成功: model=%s", self.config.LLM_MODEL)

        # —— LangGraph checkpointer（短期记忆）——
        saver = await self._build_checkpointer()
        self.clients["redis_checkpointer"] = saver
        logger.info("Checkpointer（短期记忆）初始化成功: %s", type(saver).__name__)

        # —— MongoDBStore（LangGraph store，长期记忆）——
        mongo_store = MongoDBStore(
            collection=mongo_client["agent_memory"]["long_term_memories"],
        )
        self.clients["mongo_store"] = mongo_store
        logger.info("MongoDB Store（长期记忆）初始化成功")

    async def _build_checkpointer(self):
        """按 CHECKPOINT_BACKEND 选择 checkpointer，auto 时按能力探测回落

        Redis 版依赖 RedisJSON（写 checkpoint）与 RediSearch（建索引）。
        裸 Redis（如 Windows 版）两者都没有：写会报 unknown command
        'JSON.SET'，导致整轮对话在收尾阶段失败。auto 会先探测再决定。
        """
        backend = (self.config.CHECKPOINT_BACKEND or "auto").strip().lower()

        use_redis = backend == "redis"
        if backend not in ("auto", "redis", "memory"):
            logger.warning("未知 CHECKPOINT_BACKEND=%s，按 auto 处理", backend)
            backend = "auto"

        if backend == "auto":
            use_redis = await self._redis_has_json()
            if not use_redis:
                logger.warning(
                    "Redis 未加载 RedisJSON 模块，checkpointer 回落内存实现"
                    "（进程重启后 LangGraph thread 状态丢失，业务侧 Redis 上下文不受影响）；"
                    "生产环境请使用 Redis Stack / Redis 8，并把 CHECKPOINT_BACKEND 设为 redis"
                )

        if use_redis:
            saver = AsyncRedisSaver(
                redis_url=self.config.redis_url,
                ttl={
                    # 单位：分钟。60 分钟与非活跃会话的上下文 TTL 对齐
                    "default_ttl": 60,
                    "refresh_on_read": True,
                },
            )
            await self._setup_checkpointer(saver)
            return saver

        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()

    async def _redis_has_json(self) -> bool:
        """探测 Redis 是否加载了 RedisJSON 模块"""
        client = self.clients.get("redis_async_client")
        if client is None:
            return False
        try:
            modules = await client.execute_command("MODULE", "LIST")
        except Exception as e:  # noqa: BLE001 — 命令不支持/无权限都按「没有」处理
            logger.info("Redis 不支持 MODULE LIST，判定无 RedisJSON: %s", e)
            return False

        for module in modules or []:
            for field in module:
                if str(field).lower().strip("'\"") in ("json", "rejson"):
                    return True
        return False

    async def _setup_checkpointer(self, saver: AsyncRedisSaver) -> None:
        """尽力完成 checkpointer 的索引初始化。

        langgraph-redis 的部分版本要求显式 setup() 创建搜索索引；
        无该协程或创建失败（索引已存在）都不阻断启动。
        """
        setup = getattr(saver, "setup", None)
        if setup is None:
            return
        try:
            result = setup()
            # 兼容 setup 是同步方法或协程两种形态
            if hasattr(result, "__await__"):
                await result
        except Exception as e:
            logger.warning("AsyncRedisSaver setup 未成功（索引可能已存在，不影响基本读写）: %s", e)

    def get_client(self, name: str) -> Any:
        """获取已注册的客户端实例"""
        if name not in self.clients:
            raise ValueError(f"Service '{name}' is not registered in the container.")
        return self.clients[name]

    async def shutdown(self):
        """优雅关闭所有客户端连接"""
        logger.info("开始关闭服务容器中的客户端连接...")

        # RedisSaver / MongoDBStore 不提供 close()，用 hasattr 防御，
        # 避免关停阶段抛 AttributeError 掩盖其他清理。
        for key in ("redis_checkpointer", "mongo_store"):
            client = self.clients.get(key)
            closer = getattr(client, "close", None)
            if callable(closer):
                try:
                    result = closer()
                    if hasattr(result, "__await__"):
                        await result
                except Exception as e:
                    logger.warning("关闭 %s 失败: %s", key, e)

        mongo = self.clients.get("mongo_client")
        if mongo:
            mongo.close()

        motor_client = self.clients.get("motor_client")
        if motor_client:
            motor_client.close()

        redis_client = self.clients.get("redis_client")
        if redis_client:
            redis_client.close()

        redis_async = self.clients.get("redis_async_client")
        if redis_async:
            await redis_async.aclose()

        engine = self.clients.get("db_engine")
        if engine:
            await engine.dispose()

        logger.info("所有外部服务连接已关闭。")

    def _create_llm(self) -> BaseChatModel:
        """根据配置创建 LLM 模型实例"""
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=self.config.LLM_MODEL,
            api_key=self.config.LLM_API_KEY,
            base_url=self.config.LLM_BASE_URL,
            temperature=self.config.LLM_TEMPERATURE,
        )


service_container = ServiceContainer()
