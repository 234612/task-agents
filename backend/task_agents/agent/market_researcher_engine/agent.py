from pathlib import Path

from deepagents import create_deep_agent, SubAgent
from langchain_core.language_models import BaseChatModel

from task_agents.agent.leaf_rules import LEAF_AGENT_RULES
from task_agents.agent.market_researcher_engine.prompts import MARKET_RESEARCHER_PROMPT
from task_agents.agent.market_researcher_engine.tools.coding_tools import CODING_TOOLS
from task_agents.agent.market_researcher_engine.tools.web_research import WEB_RESEARCH_TOOLS


def _load_prompt(name: str) -> str:
    """从 subagents/{name}/prompt.md 加载提示词（并追加叶子 Agent 约束）"""
    prompt_path = Path(__file__).parent / "subagents" / name / "prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"找不到子 Agent 提示词: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8") + LEAF_AGENT_RULES


def create_market_researcher(model: BaseChatModel, backend=None, store=None, memory=None, checkpointer=None,
                             middleware=None):
    base_dir = Path(__file__).parent

    # ==================== 子 Agent 定义 ====================
    subagents: list[SubAgent] = [
        {
            "name": "data_collector",
            "description": (
                "当任务涉及互联网信息搜集、竞品数据抓取、"
                "用户评论挖掘或特定网页内容提取时调用。"
            ),
            "system_prompt": _load_prompt("data_collector"),
            "tools": WEB_RESEARCH_TOOLS,
        },
        {
            "name": "analyst",
            "description": (
                "当需要对已有数据进行深度分析、SWOT 评估、"
                "趋势预测或撰写商业报告时调用。"
            ),
            "system_prompt": _load_prompt("analyst"),
            "tools": [],
        },
        {
            "name": "programmer",
            "description": (
                "当任务涉及编写代码、修复 bug、重构、算法实现、"
                "脚本编写或需要实际运行代码验证结果时调用。"
                "具备受限的文件读写与 Python 执行能力，产出的代码经过真实运行验证。"
            ),
            "system_prompt": _load_prompt("programmer"),
            "tools": CODING_TOOLS,
        },
    ]

    # ==================== 主 Agent ====================
    agent = create_deep_agent(
        model=model,
        tools=[],
        system_prompt=MARKET_RESEARCHER_PROMPT,
        subagents=subagents,
        backend=backend,
        store=store,
        memory=memory,
        checkpointer=checkpointer,
        middleware=middleware or (),
        debug=False,
    )

    return agent
