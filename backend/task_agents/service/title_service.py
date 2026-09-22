"""会话标题生成器 — 首轮对话后由 LLM 归纳标题

设计为后台任务调用，不阻塞 POST /api/chat 的响应。LLM 不可用或超时都
只记日志并返回 None，会话保留占位标题「新会话」，不影响主流程。
"""
import logging
from typing import Optional

from task_agents.core.config import Settings

logger = logging.getLogger(__name__)

# 标题长度上限：与 MySQL title 列宽（256）保持安全余量
MAX_TITLE_LENGTH = 30

TITLE_SYSTEM_PROMPT = (
    "你是一个会话标题生成器。根据用户的第一句话，生成一个简洁的中文标题。\n"
    f"要求：不超过 {MAX_TITLE_LENGTH} 个字；只输出标题本身；"
    "不要引号、不要标点结尾、不要任何解释。"
)


async def build_title_generator(settings: Settings):
    """构造标题生成器协程

    返回一个可调用对象：async (user_content) -> Optional[str]。
    LLM 客户端在此处惰性创建，避免未配置 API Key 时影响应用启动。
    """
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        # 标题生成是确定性任务，温度调低以减少发散
        temperature=0.2,
        timeout=15,
    )

    async def generate_title(user_content: str) -> Optional[str]:
        """调用 LLM 归纳标题"""
        try:
            result = await llm.ainvoke([
                {"role": "system", "content": TITLE_SYSTEM_PROMPT},
                {"role": "user", "content": user_content[:500]},
            ])
        except Exception as e:
            logger.warning("标题生成调用 LLM 失败: %s", e)
            return None

        raw = getattr(result, "content", None)
        if not isinstance(raw, str) or not raw.strip():
            return None

        title = _clean_title(raw.strip())
        return title or None

    return generate_title


def _clean_title(raw: str) -> str:
    """清洗 LLM 输出：去引号、去首尾标点、截断长度"""
    title = raw.strip().strip('"\'“”「」《》').strip()
    # LLM 偶尔会带上「标题：」这类前缀
    for prefix in ("标题：", "标题:"):
        if title.startswith(prefix):
            title = title[len(prefix):].strip()
            break
    # 只取首行，避免 LLM 输出多行解释
    title = title.splitlines()[0].strip() if title.splitlines() else ""
    return title[:MAX_TITLE_LENGTH]


__all__ = ["MAX_TITLE_LENGTH", "build_title_generator"]
