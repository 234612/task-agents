"""演示数据种子脚本

为前端联调写入可直接看到效果的演示数据（MySQL + MongoDB + Redis 三处一致）。

用法：
    cd backend
    uv run python scripts/seed_demo_data.py            # 写入演示数据
    uv run python scripts/seed_demo_data.py --reset    # 先清空再写入

设计要点：
1. 幂等且安全：会话 ID 固定。默认只覆盖这些固定的演示会话，
   **不会**触碰用户通过前端真实创建的数据；只有显式传 --reset 才清空该用户全部会话。
2. 时间戳手工构造并逐条拉开差距，用于验证侧边栏「按最后活跃时间倒序」。
3. 三处数据保持一致：MySQL message_count 与 MongoDB messages 长度相等，
   Redis 上下文填充最近若干条，模拟真实会话被打开过的状态。
4. 含 tool_calls 样例，便于前端验证工具调用的渲染。
"""
import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

# 支持以 `python scripts/seed_demo_data.py` 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from task_agents.core.config import get_settings  # noqa: E402
from task_agents.core.timeutils import utcnow  # noqa: E402
from task_agents.database.models import ChatSession, SessionStatus  # noqa: E402
from task_agents.database.redis_keys import turns_to_messages  # noqa: E402
from task_agents.main import app  # noqa: E402
from task_agents.repository.mongo_message_repository import MongoMessageRepository  # noqa: E402
from task_agents.repository.mysql_session_repository import MySQLSessionRepository  # noqa: E402
from task_agents.repository.redis_context_repository import RedisContextRepository  # noqa: E402
from task_agents.schemas.mongo import SessionDocument, StoredMessage, ToolCall  # noqa: E402

# 现阶段前端 hardcode 的用户 ID（登录功能留待下一阶段实现）
DEMO_USER_ID = "userid_1"
DEMO_AGENT_KEY = "market_researcher"

# 固定会话 ID，保证脚本幂等
SESSION_IDS = [
    "demo-sess-0001-arch-storage",
    "demo-sess-0002-fastapi-di",
    "demo-sess-0003-market-research",
    "demo-sess-0004-mongo-modeling",
]


def _turns(user: str, assistant: str, tool_calls: list[ToolCall] | None = None) -> list[tuple]:
    """构造一组 (role, content, tool_calls) 三元组"""
    return [("user", user, None), ("assistant", assistant, tool_calls)]


# ==================== 演示会话内容 ====================
# 每个会话：(标题, 距今小时数, 消息轮次)
DEMO_SESSIONS: list[tuple[str, float, list[tuple]]] = [
    (
        "AI Agent 分层存储架构设计",
        0.5,  # 最近活跃，应排在侧边栏首位
        [
            _turns(
                "我在做一个 AI Agent 对话助手，会话数据该怎么存？",
                "建议按数据特征做分层存储：\n\n"
                "1. **会话元数据**放 MySQL —— 列表页要排序、分页、筛选，"
                "关系型数据库的 ACID 与二级索引正好适配，"
                "为 `(user_id, updated_at)` 建联合索引就能避免 filesort。\n"
                "2. **对话历史**放 MongoDB —— 文档模型的嵌套结构一次查询取回整条会话，"
                "免掉多表 JOIN，`$push` 追加写契合高频写入。\n"
                "3. **短期记忆**放 Redis —— 亚毫秒读写加 TTL 自动过期，"
                "给 Prompt 注入提供低延迟上下文。",
            ),
            _turns(
                "那写入的时候怎么保证三个存储一致？",
                "采用「同步 + 异步」的双写策略：\n\n"
                "- Redis **同步写**，保证下一轮 Prompt 立刻能读到上下文\n"
                "- MySQL **同步写**，`updated_at` 和 `message_count` 要即时正确，"
                "否则侧边栏排序会错\n"
                "- MongoDB **异步写**，走后台任务不阻塞主流程\n\n"
                "要接受的取舍是：MySQL 计数与 MongoDB 实际条数是**最终一致**。"
                "如果后台写入失败会出现偏差，需要一个对账接口定期核对，"
                "以 MongoDB 的实际消息数为准回写 MySQL。",
                [
                    ToolCall(
                        id="call_demo_001",
                        name="web_research",
                        args={"query": "AI Agent memory architecture Redis MongoDB"},
                    )
                ],
            ),
            _turns(
                "Redis 的上下文窗口怎么控制？",
                "用 List 结构配合定长裁剪：\n\n"
                "```\n"
                "RPUSH memory:context:{session_id} <json>\n"
                "LTRIM memory:context:{session_id} -20 -1\n"
                "EXPIRE memory:context:{session_id} 604800\n"
                "```\n\n"
                "这三条放在同一个 pipeline 里执行，窗口长度和 TTL 就不会出现中间态。"
                "负数起止表示只保留最后 20 条，10 轮对话正好 20 条消息。\n\n"
                "另外单独用一个 Hash 存 `last_seq`，靠 `HINCRBY` 做并发安全的序号分配，"
                "避免多请求同时写导致 seq 重复。",
            ),
        ],
    ),
    (
        "FastAPI 依赖注入与异步会话管理",
        26.0,  # 约 1 天前
        [
            _turns(
                "FastAPI 里数据库会话怎么用 Depends 管理比较好？",
                "用带 `yield` 的依赖，每个请求一个 AsyncSession：\n\n"
                "```python\n"
                "async def get_db_session(request: Request):\n"
                "    async with request.app.state.db_session_factory() as session:\n"
                "        yield session\n"
                "```\n\n"
                "`async with` 保证即使处理函数抛异常也会把连接释放回池。"
                "引擎本身要在 lifespan 里创建一次挂在 `app.state` 上复用，"
                "不要在请求内反复建连。\n\n"
                "连接池记得配 `pool_pre_ping=True` 和 `pool_recycle=3600`，"
                "前者做健康检查自动重连，后者规避 MySQL 8 小时空闲超时。",
            ),
            _turns(
                "有个坑：我在 BackgroundTasks 里复用请求的 db 会话，报错了。",
                "这是 FastAPI 的时序特性，不是 bug。\n\n"
                "带 `yield` 的依赖，其清理代码在**响应发送后、后台任务执行前**就会运行。"
                "所以后台任务拿到的是一个已经关闭的会话。\n\n"
                "正确做法是让后台任务函数只接收 `app` 和纯数据参数，"
                "内部自己从 `app.state.db_session_factory` 新建会话：\n\n"
                "```python\n"
                "async def persist(app, session_id, messages):\n"
                "    async with app.state.db_session_factory() as db:\n"
                "        repo = MySQLSessionRepository(db)\n"
                "        ...\n"
                "```\n\n"
                "SSE 流式响应也是同样的道理，生成器执行期间请求级依赖已不可依赖。",
            ),
        ],
    ),
    (
        "市场调研：AI Agent 行业趋势分析",
        74.0,  # 约 3 天前
        [
            _turns(
                "帮我梳理一下 AI Agent 领域的主要技术方向。",
                "当前主要沿四条线演进：\n\n"
                "| 方向 | 核心问题 | 代表做法 |\n"
                "|------|---------|--------|\n"
                "| 记忆架构 | 上下文窗口有限 | 分层存储 + 向量检索 |\n"
                "| 工具调用 | 与外部系统交互 | Function Calling / MCP |\n"
                "| 多智能体 | 复杂任务分解 | 主 Agent 委派子 Agent |\n"
                "| 可观测性 | 链路难以排查 | 轨迹追踪 + 结构化日志 |\n\n"
                "工程化落地上，记忆分层和工具调用是投入产出比最高的两块，"
                "建议优先做。多智能体会显著增加调试复杂度，"
                "在单 Agent 能力没有瓶颈前不必过早引入。",
                [
                    ToolCall(
                        id="call_demo_002",
                        name="web_research",
                        args={"query": "AI Agent 技术趋势 记忆架构 工具调用"},
                    ),
                    ToolCall(
                        id="call_demo_003",
                        name="web_research",
                        args={"query": "multi-agent orchestration production challenges"},
                    ),
                ],
            ),
            _turns(
                "多 Agent 协作的落地难点具体是什么？",
                "三个最现实的难点：\n\n"
                "**1. 错误传播放大** —— 子 Agent 的输出是下一个的输入，"
                "单点幻觉会沿链路累积，最终结果偏离度远大于单 Agent。\n\n"
                "**2. 状态一致性** —— 多个 Agent 并发读写共享状态时，"
                "需要明确的所有权划分，否则会出现竞态。"
                "LangGraph 的 checkpointer 机制就是为解决这个而设计的。\n\n"
                "**3. 成本不可控** —— 每次委派都是一轮完整 LLM 调用，"
                "Token 消耗随链路深度指数增长。生产环境必须设置递归深度上限，"
                "并对子任务做缓存。\n\n"
                "实践建议：先用「主 Agent + 明确职责的子 Agent」这种星型结构，"
                "不要让子 Agent 之间横向互相调用。",
            ),
        ],
    ),
    (
        "MongoDB 文档建模与索引优化",
        122.0,  # 约 5 天前
        [
            _turns(
                "对话历史用 MongoDB 存，文档结构怎么设计？",
                "推荐「一个会话一个文档，消息内嵌数组」：\n\n"
                "```json\n"
                "{\n"
                '  "_id": "会话UUID",\n'
                '  "user_id": "userid_1",\n'
                '  "messages": [\n'
                '    {"seq": 1, "role": "user", "content": "...",\n'
                '     "tool_calls": [], "ts": ISODate}\n'
                "  ]\n"
                "}\n"
                "```\n\n"
                "好处是加载整条会话只需一次查询，没有 JOIN。\n\n"
                "两个注意点：\n"
                "- 单文档有 16MB 上限，超长会话要考虑分片或归档\n"
                "- 消息顺序**不要依赖 `ts`**，时钟漂移和精度不足都可能导致乱序，"
                "应该用会话内单调递增的 `seq` 字段",
            ),
            _turns(
                "seq 怎么生成才不会重复？",
                "别用「读出来 +1 再写回」，并发下必然丢失更新。\n\n"
                "两种可靠方案：\n\n"
                "**方案 A：Redis 原子自增**（本项目采用）\n"
                "```\n"
                "HSETNX memory:meta:{session_id} last_seq <mongo消息数>\n"
                "HINCRBY memory:meta:{session_id} last_seq 1\n"
                "```\n"
                "`HSETNX` 只在字段缺失时播种，所以 Redis Key 过期重建后序号能接上；"
                "`HINCRBY` 保证每个请求拿到互不重复的值。\n\n"
                "**方案 B：MongoDB findAndModify 自增**\n"
                "在会话文档上维护计数器字段，用 `$inc` 配合原子更新。\n\n"
                "补充一点：seq 允许出现空洞（比如 Agent 调用失败时已分配的号被浪费），"
                "因为它只用于排序，不要求连续。",
            ),
        ],
    ),
]


async def _clear_user_data(app_obj, factory, user_id: str, full_reset: bool) -> None:
    """清理旧数据后再写入

    full_reset=False（默认）：只删除本脚本固定的 SESSION_IDS，
        保留用户通过前端真实创建的会话，避免误删业务数据。
    full_reset=True（--reset）：删除该 user_id 的全部会话，
        用于把演示账号彻底恢复到初始状态。
    """
    if full_reset:
        async with factory() as db:
            rows = await db.execute(
                select(ChatSession.session_id).where(ChatSession.user_id == user_id)
            )
            existing_ids = [r[0] for r in rows.all()]
            await db.execute(delete(ChatSession).where(ChatSession.user_id == user_id))
            await db.commit()
        target_ids = existing_ids
        print(f"  [全量清理] MySQL: 已删除 {len(existing_ids)} 条会话记录")
    else:
        async with factory() as db:
            rows = await db.execute(
                select(ChatSession.session_id).where(
                    ChatSession.session_id.in_(SESSION_IDS)
                )
            )
            target_ids = [r[0] for r in rows.all()]
            await db.execute(
                delete(ChatSession).where(ChatSession.session_id.in_(SESSION_IDS))
            )
            await db.commit()
        print(f"  [仅清理演示数据] MySQL: 已删除 {len(target_ids)} 条会话记录")

    settings = get_settings()
    mongo_repo = MongoMessageRepository(app_obj.state.mongo_db, settings.MONGO_SESSION_COLLECTION)

    from task_agents.database.redis_keys import context_list_key, context_meta_key

    redis_client = app_obj.state.redis_client
    mongo_deleted = 0
    redis_removed = 0
    for sid in target_ids:
        if await mongo_repo.delete_document(sid):
            mongo_deleted += 1
        redis_removed += await redis_client.delete(context_list_key(sid), context_meta_key(sid))
    print(f"  MongoDB: 已删除 {mongo_deleted} 个会话文档")
    print(f"  Redis: 已删除 {redis_removed} 个上下文 Key")


async def seed(full_reset: bool = False) -> int:
    settings = get_settings()
    window = turns_to_messages(settings.REDIS_CONTEXT_MAX_TURNS)

    async with app.router.lifespan_context(app):
        factory = app.state.db_session_factory
        mongo_repo = MongoMessageRepository(
            app.state.mongo_db, settings.MONGO_SESSION_COLLECTION
        )
        redis_repo = RedisContextRepository(
            app.state.redis_client,
            max_messages=window,
            ttl_seconds=settings.REDIS_CONTEXT_TTL_SECONDS,
        )

        await mongo_repo.ensure_indexes()

        print(f"开始写入演示数据: user_id={DEMO_USER_ID}")
        await _clear_user_data(app, factory, DEMO_USER_ID, full_reset)

        now = utcnow()
        summary: list[str] = []

        for idx, (title, hours_ago, turns) in enumerate(DEMO_SESSIONS):
            session_id = SESSION_IDS[idx]
            created_at = now - timedelta(hours=hours_ago + len(turns) * 0.5)
            updated_at = now - timedelta(hours=hours_ago)

            # —— 组装消息数组，seq 从 1 连续递增 ——
            messages: list[StoredMessage] = []
            seq = 1
            for turn in turns:
                for role, content, tool_calls in turn:
                    # 消息时间戳在 created_at 与 updated_at 之间均匀铺开
                    offset = (seq - 1) / max(1, len(turns) * 2)
                    messages.append(
                        StoredMessage(
                            seq=seq,
                            role=role,
                            content=content,
                            tool_calls=tool_calls or [],
                            ts=created_at + (updated_at - created_at) * offset,
                        )
                    )
                    seq += 1

            # —— 1. MySQL：会话元数据 ——
            async with factory() as db:
                repo = MySQLSessionRepository(db)
                await repo.create(
                    ChatSession(
                        session_id=session_id,
                        user_id=DEMO_USER_ID,
                        title=title,
                        agent_key=DEMO_AGENT_KEY,
                        status=SessionStatus.ACTIVE,
                        message_count=len(messages),
                        created_at=created_at,
                        updated_at=updated_at,
                    )
                )
                await repo.commit()

            # —— 2. MongoDB：会话文档与消息数组 ——
            await mongo_repo.create_document(
                SessionDocument(
                    id=session_id,
                    user_id=DEMO_USER_ID,
                    agent_key=DEMO_AGENT_KEY,
                    messages=messages,
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )

            # —— 3. Redis：初始化上下文并填充最近若干条 ——
            await redis_repo.init_context(
                session_id, user_id=DEMO_USER_ID, agent_key=DEMO_AGENT_KEY
            )
            tail = messages[-window:]
            if tail:
                await redis_repo.append_messages(session_id, tail)
                # 校正 last_seq 为全量最后一条，保证后续新消息序号接续
                from task_agents.database.redis_keys import context_meta_key

                await app.state.redis_client.hset(
                    context_meta_key(session_id), "last_seq", messages[-1].seq
                )

            mongo_count = await mongo_repo.count_messages(session_id)
            summary.append(
                f"  [{idx + 1}] {title}\n"
                f"      session_id={session_id}\n"
                f"      消息数={len(messages)} (Mongo 核对={mongo_count}) "
                f"Redis 上下文={len(tail)} 条\n"
                f"      活跃时间={updated_at.isoformat()}Z"
            )

        print("\n写入完成：")
        for line in summary:
            print(line)

        # —— 复核：MySQL 与 MongoDB 计数一致性 ——
        print("\n一致性核对：")
        all_ok = True
        for idx in range(len(DEMO_SESSIONS)):
            sid = SESSION_IDS[idx]
            async with factory() as db:
                repo = MySQLSessionRepository(db)
                mysql_count = await repo.get_message_count(sid)
            mongo_count = await mongo_repo.count_messages(sid)
            ok = mysql_count == mongo_count
            all_ok = all_ok and ok
            print(f"  {'OK ' if ok else 'BAD'} {sid[:34]} mysql={mysql_count} mongo={mongo_count}")

        # 复核列表排序（应严格倒序）
        async with factory() as db:
            repo = MySQLSessionRepository(db)
            rows, total = await repo.list_by_user(DEMO_USER_ID, page=1, page_size=20)
        timestamps = [r.updated_at for r in rows]
        ordered = timestamps == sorted(timestamps, reverse=True)
        all_ok = all_ok and ordered and total == len(DEMO_SESSIONS)
        print(f"\n  会话总数={total} 列表倒序正确={ordered}")
        print(f"  首位应为「{DEMO_SESSIONS[0][0]}」→ 实际「{rows[0].title}」")

        print("\n全部核对通过" if all_ok else "\n存在不一致，请检查上方 BAD 行")
        return 0 if all_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="写入演示数据")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="清空该用户的全部会话后再写入（默认只覆盖固定的演示会话，保留真实数据）",
    )
    args = parser.parse_args()

    if args.reset:
        print("已指定 --reset：将清空该用户全部会话数据后重新写入")

    return asyncio.run(seed(full_reset=args.reset))


if __name__ == "__main__":
    raise SystemExit(main())
