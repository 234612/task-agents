"""MongoDB 对话历史仓储

使用 motor（PyMongo 官方异步驱动）而非同步 PyMongo：本项目路由与服务层
全部是 async，同步驱动会阻塞事件循环。

只负责 chat_sessions 集合的文档读写，不含业务编排。
"""
import logging
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

from task_agents.core.timeutils import utcnow
from task_agents.schemas.mongo import SessionDocument, StoredMessage

logger = logging.getLogger(__name__)


class MongoMessageRepository:
    """对话历史文档仓储"""

    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str):
        self._collection: AsyncIOMotorCollection = db[collection_name]
        self._collection_name = collection_name
        self._indexes_ready = False

    # ==================== 生命周期 ====================

    async def ensure_indexes(self) -> None:
        """创建查询所需索引（幂等，重复调用无副作用）

        - user_id + updated_at：按用户做数据治理/导出时的排序查询
        - messages.seq：$push 配合 sort 时的数组内定位
        """
        if self._indexes_ready:
            return
        await self._collection.create_index([("user_id", 1), ("updated_at", -1)])
        self._indexes_ready = True
        logger.info("MongoDB 索引就绪: collection=%s", self._collection_name)

    # ==================== 查询 ====================

    async def get_document(self, session_id: str) -> Optional[SessionDocument]:
        """读取完整会话文档"""
        doc = await self._collection.find_one({"_id": session_id})
        if doc is None:
            return None
        return SessionDocument.from_mongo(doc)

    async def list_messages(
        self,
        session_id: str,
        skip: int = 0,
        limit: int = 0,
    ) -> list[StoredMessage]:
        """读取指定会话的历史消息

        用投影 + $slice 只取 messages 字段，避免把整个文档（含其他字段）
        拉回应用层。limit <= 0 表示不限制，返回全部消息。

        数组本身按写入顺序存储，这里再按 seq 显式排序，保证即使历史数据
        存在乱序追加也能返回稳定顺序。
        """
        if skip < 0:
            raise ValueError("skip 不能为负数")
        if limit < 0:
            raise ValueError("limit 不能为负数")

        projection: dict[str, Any] = {"messages": 1}
        if limit > 0:
            # find 投影的 $slice 语法为 {"$slice": [skip, limit]}，
            # 不带字段名（那是聚合 $project 阶段的表达式写法）。
            projection["messages"] = {"$slice": [skip, limit]}

        doc = await self._collection.find_one({"_id": session_id}, projection)
        if not doc:
            return []

        raw_messages = doc.get("messages") or []
        messages = [StoredMessage.model_validate(m) for m in raw_messages]
        messages.sort(key=lambda m: m.seq)
        return messages

    async def count_messages(self, session_id: str) -> int:
        """统计消息条数（用于与 MySQL message_count 做一致性核对）"""
        pipeline = [
            {"$match": {"_id": session_id}},
            {"$project": {"count": {"$size": {"$ifNull": ["$messages", []]}}}},
        ]
        async for doc in self._collection.aggregate(pipeline):
            return int(doc.get("count", 0))
        return 0

    # ==================== 写入 ====================

    async def create_document(self, document: SessionDocument) -> SessionDocument:
        """创建会话文档（空 messages 数组）

        与 MySQL 记录同生命周期：创建会话时先在 MySQL 建元数据，再来这里
        建文档骨架，后续消息一律走 append_message 追加。
        """
        await self._collection.insert_one(document.to_mongo())
        logger.info(
            "创建 MongoDB 会话文档: session_id=%s user_id=%s",
            document.id, document.user_id,
        )
        return document

    async def append_message(self, session_id: str, message: StoredMessage) -> bool:
        """向 messages 数组追加一条消息（$push）

        使用 upsert=True 兜底：若文档因异常缺失则自动重建骨架，保证消息
        不会因为文档不存在而静默丢失。

        返回 True 表示已写入。
        """
        now = utcnow()
        result = await self._collection.update_one(
            {"_id": session_id},
            {
                "$push": {"messages": message.model_dump()},
                "$set": {"updated_at": now},
                "$setOnInsert": {
                    "created_at": now,
                    "user_id": "",
                    "agent_key": "",
                },
            },
            upsert=True,
        )
        ok = result.modified_count > 0 or result.upserted_id is not None
        logger.debug(
            "追加消息到 MongoDB: session_id=%s seq=%s role=%s ok=%s",
            session_id, message.seq, message.role, ok,
        )
        return ok

    async def append_messages(self, session_id: str, messages: list[StoredMessage]) -> bool:
        """批量追加消息（单次往返写入 user + assistant 两条）"""
        if not messages:
            return False

        now = utcnow()
        result = await self._collection.update_one(
            {"_id": session_id},
            {
                "$push": {"messages": {"$each": [m.model_dump() for m in messages]}},
                "$set": {"updated_at": now},
            },
        )
        ok = result.modified_count > 0
        logger.debug(
            "批量追加消息到 MongoDB: session_id=%s count=%s ok=%s",
            session_id, len(messages), ok,
        )
        return ok

    async def next_seq(self, session_id: str) -> int:
        """计算下一条消息的 seq

        以 MongoDB 中已有消息数为基准 +1。文档不存在时返回 1。
        """
        count = await self.count_messages(session_id)
        return count + 1

    # ==================== 删除 ====================

    async def delete_document(self, session_id: str) -> bool:
        """物理删除会话文档

        注意：MySQL 侧默认是软删除（status=deleted）。是否同步物理删除
        MongoDB 文档由 Service 层按数据保留策略决定。
        """
        result = await self._collection.delete_one({"_id": session_id})
        deleted = result.deleted_count > 0
        logger.info("删除 MongoDB 会话文档: session_id=%s deleted=%s", session_id, deleted)
        return deleted
