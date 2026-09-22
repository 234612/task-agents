"""消息写入服务 — 三存储写入编排

分层存储的写入路径（对应「双写逻辑」要求）：

1. Redis   同步写。保证 Prompt 上下文即时生效，下一轮对话立刻能读到。
2. MySQL   同步写。更新 updated_at 与 message_count，让侧边栏排序立即正确。
3. MongoDB 异步写。由调用方通过 BackgroundTasks 调度，不阻塞主流程。

一致性取舍（重要）：
MySQL 计数与 MongoDB 文档之间是最终一致，不是强一致。若 MongoDB 后台写入
失败，会出现「MySQL message_count 比 Mongo 实际消息数多」的偏差。这是刻意
的选择——换取侧边栏的即时正确性。偏差可通过 compare_counts() 核对，并由
定时任务补偿。Redis 属于可重建的加速层，故障时直接降级，不影响写入结果。
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from task_agents.repository.mongo_message_repository import MongoMessageRepository
from task_agents.repository.mysql_session_repository import MySQLSessionRepository
from task_agents.repository.redis_context_repository import RedisContextRepository
from task_agents.schemas.mongo import Citation, StoredMessage, ThinkingStep, ToolCall

logger = logging.getLogger(__name__)


@dataclass
class TurnRecord:
    """一轮对话的写入结果

    messages 需由调用方交给后台任务写入 MongoDB。
    """
    session_id: str
    user_message: StoredMessage
    assistant_message: StoredMessage
    message_count: Optional[int] = None

    @property
    def messages(self) -> list[StoredMessage]:
        return [self.user_message, self.assistant_message]

    @property
    def delta(self) -> int:
        return len(self.messages)


@dataclass
class MessageWriteService:
    """消息写入编排服务"""

    session_repo: MySQLSessionRepository
    mongo_repo: MongoMessageRepository
    redis_repo: RedisContextRepository
    tool_calls: list[ToolCall] = field(default_factory=list)

    # ==================== 序号分配 ====================

    async def _next_seq(self, session_id: str, fallback_base: int) -> int:
        """分配下一条消息的 seq

        优先走 Redis 的原子自增（HSETNX + HINCRBY），并发安全且无 Mongo 往返；
        Redis 不可用时降级为「MySQL message_count + 偏移量」，保证主流程不断。
        """
        try:
            return await self.redis_repo.allocate_seq(session_id, seed=fallback_base)
        except Exception as e:
            logger.warning(
                "Redis 序号分配失败，降级使用 MySQL 计数: session_id=%s error=%s",
                session_id, e,
            )
            return fallback_base + 1

    # ==================== 上下文读取 ====================

    async def load_context(self, session_id: str) -> list[StoredMessage]:
        """读取会话短期记忆，用于 Prompt 注入

        Redis 未命中或故障时返回空列表，调用方应回源 MongoDB 读取全量历史。
        """
        try:
            return await self.redis_repo.get_context(session_id)
        except Exception as e:
            logger.warning("读取 Redis 上下文失败，将回源 MongoDB: session_id=%s error=%s", session_id, e)
            return []

    # ==================== 核心写入 ====================

    async def record_turn(
        self,
        session_id: str,
        user_id: str,
        user_content: str,
        assistant_content: str,
        assistant_tool_calls: Optional[list[ToolCall]] = None,
        assistant_thinking_steps: Optional[list[ThinkingStep]] = None,
        assistant_citations: Optional[list[Citation]] = None,
    ) -> TurnRecord:
        """记录一轮对话（user + assistant）

        同步完成 Redis 与 MySQL 写入，返回待异步落库的消息列表。
        thinking_steps / citations 只随 assistant 消息落 MongoDB
        （长期记忆层），不写 Redis——短期记忆只服务 Prompt 注入，
        思考链对下一轮回答没有价值，放进窗口只会浪费 Token。

        调用方拿到返回值后应立刻调度后台任务写 MongoDB：
            background_tasks.add_task(write_messages_to_mongo, ...)
        """
        base_count = await self._message_count(session_id)

        user_seq = await self._next_seq(session_id, fallback_base=base_count)
        assistant_seq = await self._next_seq(session_id, fallback_base=base_count + 1)

        user_message = StoredMessage(seq=user_seq, role="user", content=user_content)
        assistant_message = StoredMessage(
            seq=assistant_seq,
            role="assistant",
            content=assistant_content,
            tool_calls=assistant_tool_calls or [],
            thinking_steps=assistant_thinking_steps or [],
            citations=assistant_citations or [],
        )
        turn = TurnRecord(
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant_message,
        )

        # —— 1. 同步写 Redis（失败降级，不阻断） ——
        await self._write_redis(turn)

        # —— 2. 同步写 MySQL（失败即抛，事务由 Service 层控制） ——
        new_count = await self._touch_mysql(session_id, delta=turn.delta)
        turn.message_count = new_count

        logger.info(
            "一轮对话已写入 Redis+MySQL: session_id=%s user_seq=%s assistant_seq=%s count=%s",
            session_id, user_seq, assistant_seq, new_count,
        )
        return turn

    async def _write_redis(self, turn: TurnRecord) -> None:
        """同步写入 Redis 上下文，失败时降级为仅记录日志"""
        try:
            await self.redis_repo.append_messages(turn.session_id, turn.messages)
        except Exception as e:
            # Redis 是加速层，故障不应导致用户消息发送失败
            logger.error(
                "Redis 上下文写入失败，已降级（消息仍会落 MongoDB）: session_id=%s error=%s",
                turn.session_id, e,
            )

    async def _touch_mysql(self, session_id: str, delta: int) -> Optional[int]:
        """更新 MySQL 的 updated_at 与 message_count，返回最新计数

        必须显式 commit：请求级 AsyncSession 在退出 async with 时会关闭并
        回滚未提交事务，只 execute 不 commit 会导致计数与活跃时间静默丢失，
        侧边栏排序随之失效。

        读回计数走标量查询而非 ORM 实体，避免 identity map 返回过期对象。
        """
        affected = await self.session_repo.touch(session_id, message_delta=delta)
        if not affected:
            logger.warning("MySQL 会话记录不存在，计数更新被跳过: session_id=%s", session_id)
            return None

        await self.session_repo.commit()
        return await self.session_repo.get_message_count(session_id)

    async def message_count(self, session_id: str) -> int:
        """读取会话当前消息数（MySQL 权威值）

        用于判定是否首轮对话。刻意不用 Redis 上下文长度：Redis 有 TTL 且属于
        可失效的加速层，过期或故障时会误判为首轮，导致重复生成标题。
        """
        return await self._message_count(session_id)

    async def _message_count(self, session_id: str) -> int:
        """读取当前消息数，作为序号分配的降级基准"""
        count = await self.session_repo.get_message_count(session_id)
        return count or 0

    # ==================== MongoDB 异步落库 ====================

    async def write_messages_to_mongo(self, session_id: str, messages: list[StoredMessage]) -> bool:
        """把消息批量追加到 MongoDB（供 BackgroundTasks 调用）

        后台任务中任何异常都不能向上抛出——FastAPI 已经返回响应，抛出只会
        污染日志且无法告知客户端。失败靠日志告警与对账补偿。
        """
        try:
            ok = await self.mongo_repo.append_messages(session_id, messages)
            if not ok:
                logger.error(
                    "MongoDB 消息写入未生效: session_id=%s count=%s",
                    session_id, len(messages),
                )
            return ok
        except Exception:
            logger.exception(
                "MongoDB 消息写入异常，等待对账补偿: session_id=%s seqs=%s",
                session_id, [m.seq for m in messages],
            )
            return False

    # ==================== 对账 ====================

    async def compare_counts(self, session_id: str) -> dict[str, Any]:
        """核对 MySQL 计数与 MongoDB 实际消息数

        用于排查双写偏差，也可作为定时补偿任务的健康度指标。
        """
        mysql_count = await self._message_count(session_id)
        try:
            mongo_count = await self.mongo_repo.count_messages(session_id)
        except Exception as e:
            logger.error("MongoDB 计数查询失败: session_id=%s error=%s", session_id, e)
            mongo_count = -1

        return {
            "session_id": session_id,
            "mysql_message_count": mysql_count,
            "mongo_message_count": mongo_count,
            "consistent": mongo_count >= 0 and mysql_count == mongo_count,
        }


__all__ = ["MessageWriteService", "TurnRecord"]
