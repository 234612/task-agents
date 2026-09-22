"""会话标题工具 — 首句截取规则

spec 约定：标题取用户第一句话的前 50 字，不调用 LLM。
「首句」按常见中英文句读切分（。！？!? 换行），去掉空白后截断。
"""
import re

# 会话创建时还没有首句，先用占位标题；首轮消息落库时被真实标题替换
DEFAULT_TITLE = "新会话"

# 中英文句读：句号、问号、感叹号（含全半角）与换行
_SENTENCE_SPLIT = re.compile(r"[。！？!?\n\r]+")


def extract_title(text: str, max_chars: int = 50) -> str:
    """从用户消息中提取标题：首个非空句子，截断到 max_chars

    返回空串表示消息本身没有可用文本（如纯空白），调用方应保留占位标题。
    """
    if not text or not text.strip():
        return ""

    for sentence in _SENTENCE_SPLIT.split(text):
        cleaned = sentence.strip()
        if cleaned:
            return cleaned[:max_chars]

    # 全是句读符号的极端情况：退化为整段去空白
    return text.strip()[:max_chars]
