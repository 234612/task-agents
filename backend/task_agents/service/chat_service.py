"""聊天服务层 — Agent 调用、SSE 事件协议与三层记忆编排

两条调用路径，都完成「Redis 短期 / MySQL 业务 / MongoDB 长期」三写：

1. invoke_chat（服务 POST /api/chat）
   ainvoke 取完整回复后一次性返回。

2. stream_chat_persist（服务 POST /api/chat/stream，前端主链路）
   astream 增量推送 SSE，推送完毕后落库，再下发 done 事件。

SSE 事件协议（所有帧均为 data: {JSON}，type 字段区分）：
- meta         流开始时首帧下发：{session_id, created}，首次对话由后端
               自动生成的会话 ID 通过它回传前端
- content      模型回复增量：{content}
- thinking     图节点执行步骤：{node, summary}，前端渲染思考链
- tool_call    工具调用发起：{id, name, args}
- tool_result  工具执行结果：{tool_call_id, name, content}
- done         流结束：{persisted, message_count, session_id}
               —— 持久化完成后才发送，前端收到即可安全刷新列表/历史
- error        流内错误：{message}

三层记忆接线：
- RedisSaver checkpointer（thread_id=session_id）是 Agent 的进程外短期记忆；
- 业务侧 Redis 滑动窗口（memory:context:*）在 checkpointer 冷启动
  （Key 过期 / 服务重启）时回灌上下文到 state，见 _seed_context；
- MongoDBStore 承载跨会话长期记忆（由 deepagents backend 接入，本层不直接读写）。
"""
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from task_agents.agent.middleware.loop_breaker import tool_failure_breaker
from task_agents.core.config import get_settings
from task_agents.sandbox.sandbox_registry import ensure_pool
from task_agents.schemas.mongo import (
    Citation,
    StoredMessage,
    ThinkingStep,
    ToolCall,
)
from task_agents.service.message_service import MessageWriteService, TurnRecord

if TYPE_CHECKING:
    from task_agents.service.session_service import SessionService

logger = logging.getLogger(__name__)

# 工具结果写入思考链时的截断长度：防止大段网页正文撑爆 SSE 帧与文档
_STEP_SUMMARY_LIMIT = 200
_TOOL_RESULT_PREVIEW_LIMIT = 500


class AgentNotFoundError(Exception):
    """指定的 agent_key 未注册"""


class SessionEndedHTTPError(Exception):
    """会话已结束（status=2）仍尝试追加消息。Router 转 409。"""


@dataclass
class ChatTurnResult:
    """一次聊天的处理结果，供 Router 层组装响应与调度后台任务"""
    session_id: str
    created: bool
    user_message: StoredMessage
    assistant_message: StoredMessage
    message_count: Optional[int]
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking_steps: list[ThinkingStep] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)

    @property
    def is_first_turn(self) -> bool:
        """本结果对应的那条用户消息是否为会话首条"""
        return self.user_message.seq == 1

    @property
    def pending_messages(self) -> list[StoredMessage]:
        """待异步写入 MongoDB 的消息"""
        return [self.user_message, self.assistant_message]


class ChatService:
    """聊天服务

    依赖注入 agent_getter 而非直接依赖 factory，便于测试时替换为桩对象。
    """

    def __init__(
        self,
        message_service: MessageWriteService,
        agent_getter: Any,
        session_service: "SessionService",
    ):
        """
        Args:
            message_service: 三存储写入编排服务
            agent_getter: 可调用对象，签名 (agent_key) -> CompiledStateGraph
            session_service: 会话服务。负责会话隔离——首次对话自动建会话，
                             续聊校验归属与状态——必须在任何写入之前完成
        """
        self._messages = message_service
        self._agent_getter = agent_getter
        self._session_service = session_service

    # ==================== 会话隔离入口 ====================

    async def prepare_session(
        self,
        user_id: str,
        agent_key: str,
        session_id: Optional[str],
        first_message: str,
    ) -> tuple[str, bool]:
        """会话隔离：校验续聊会话或为首次对话自动创建

        必须在生成任何响应（含 SSE 首帧）之前调用：403/404/409 只能在
        HTTP 状态码阶段表达，流一旦开始就固定为 200 了。

        Returns:
            (session_id, created) —— created=True 表示本次新建
        """
        session = await self._session_service.ensure_session(
            user_id=user_id,
            agent_key=agent_key,
            session_id=session_id,
            first_message=first_message,
        )
        return session.session_id, int(session.message_count) == 0 and session_id is None

    def resolve_agent(self, agent_key: str) -> Any:
        """按 key 获取 Agent 实例

        Raises:
            AgentNotFoundError: agent_key 未注册
        """
        try:
            return self._agent_getter(agent_key)
        except KeyError as e:
            raise AgentNotFoundError(str(e)) from e

    # ==================== 主路径：同步调用 Agent ====================

    async def invoke_chat(
        self,
        user_id: str,
        agent_key: str,
        content: str,
        session_id: Optional[str],
    ) -> ChatTurnResult:
        """调用 Agent 生成回复并完成三存储写入

        流程：会话隔离 → 取 Agent → 冷启动回灌上下文 → ainvoke → 持久化。
        Agent 调用失败时直接向上抛出，由 Router 转成 5xx；此时不写任何
        存储，避免落下「有用户消息但没有回复」的半轮对话。
        """
        final_session_id, created = await self.prepare_session(
            user_id, agent_key, session_id, content
        )
        agent = self.resolve_agent(agent_key)

        logger.info(
            "开始调用 Agent: session_id=%s user_id=%s agent_key=%s 新建=%s",
            final_session_id, user_id, agent_key, created,
        )

        # —— 沙箱池：请求前为该 thread（=session_id）分配独占沙箱 ——
        # Thread-scoped 隔离：工具调用（含子 Agent）都经由 ThreadScopedBackend
        # 按 thread_id 路由到池中该会话的沙箱；请求结束 release 时「先回收产物再销毁」。
        pool = await ensure_pool()
        await pool.acquire(final_session_id)
        # 新一轮对话：清掉上一轮残留的失败计数，否则一次熔断会卡死整个会话
        tool_failure_breaker.reset(final_session_id)
        try:
            reply_text, tool_calls = await self._invoke_agent(agent, final_session_id, user_id, content)
        finally:
            # 无论成功或异常，都回收产物并销毁沙箱（release 内部先 recover 再 cleanup）
            await pool.release(final_session_id)

        turn: TurnRecord = await self._messages.record_turn(
            session_id=final_session_id,
            user_id=user_id,
            user_content=content,
            assistant_content=reply_text,
            assistant_tool_calls=tool_calls,
        )

        logger.info(
            "Agent 回复已写入 Redis+MySQL: session_id=%s 回复长度=%s 工具调用=%s",
            final_session_id, len(reply_text), len(tool_calls),
        )

        return ChatTurnResult(
            session_id=final_session_id,
            created=created,
            user_message=turn.user_message,
            assistant_message=turn.assistant_message,
            message_count=turn.message_count,
            tool_calls=tool_calls,
        )

    async def _invoke_agent(
        self,
        agent: Any,
        session_id: str,
        user_id: str,
        content: str,
    ) -> tuple[str, list[ToolCall]]:
        """调用 Agent 并解析回复正文与工具调用"""
        messages = await self._seed_context(session_id, content)
        stream_config = self._thread_config(session_id, user_id)

        result = await agent.ainvoke({"messages": messages}, stream_config)
        last_message = self._last_message(result)

        if last_message is None:
            logger.warning("Agent 未返回消息: session_id=%s", session_id)
            return "", []

        return (
            self._extract_text(self._get_field(last_message, "content")),
            self._extract_tool_calls(self._get_field(last_message, "tool_calls")),
        )

    # ==================== 短期记忆：上下文注入 ====================

    async def _seed_context(self, session_id: str, content: str) -> list[dict]:
        """构造本次调用的消息输入：当前消息 + （冷启动时）Redis 窗口回灌

        分工：RedisSaver checkpointer 持有 thread 的完整历史，正常续聊时
        LangGraph 会自动恢复 state，这里只需传新消息。但 checkpointer 的
        Key 有 1 小时 TTL，过期或服务重启后 thread 是「冷」的——此时若不
        回灌，Agent 会失忆。业务侧 Redis 滑动窗口（同样是最近 N 条）恰好
        是廉价的热备份：检测冷启动后，把窗口内的历史拼进输入。

        冷启动判定直接查 checkpointer.aget_tuple 返回是否为 None，
        而不是猜 TTL 时间——重启后内存清空但 Key 仍在，只有真查才算准。

        注意窗口与当前消息的重叠问题：本轮 user 消息在「对话结束后」才
        append 回 Redis（见 record_turn），所以读取窗口时当前消息尚未写入，
        直接拼接不会重复。
        """
        current = {"role": "user", "content": content}

        context: list[StoredMessage] = []
        try:
            if await self._is_thread_cold(session_id):
                context = await self._messages.load_context(session_id)
        except Exception as e:
            # 上下文注入是增强而非依赖：失败只降级为「无历史」，不阻断对话
            logger.warning("冷启动上下文回灌失败，按无历史继续: session_id=%s error=%s", session_id, e)
            context = []

        seeded: list[dict] = []
        for msg in context:
            if msg.role not in ("user", "assistant") or not msg.content:
                continue
            seeded.append({"role": msg.role, "content": msg.content})
        seeded.append(current)
        return seeded

    async def _is_thread_cold(self, session_id: str) -> bool:
        """checkpointer 中是否还没有该 thread 的存档

        查不到 checkpointer 时按「不冷」处理——注入与否是优化，
        checkpointer 本身通常完好，不该因探测能力缺失改变主流程。
        """
        checkpointer = getattr(self, "_checkpointer", None)
        if checkpointer is None:
            return False
        try:
            tup = await checkpointer.aget_tuple({"configurable": {"thread_id": session_id}})
        except Exception as e:
            logger.warning("检查 thread 冷启动失败，按热处理: session_id=%s error=%s", session_id, e)
            return False
        return tup is None

    @staticmethod
    def _thread_config(session_id: str, user_id: str) -> dict:
        """LangGraph 运行配置：thread_id 与 session_id 一致，会话隔离

        recursion_limit 是防失控闸门：小模型可能陷入「重复委派 / 反复重试
        失败工具」的循环，不加限制一条请求能跑上千步。超出后 LangGraph 抛
        GraphRecursionError，被 stream 的 except 捕获成 error 事件返回。
        """
        return {
            "configurable": {
                "thread_id": session_id,
                "context": {"user_id": user_id},
            },
            "recursion_limit": get_settings().AGENT_RECURSION_LIMIT,
        }

    # ==================== 流式 + 持久化（前端主链路） ====================

    async def stream_chat_persist(
        self,
        agent: Any,
        content: str,
        session_id: str,
        user_id: str,
        created: bool,
        on_complete: Any,
    ) -> AsyncIterator[str]:
        """流式聊天并在结束后持久化，yield SSE 格式字符串

        事件时序：meta → (content | thinking | tool_call | tool_result)*
        → done。done 必须在持久化完成之后发送——前端收到 done 会立刻
        刷新列表/历史，落库未完成就读会丢消息。done 携带 persisted 与
        message_count，供前端直接更新本地状态。

        客户端中途断连时不持久化半截回复（completed 标志位控制），
        避免落库一条没有结尾的助手消息。

        Args:
            on_complete: 异步回调，签名
                (assistant_text, tool_calls, thinking_steps) -> Optional[int]，
                返回最新 message_count。由路由层注入 persist_turn，
                使服务层不依赖 FastAPI 的 app 对象。
        """
        pool = await ensure_pool()
        await pool.acquire(session_id)
        tool_failure_breaker.reset(session_id)
        try:
            # —— 首帧：会话元数据（自动创建的 session_id 通过它回传前端） ——
            yield _format_sse({
                "type": "meta",
                "session_id": session_id,
                "created": created,
            })

            accumulated = ""
            tool_calls: list[ToolCall] = []
            thinking_steps: list[ThinkingStep] = []
            citations: list[Citation] = []
            seen_tool_call_ids: set[str] = set()
            completed = False

            try:
                messages = await self._seed_context(session_id, content)
                stream_config = self._thread_config(session_id, user_id)

                logger.info(
                    "开始流式聊天(持久化): user_id=%s session_id=%s 回灌消息=%s",
                    user_id, session_id, len(messages) - 1,
                )

                async for chunk_type, chunk_data in agent.astream(
                    {"messages": messages},
                    stream_config,
                    stream_mode=["messages", "updates"],
                ):
                    # —— content：模型 token 增量（打字机效果） ——
                    if chunk_type == "messages":
                        msg = chunk_data[0]
                        piece = self._extract_text(self._get_field(msg, "content"))
                        if piece:
                            # 累积口径与前端展示完全一致，保证「看到的」=「落库的」
                            accumulated += piece
                            yield _format_sse({"type": "content", "content": piece})

                    # —— updates：节点产物 → thinking / tool_call / tool_result ——
                    elif chunk_type == "updates":
                        for evt in self._parse_node_update(chunk_data):
                            if evt["type"] == "tool_call":
                                calls = evt["_calls"]
                                # messages 模式里模型分片可能重复携带同一批
                                # tool_calls，按 id 去重后再累计
                                for call in calls:
                                    key = call.id or f"{call.name}:{json.dumps(call.args, sort_keys=True, default=str)[:80]}"
                                    if key not in seen_tool_call_ids:
                                        seen_tool_call_ids.add(key)
                                        tool_calls.append(call)
                                        if call.name == "extract_web_content" and call.args.get("url"):
                                            citations.append(_citation_from_url(str(call.args["url"])))
                                yield _format_sse({
                                    "type": "tool_call",
                                    "id": [c.id for c in calls],
                                    "name": [c.name for c in calls],
                                    "args": [c.args for c in calls],
                                })
                            else:
                                if evt["type"] == "thinking":
                                    thinking_steps.append(
                                        ThinkingStep(node=evt["node"], summary=evt["summary"])
                                    )
                                yield _format_sse(evt)

                completed = True

            except Exception as e:
                logger.exception("流式聊天异常: session_id=%s", session_id)
                yield _format_sse({"type": "error", "message": str(e)})
                return

            if not completed:
                return

            # —— 流式推送完毕，执行三存储持久化 ——
            message_count: Optional[int] = None
            persisted = True
            try:
                message_count = await on_complete(accumulated, tool_calls, thinking_steps, citations)
                persisted = message_count is not None
            except Exception:
                logger.exception("流式对话持久化失败: session_id=%s", session_id)
                persisted = False

            if not persisted:
                logger.error(
                    "回复已推送但持久化失败，历史中可能缺失本轮: session_id=%s", session_id
                )

            yield _format_sse({
                "type": "done",
                "persisted": persisted,
                "message_count": message_count,
                "session_id": session_id,
            })
        finally:
            pass
            # await pool.release(session_id)

    @staticmethod
    def _parse_node_update(chunk_data: Any) -> list[dict]:
        """把一个 updates 节点产物解析为 0..n 个 SSE 事件

        节点输出 {"messages": [...]} 中可能包含：
        - AIMessage(tool_calls=[...]) → tool_call 事件
        - ToolMessage → tool_result 事件
        其余状态更新（非消息键）→ thinking 事件。
        """
        events: list[dict] = []
        if not isinstance(chunk_data, dict):
            return events

        for node_name, node_output in chunk_data.items():
            if node_name in ("__start__", "__end__"):
                continue

            node_messages = node_output.get("messages") if isinstance(node_output, dict) else None
            if node_messages:
                for msg in node_messages:
                    msg_type = type(msg).__name__
                    calls = ChatService._extract_tool_calls(ChatService._get_field(msg, "tool_calls"))
                    if calls:
                        events.append({"type": "tool_call", "_calls": calls})
                    elif msg_type == "ToolMessage":
                        events.append({
                            "type": "tool_result",
                            "tool_call_id": ChatService._get_field(msg, "tool_call_id"),
                            "name": ChatService._get_field(msg, "name"),
                            "content": ChatService._extract_text(
                                ChatService._get_field(msg, "content")
                            )[:_TOOL_RESULT_PREVIEW_LIMIT],
                        })
                    else:
                        text = ChatService._extract_text(ChatService._get_field(msg, "content"))
                        if text.strip():
                            events.append({
                                "type": "thinking",
                                "node": node_name,
                                "summary": text[:_STEP_SUMMARY_LIMIT],
                            })
            else:
                events.append({
                    "type": "thinking",
                    "node": node_name,
                    "summary": str(node_output)[:_STEP_SUMMARY_LIMIT],
                })
        return events

    # ==================== 回复解析辅助 ====================

    @staticmethod
    def _last_message(result: Any) -> Optional[Any]:
        """从 Agent 返回值中取出最后一条消息"""
        if not isinstance(result, dict):
            return None
        messages = result.get("messages")
        if not messages:
            return None
        return messages[-1]

    @staticmethod
    def _get_field(message: Any, name: str) -> Any:
        """兼容对象属性与字典两种消息形态"""
        if isinstance(message, dict):
            return message.get(name)
        return getattr(message, name, None)

    @staticmethod
    def _extract_text(content: Any) -> str:
        """把消息正文归一化为字符串

        LangChain 的 content 可能是 str，也可能是多模态分块列表
        （如 [{"type": "text", "text": "..."}]），两种都要能处理。
        """
        if content is None:
            return ""
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    text = block.get("text") or block.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)

        return str(content)

    @staticmethod
    def _extract_tool_calls(raw: Any) -> list[ToolCall]:
        """把 Agent 产出的工具调用转换为落库模型"""
        if not raw or not isinstance(raw, list):
            return []

        calls: list[ToolCall] = []
        for item in raw:
            if isinstance(item, ToolCall):
                calls.append(item)
                continue
            if isinstance(item, dict):
                calls.append(
                    ToolCall(
                        id=item.get("id"),
                        name=item.get("name"),
                        args=item.get("args") or {},
                    )
                )
        return calls


def _citation_from_url(url: str) -> Citation:
    """从检索 URL 构造引用来源。title 用域名占位，前端展示为参考链接。"""
    domain = url
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
            break
    domain = domain.split("/")[0]
    return Citation(title=domain, url=url, snippet="")


def _format_sse(data: dict) -> str:
    """格式化为 SSE data 帧（ensure_ascii=False 保证中文原样传输）"""
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


__all__ = [
    "AgentNotFoundError",
    "ChatService",
    "ChatTurnResult",
    "SessionEndedHTTPError",
]
