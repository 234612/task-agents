# agents/code_engineer_engine/agent.py
"""软件开发引擎

与 market_researcher 平级的独立主 Agent：专注"把需求变成能跑的代码"。
三个子 Agent 覆盖软件的典型工序：架构设计 → 编码实现 → 测试验证。
"""

from pathlib import Path

from deepagents import create_deep_agent, SubAgent
from langchain_core.language_models import BaseChatModel

from task_agents.agent.code_engineer_engine.prompts import CODE_ENGINEER_PROMPT
from task_agents.agent.leaf_rules import LEAF_AGENT_RULES
from task_agents.agent.shared_tools import SANDBOX_TOOLS


def _load_prompt(name: str) -> str:
    """从 subagents/{name}/prompt.md 加载提示词（并追加叶子 Agent 约束）"""
    prompt_path = Path(__file__).parent / "subagents" / name / "prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"找不到子 Agent 提示词: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8") + LEAF_AGENT_RULES


def create_code_engineer(model: BaseChatModel, backend=None, store=None, memory=None, checkpointer=None):
    subagents: list[SubAgent] = [
        {
            "name": "architect",
            "description": (
                "当需求还很模糊、需要先拆解模块、设计接口/数据模型/技术选型，"
                "或要在多种实现方案之间做权衡时调用。产出的是设计方案，不是代码。"
            ),
            "system_prompt": _load_prompt("architect"),
            "tools": [],
        },
        {
            "name": "coder",
            "description": (
                "当需要真正写出或修改代码时调用：新功能实现、重构、修复 bug、"
                "写脚本。具备受限的文件读写与 Python 执行能力，产出的代码经过真实运行验证。"
            ),
            "system_prompt": _load_prompt("coder"),
            "tools": SANDBOX_TOOLS,
        },
        {
            "name": "tester",
            "description": (
                "当代码已存在、需要验证正确性时调用：设计测试用例、边界与异常场景，"
                "实际运行并报告失败原因。修复仍交给 coder。"
            ),
            "system_prompt": _load_prompt("tester"),
            "tools": SANDBOX_TOOLS,
        },
    ]

    agent = create_deep_agent(
        model=model,
        tools=[],  # 主 Agent 不持有工具，全部委派
        system_prompt=CODE_ENGINEER_PROMPT,
        subagents=subagents,
        backend=backend,
        store=store,
        memory=memory,
        checkpointer=checkpointer,
        debug=False,
    )

    return agent
