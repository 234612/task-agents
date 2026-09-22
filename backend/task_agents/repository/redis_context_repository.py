"""Redis 会话上下文仓储（短期记忆）

使用 redis.asyncio。Key 规范见 task_agents/database/redis_keys.py。

数据结构选型：List 存消息、Hash 存元信息。
- List：RPUSH 追加 O(1)，LTRIM 定长裁剪实现「最近 N 轮」滑动窗口，
  LRANGE 0 -1 一次取回全部上下文，天然适配 Prompt 注入场景。
- Hash：记录 last_seq，用于给新消息分配全局单调递增的 seq（MongoDB
  是权威存储，Redis 命中时可省去一次 Mongo 往返）。

所有 Key 都在写入时刷新 TTL，冷会话到期自动回收。

容错策略：Redis 属于「可重建的加速层」，故障时不应阻断聊天主流程。
本仓储只做原样异常抛出，由 Service 层决定是否降级（记日志后继续）。
"""
import json
import logging
from typing import Any, Optional

from redis.asyncio import Redis

from task_agents.core.timeutils import utcnow
from task_agents.database.redis_keys import context_list_key, context_meta_key
from task_agents.schemas.mongo import StoredMessage

logger = logging.getLogger(__name__)


class RedisContextRepository:
    """会话短期记忆仓储"""

    def __init__(
        self,
        client: Redis,
        max_messages: int,
        ttl_seconds: int,
    ):
        """
        Args:
            client: redis.asyncio 客户端
            max_messages: List 最大长度（已由 turns_to_messages 换算）
            ttl_seconds: 上下文过期时间（秒）
        """
        if max_messages <= 0:
            raise ValueError("max_messages 必须为正整数")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须为正整数")

        self._client = client
        self._max_messages = max_messages
        self._ttl = ttl_seconds

    # ==================== 查询 ====================

    async def get_context(self, session_id: str) -> list[StoredMessage]:
        """读取会话上下文（最近 N 条消息，按时间正序）

        Key 不存在或已过期时返回空列表，调用方需自行回源 MongoDB。
        """
        key = context_list_key(session_id)
        raw_items = await self._client.lrange(key, 0, -1)

        messages: list[StoredMessage] = []
        for raw in raw_items:
            parsed = self._decode(raw)
            if parsed is None:
                continue
            try:
                messages.append(StoredMessage.model_validate(parsed))
            except Exception:
                # 单条脏数据不应毁掉整个上下文，跳过并告警
                logger.warning("上下文消息反序列化失败，已跳过: session_id=%s", session_id)

        return messages

    async def get_last_seq(self, session_id: str) -> Optional[int]:
        """读取上下文中最后一条消息的 seq

        返回 None 表示 Key 不存在（未初始化或已过期），此时应回源 MongoDB 计算。
        """
        meta = await self._client.hgetall(context_meta_key(session_id))
        if not meta:
            return None

        raw_seq = self._decode_field(meta, "last_seq")
        if raw_seq is None:
            return None
        try:
            return int(raw_seq)
        except (TypeError, ValueError):
            logger.warning("last_seq 非法: session_id=%s value=%r", session_id, raw_seq)
            return None

    async def allocate_seq(self, session_id: str, seed: int = 0) -> int:
        """原子分配下一条消息的 seq

        实现要点：
        - HSETNX 仅在字段缺失时播种（seed 传入 MongoDB 的权威消息数），
          这样 Redis Key 过期重建后序号能接上，不会从 1 重新开始。
        - HINCRBY 保证并发下每个请求拿到互不重复的递增值，避免
          「读出来 +1 再写回」的竞态。

        序号允许出现空洞（例如 Agent 调用失败时已分配的 seq 被浪费），
        seq 只用于排序，不要求连续。
        """
        meta_key = context_meta_key(session_id)
        await self._client.hsetnx(meta_key, "last_seq", seed)
        seq = await self._client.hincrby(meta_key, "last_seq", 1)
        await self._client.expire(meta_key, self._ttl)
        return int(seq)

    async def exists(self, session_id: str) -> bool:
        """判断上下文 Key 是否存活"""
        return bool(await self._client.exists(context_list_key(session_id)))

    # ==================== 写入 ====================

    async def init_context(
        self,
        session_id: str,
        user_id: str,
        agent_key: str,
    ) -> None:
        """初始化空上下文并写入元信息

        创建会话时调用，让后续第一次 get_context 不会误判为「已过期」，
        同时把 last_seq 归零。
        """
        list_key = context_list_key(session_id)
        meta_key = context_meta_key(session_id)

        pipe = self._client.pipeline(transaction=True)
        pipe.delete(list_key, meta_key)
        pipe.hset(
            meta_key,
            mapping={
                "session_id": session_id,
                "user_id": user_id,
                "agent_key": agent_key,
                "last_seq": 0,
                "updated_at": utcnow().isoformat(),
            },
        )
        pipe.expire(meta_key, self._ttl)
        await pipe.execute()

        logger.info("初始化 Redis 上下文: session_id=%s user_id=%s", session_id, user_id)

    async def append_message(self, session_id: str, message: StoredMessage) -> None:
        """追加单条消息到上下文，并裁剪至窗口大小

        RPUSH + LTRIM + EXPIRE 放在同一个 pipeline 中执行，保证窗口长度
        与 TTL 不会出现中间态。
        """
        await self._append(session_id, [message])

    async def append_messages(self, session_id: str, messages: list[StoredMessage]) -> None:
        """批量追加消息（一次写入 user + assistant 两条，单次往返）"""
        if not messages:
            return
        await self._append(session_id, messages)

    async def _append(self, session_id: str, messages: list[StoredMessage]) -> None:
        """追加消息的内部实现"""
        list_key = context_list_key(session_id)
        meta_key = context_meta_key(session_id)
        last_seq = messages[-1].seq

        pipe = self._client.pipeline(transaction=True)
        for message in messages:
            # ensure_ascii=False：中文正文以原文存储，便于 redis-cli 直接排查
            pipe.rpush(list_key, json.dumps(message.model_dump(mode="json"), ensure_ascii=False))
        # 负数起止表示「只保留最后 max_messages 条」
        pipe.ltrim(list_key, -self._max_messages, -1)
        pipe.expire(list_key, self._ttl)
        pipe.hset(
            meta_key,
            mapping={
                "last_seq": last_seq,
                "updated_at": utcnow().isoformat(),
            },
        )
        pipe.expire(meta_key, self._ttl)
        await pipe.execute()

        logger.debug(
            "写入 Redis 上下文: session_id=%s count=%s last_seq=%s 窗口=%s",
            session_id, len(messages), last_seq, self._max_messages,
        )

    # ==================== 删除 ====================

    async def delete_context(self, session_id: str) -> None:
        """删除会话上下文（会话归档或删除时调用）"""
        await self._client.delete(context_list_key(session_id), context_meta_key(session_id))
        logger.info("删除 Redis 上下文: session_id=%s", session_id)

    # ==================== 序列化辅助 ====================

    @staticmethod
    def _decode(raw: Any) -> Optional[dict[str, Any]]:
        """把 Redis 返回的字节/字符串解码为 dict"""
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _decode_field(mapping: dict[Any, Any], field: str) -> Optional[Any]:
        """从 hgetall 结果中按字段名取值

        redis-py 未开启 decode_responses 时，键为 bytes，需要兼容两种形态。
        """
        if field in mapping:
            return mapping[field]
        encoded = field.encode("utf-8")
        if encoded in mapping:
            value = mapping[encoded]
            return value.decode("utf-8") if isinstance(value, bytes) else value
        return None
