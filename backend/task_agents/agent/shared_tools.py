"""
共享工具转发层

背景：工具实现目前还放在 market_researcher_engine/tools/ 下，但它们是
**通用能力**（Python 沙箱、网页正文抓取），不应只属于市场调研引擎。若让新引擎
直接 `from ...market_researcher_engine.tools...` 会形成跨引擎依赖，语义也很怪。

因此这里做一层薄转发：新引擎一律从本模块取工具。将来把物理文件抽到
`agent/shared/tools/` 时，只需改这一个文件，各引擎不用动。

导出：
- SANDBOX_TOOLS：Python 沙箱（列目录 / 读写文本文件 / 执行代码），受
  `config.py` 的 CODER_* 配置约束，详见 coding_tools 模块的安全边界说明。
- WEB_TOOLS：网页正文抓取（注意：目前**没有**真正的搜索工具，见下）。
"""

from task_agents.agent.market_researcher_engine.tools.coding_tools import CODING_TOOLS
from task_agents.agent.market_researcher_engine.tools.web_research import WEB_RESEARCH_TOOLS

# 语义化别名：新引擎按「能力」取用，而不是按「它属于哪个引擎」取用
SANDBOX_TOOLS = CODING_TOOLS
WEB_TOOLS = WEB_RESEARCH_TOOLS

__all__ = ["SANDBOX_TOOLS", "WEB_TOOLS", "CODING_TOOLS", "WEB_RESEARCH_TOOLS"]
