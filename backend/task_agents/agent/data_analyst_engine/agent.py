# agents/data_analyst_engine/agent.py
"""数据分析引擎

与 market_researcher 平级的独立主 Agent：专注"从数据里得到可信结论"。
三个子 Agent 覆盖分析链路：取数与清洗 → 统计建模 → 图表呈现。
全部复用共享沙箱（Python 执行），因此每一步都要求**真实跑出结果**再汇报。

注意：沙箱执行的是后端虚拟环境里的 Python，pandas / matplotlib 等第三方库
**未必已安装**。子 Agent 被要求先探测依赖，缺失时如实报错而不是假装出图。
"""

from pathlib import Path

from deepagents import create_deep_agent, SubAgent
from langchain_core.language_models import BaseChatModel

from task_agents.agent.data_analyst_engine.prompts import DATA_ANALYST_PROMPT
from task_agents.agent.leaf_rules import LEAF_AGENT_RULES
from task_agents.agent.shared_tools import SANDBOX_TOOLS


def _load_prompt(name: str) -> str:
    """从 subagents/{name}/prompt.md 加载提示词（并追加叶子 Agent 约束）"""
    prompt_path = Path(__file__).parent / "subagents" / name / "prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"找不到子 Agent 提示词: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8") + LEAF_AGENT_RULES


def create_data_analyst(model: BaseChatModel, backend=None, store=None, memory=None, checkpointer=None,
                        middleware=None):
    subagents: list[SubAgent] = [
        {
            "name": "data_loader",
            "description": (
                "当任务开始时需要先拿到数据并整理干净时调用：读取 CSV/JSON/Excel、"
                "对齐字段名、处理缺失值与异常值、输出可直接分析的数据集与数据字典。"
            ),
            "system_prompt": _load_prompt("data_loader"),
            "tools": SANDBOX_TOOLS,
        },
        {
            "name": "statistician",
            "description": (
                "当数据已就绪、需要算指标、做分组对比、相关性/趋势/假设检验等"
                "定量结论时调用。结论必须来自实际计算，不允许估算。"
            ),
            "system_prompt": _load_prompt("statistician"),
            "tools": SANDBOX_TOOLS,
        },
        {
            "name": "visualizer",
            "description": (
                "当需要把结论画成图表时调用：选图型、出图并落盘，"
                "给出图表路径与读图要点。"
            ),
            "system_prompt": _load_prompt("visualizer"),
            "tools": SANDBOX_TOOLS,
        },
    ]

    agent = create_deep_agent(
        model=model,
        tools=[],
        system_prompt=DATA_ANALYST_PROMPT,
        subagents=subagents,
        backend=backend,
        store=store,
        memory=memory,
        checkpointer=checkpointer,
        middleware=middleware or (),
        debug=False,
    )

    return agent
