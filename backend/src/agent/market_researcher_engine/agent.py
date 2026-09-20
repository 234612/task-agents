# agents/market_researcher_engine/agent.py

from pathlib import Path
from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel

from .tools.web_research import WEB_RESEARCH_TOOLS
from .prompts import MARKET_RESEARCHER_PROMPT

def _load_prompt(name: str) -> str:
    """从 subagents/{name}/prompt.md 加载提示词"""
    prompt_path = Path(__file__).parent / "subagents" / name / "prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"找不到子 Agent 提示词: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def create_market_researcher(model: BaseChatModel, backend=None, checkpointer=None):
    base_dir = Path(__file__).parent

    # ==================== 子 Agent 定义 ====================
    subagents = [
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
    ]

    # ==================== 主 Agent ====================
    agent = create_deep_agent(
        model=model,
        tools=[],  # 主 Agent 不持有工具，全部委派
        system_prompt=MARKET_RESEARCHER_PROMPT,
        subagents=subagents,
        backend=backend,
        checkpointer=checkpointer,
        debug=False,
    )

    return agent