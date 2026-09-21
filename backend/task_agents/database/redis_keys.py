"""Redis Key 命名规范与上下文窗口计算

命名约定：{业务域}:{数据类型}:{主键}
    memory:context:{session_id}   会话短期记忆（List，JSON 序列化的消息）
    memory:meta:{session_id}      会话上下文元信息（Hash）

设计要点：
- 统一 memory: 前缀，便于按前缀做 DB 级隔离、监控与批量清理。
- List 而非 Hash 存消息：追加写 O(1)，配合 LTRIM 定长裁剪即可实现
  「最近 N 轮」滑动窗口，读取时用 LRANGE 0 -1 一次取回。
- 所有 Key 都挂 TTL，过期自动回收，不需要定时任务清理冷会话。
"""
from typing import Final

# 业务域前缀
KEY_PREFIX: Final[str] = "memory"

# List：会话上下文消息，元素为 JSON 字符串，最新一条在尾部
CONTEXT_LIST_KEY: Final[str] = f"{KEY_PREFIX}:context:{{session_id}}"

# Hash：上下文元信息（user_id / agent_key / last_seq / updated_at）
CONTEXT_META_KEY: Final[str] = f"{KEY_PREFIX}:meta:{{session_id}}"


def context_list_key(session_id: str) -> str:
    """构建会话上下文 List 的 Key"""
    return CONTEXT_LIST_KEY.format(session_id=session_id)


def context_meta_key(session_id: str) -> str:
    """构建会话上下文元信息 Hash 的 Key"""
    return CONTEXT_META_KEY.format(session_id=session_id)


def turns_to_messages(max_turns: int) -> int:
    """把「轮数」换算为「消息条数」

    1 轮对话 = user 1 条 + assistant 1 条 = 2 条消息。
    系统消息不占轮次配额，但会计入 List 长度，因此这里对入参做下限保护。
    """
    if max_turns <= 0:
        raise ValueError("max_turns 必须为正整数")
    return max_turns * 2
