"""
Agent 工厂模块
负责创建和缓存所有 Agent 实例。
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, Any
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from task_agents.agent.code_engineer_engine.agent import create_code_engineer
from task_agents.agent.content_writer_engine.agent import create_content_writer
from task_agents.agent.data_analyst_engine.agent import create_data_analyst
from task_agents.agent.market_researcher_engine.agent import create_market_researcher
from task_agents.agent.sandbox_backend import RestrictedSandboxBackend
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
        # 所有引擎共用同一套基础设施：LLM / checkpointer（短期记忆）/ store（长期记忆）。
        # 差异只在 system_prompt 与子 Agent 组合，因此逐个装配即可。
        # backend 决定 deepagents 内置的 ls/read_file/write_file/execute 落在哪。
        # 不用 StateBackend()：它没有 execute（模型一调就报错并陷入重试死循环），
        # 且文件系统是内存虚拟的、写的文件根本不落盘。
        # 也不用官方 LocalShellBackend：它 shell=True 无隔离，能读 .env 里的密钥。
        # RestrictedSandboxBackend = 真实 workspace 目录（virtual_mode 封锁逃逸）
        # + 仅允许 Python 的受限执行。详见该模块 docstring。
        common = dict(
            model=llm,
            checkpointer=checkpointer,
            store=store,
            memory=["/memories/preferences.md"],  # ← 虚拟路径
            backend=RestrictedSandboxBackend(),
        )

        _REGISTRY["market_researcher"] = create_market_researcher(**common)
        _REGISTRY["code_engineer"] = create_code_engineer(**common)
        _REGISTRY["content_writer"] = create_content_writer(**common)
        _REGISTRY["data_analyst"] = create_data_analyst(**common)

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
