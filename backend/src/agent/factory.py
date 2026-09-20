"""
Agent 工厂模块
负责创建和缓存所有 Agent 实例。
"""

import logging
import time
from typing import Dict, Any

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import MemorySaver

from ..config import Settings
from .market_researcher_engine.agent import create_market_researcher

logger = logging.getLogger(__name__)

# ==================== Agent 注册表 ====================
_REGISTRY: Dict[str, Any] = {}
_SETTINGS: Settings = None


def _create_llm(settings: Settings) -> BaseChatModel:
    """
    根据配置创建 LLM 模型实例
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        temperature=settings.LLM_TEMPERATURE,
    )


def build_agents(settings: Settings) -> Dict[str, Any]:
    """
    装配所有主 Agent 实例，返回一个字典。
    - Key: agent 标识（如 "market_researcher"），前端通过此 key 请求对应 agent
    - Value: 编译好的 Agent 实例 (CompiledStateGraph)

    使用全局缓存避免重复创建。
    """
    global _SETTINGS
    _SETTINGS = settings
    
    if _REGISTRY:
        return _REGISTRY

    logger.info("开始装配 Agent 引擎...")

    # 1. 创建 LLM 实例（从配置读取）
    llm = _create_llm(settings)

    # 2. 创建长期记忆 checkpointer（基于内存）
    checkpointer = MemorySaver()

    try:
        # 3. 创建市场调研 Agent
        market_agent = create_market_researcher(
            model=llm,
            backend=None,
            checkpointer=checkpointer,
        )
        _REGISTRY["market_researcher"] = market_agent

        # 后续新增 Agent 在此处注册：
        # from .other_engine.agent import create_other_agent
        # _REGISTRY["other_agent"] = create_other_agent(model=llm, backend=None, checkpointer=checkpointer)

        logger.info(f"成功装配 {len(_REGISTRY)} 个 Agent: {list(_REGISTRY.keys())}")

    except Exception as e:
        logger.exception("Agent 装配失败: %s", e)

    return _REGISTRY


def get_llm_info() -> Dict[str, Any]:
    """
    获取当前 LLM 配置信息，用于健康检查/校验。
    """
    if _SETTINGS is None:
        return {
            "model": "未初始化",
            "provider": "未初始化",
            "api_key_configured": False,
        }
    
    return {
        "model": _SETTINGS.LLM_MODEL,
        "base_url": _SETTINGS.LLM_BASE_URL,
        "provider": "阿里云通义千问" if "dashscope" in _SETTINGS.LLM_BASE_URL else "OpenAI 兼容接口",
        "api_key_configured": bool(_SETTINGS.LLM_API_KEY),
    }


def verify_llm() -> Dict[str, Any]:
    """
    实际调用 LLM 验证连通性。
    返回 {"ok": bool, "message": str, "latency_ms": float}
    """
    if _SETTINGS is None:
        return {
            "ok": False,
            "message": "配置未初始化",
            "latency_ms": None,
        }
    
    try:
        llm = _create_llm(_SETTINGS)
        start = time.time()
        result = llm.invoke("ping")
        latency = round((time.time() - start) * 1000, 1)
        return {
            "ok": True,
            "message": f"LLM 连通正常，模型: {_SETTINGS.LLM_MODEL}",
            "latency_ms": latency,
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"LLM 连通失败: {type(e).__name__}: {e}",
            "latency_ms": None,
        }


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
