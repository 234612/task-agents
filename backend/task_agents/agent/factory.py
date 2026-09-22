"""
Agent 工厂模块
负责创建和缓存所有 Agent 实例。
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, Any
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from task_agents.agent.market_researcher_engine.agent import create_market_researcher
from task_agents.core.services import service_container as container

logger = logging.getLogger(__name__)

# ==================== Agent 注册表 ====================
_REGISTRY: Dict[str, Any] = {}


@dataclass
class UserContext:
    user_id: str

def build_agents() -> Dict[str, Any]:
    """
    装配所有主 Agent 实例，返回一个字典。
    - Key: agent 标识（如 "market_researcher"），前端通过此 key 请求对应 agent
    - Value: 编译好的 Agent 实例 (CompiledStateGraph)

    使用全局缓存避免重复创建。
    """
    if _REGISTRY:
        return _REGISTRY

    logger.info("开始装配 Agent 引擎...")

    llm = container.get_client('llm')
    checkpointer = container.get_client('redis_checkpointer')
    store = container.get_client("mongo_store")

    try:
        market_agent = create_market_researcher(
            model=llm,
            checkpointer=checkpointer,
            store=store,
            memory=["/memories/preferences.md"],  # ← 虚拟路径
            backend=StateBackend()
        )
        _REGISTRY["market_researcher"] = market_agent

        logger.info(f"成功装配 {len(_REGISTRY)} 个 Agent: {list(_REGISTRY.keys())}")

    except Exception as e:
        logger.exception("Agent 装配失败: %s", e)

    return _REGISTRY

def get_agent_by_key(key: str) -> Any:
    """
    根据 agent key 获取对应的 Agent 实例。
    如果注册表为空，会先触发 build_agents()。
    """
    if not _REGISTRY:
        raise KeyError(f"Agent 注册表未初始化。请先调用 build_agents()。")

    if key not in _REGISTRY:
        available = list(_REGISTRY.keys())
        raise KeyError(
            f"Agent '{key}' 未找到。可用的 Agent: {available}"
        )
    return _REGISTRY[key]
