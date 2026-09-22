# backend/task_agents/core/services.py
import logging
from typing import Dict, Any

import redis
from langchain_core.language_models import BaseChatModel
from pymongo import MongoClient
from langgraph.checkpoint.redis import RedisSaver
from langgraph.store.mongodb import MongoDBStore
from task_agents.database.engine import create_engine, create_session_factory  # 假设你用了 SQLAlchemy
from task_agents.core.config import get_settings, Settings

logger = logging.getLogger(__name__)


class ServiceContainer:
    """全局服务容器，管理所有外部客户端的生命周期"""

    def __init__(self):
        self.clients: Dict[str, Any] = {}
        self.config:Settings = get_settings()

    def initialize(self):
        """初始化所有客户端连接"""

        mongo_uri = self.config.MONGO_URI
        mongo_client = MongoClient(mongo_uri)
        self.clients["mongo_client"] = mongo_client
        logger.info("MongoDB 客户端初始化成功")

        redis_uri = self.config.redis_url
        redis_client = redis.Redis.from_url(redis_uri, decode_responses=True)
        self.clients["redis_client"] = redis_client
        logger.info("Redis 客户端初始化成功")

        engine = create_engine(self.config)
        self.clients["db_engine"] = engine
        self.clients["db_session_factory"] = create_session_factory(engine)
        logger.info("mysql 客户端初始化成功")

        self.clients['llm'] = self._create_llm()
        logger.info("llm 初始化成功")

        redis_saver = RedisSaver(
            redis_client=redis_client,
            ttl={
                "default_ttl": 3600,
                "refresh_on_read": True,
            }
        )
        self.clients["redis_checkpointer"] = redis_saver
        logger.info("Redis checkpointer（短期记忆）初始化成功")

        mongo_store = MongoDBStore(
            collection=mongo_client["agent_memory"]["long_term_memories"],
        )
        self.clients["mongo_store"] = mongo_store
        logger.info("MongoDB Store（长期记忆）初始化成功")

    def get_client(self, name: str) -> Any:
        """获取已注册的客户端实例"""
        if name not in self.clients:
            raise ValueError(f"Service '{name}' is not registered in the container.")
        return self.clients[name]

    async def shutdown(self):
        """优雅关闭所有客户端连接"""
        logger.info("开始关闭服务容器中的客户端连接...")

        redis_saver = self.clients.get("redis_checkpointer")
        if redis_saver:
            redis_saver.close()


        mongo_store = self.clients.get("mongo_store")
        if mongo_store:
            mongo_store.close()


        mongo = self.clients.get("mongo_client")
        if mongo:
            mongo.close()


        redis_client = self.clients.get("redis_client")
        if redis_client:
            redis_client.close()

        engine = self.clients.get("db_engine")
        if engine:
            await engine.dispose()
        logger.info("所有外部服务连接已关闭。")

    def _create_llm(self) -> BaseChatModel:
        """
        根据配置创建 LLM 模型实例
        """
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=self.config.LLM_MODEL,
            api_key=self.config.LLM_API_KEY,
            base_url=self.config.LLM_BASE_URL,
            temperature=self.config.LLM_TEMPERATURE,
        )

service_container = ServiceContainer()
