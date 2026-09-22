# agents/content_writer_engine/agent.py
"""内容创作引擎

与 market_researcher 平级的独立主 Agent：专注"把想法变成成稿"。
三个子 Agent 覆盖写作的典型工序：大纲 → 撰写 → 审校。
本引擎**不持有任何工具**：写作是纯语言任务，联网与沙箱对它没有增益。
"""

from pathlib import Path

from deepagents import create_deep_agent, SubAgent
from langchain_core.language_models import BaseChatModel

from task_agents.agent.content_writer_engine.prompts import CONTENT_WRITER_PROMPT
from task_agents.agent.leaf_rules import LEAF_AGENT_RULES


def _load_prompt(name: str) -> str:
    """从 subagents/{name}/prompt.md 加载提示词（并追加叶子 Agent 约束）"""
    prompt_path = Path(__file__).parent / "subagents" / name / "prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"找不到子 Agent 提示词: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8") + LEAF_AGENT_RULES


def create_content_writer(model: BaseChatModel, backend=None, store=None, memory=None, checkpointer=None):
    subagents: list[SubAgent] = [
        {
            "name": "outliner",
            "description": (
                "当用户只给了模糊主题、需要先定结构时调用：确定目标读者、"
                "核心论点、章节骨架与每节要点。产出的是大纲，不是正文。"
            ),
            "system_prompt": _load_prompt("outliner"),
            "tools": [],
        },
        {
            "name": "writer",
            "description": (
                "当大纲已定、需要产出正文时调用。按大纲逐节撰写，"
                "保持语气与篇幅一致，一次写完不要中途停。"
            ),
            "system_prompt": _load_prompt("writer"),
            "tools": [],
        },
        {
            "name": "editor",
            "description": (
                "当成稿已存在、需要润色把关时调用：事实与逻辑一致性、"
                "冗余删减、语病与格式修正、语气统一。输出修改说明与定稿。"
            ),
            "system_prompt": _load_prompt("editor"),
            "tools": [],
        },
    ]

    agent = create_deep_agent(
        model=model,
        tools=[],
        system_prompt=CONTENT_WRITER_PROMPT,
        subagents=subagents,
        backend=backend,
        store=store,
        memory=memory,
        checkpointer=checkpointer,
        debug=False,
    )

    return agent
