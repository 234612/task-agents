"""聊天服务层 — Agent 调用与消息编排

两条调用路径：

1. invoke_chat（新增，服务 POST /api/chat）
   复用现有 LangGraph Agent，以 ainvoke 取完整回复，然后交给
   MessageWriteService 做三存储写入（Redis 同步 / MySQL 同步 / MongoDB 异步）。

2. stream_chat（保留，服务 POST /chat）
   既有 SSE 流式接口，前端 useAgentChat.ts 仍在调用，行为保持不变。

记忆机制说明：Agent 使用 MemorySaver（进程内）作为 checkpointer，以
thread_id=session_id 维持自身对话状态；Redis 上下文是并行的旁路快照，
用于为后续 Prompt 注入提供低延迟读取，二者互不替代。
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from task_agents.schemas.mongo import StoredMessage, ToolCall
from task_agents.service.message_service import MessageWriteService, TurnRecord

logger = logging.getLogger(__name__)


class AgentNotFoundError(Exception):
    """指定的 agent_key 未注册"""


@dataclass
class ChatTurnResult:
    """一次聊天的处理结果，供 Router 层组装响应与调度后台任务"""
    session_id: str
    user_message: StoredMessage
    assistant_message: StoredMessage
    message_count: Optional[int]
    is_first_turn: bool = False
    tool_calls: list[ToolCall] = field(default_factory=list)

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
        message_service: Optional[MessageWriteService],
        agent_getter: Any,
        title_generator: Optional[Any] = None,
        session_service: Optional[Any] = None,
    ):
        """
        Args:
            message_service: 三存储写入编排服务。仅 stream_chat（SSE）路径可以为
                             None，因为该路径不做持久化；invoke_chat 必须提供，
                             否则会在写入阶段抛出 RuntimeError。
            agent_getter: 可调用对象，签名 (agent_key) -> CompiledStateGraph
            title_generator: 可调用对象，签名 (content) -> str，用于 LLM 生成标题；
                             为 None 时跳过标题生成
            session_service: SessionService，invoke_chat 用于校验会话归属；
                             SSE 路径可为 None
        """
        self._messages = message_service
        self._agent_getter = agent_getter
        self._title_generator = title_generator
        self._session_service = session_service

    # ==================== 主路径：同步调用 Agent ====================

    async def invoke_chat(
        self,
        session_id: str,
        user_id: str,
        agent_key: str,
        content: str,
    ) -> ChatTurnResult:
        """调用 Agent 生成回复并完成三存储写入

        流程：
        1. 校验会话存在且归属当前用户（越权则抛 SessionAccessDeniedError）
        2. 取 Agent 实例（未注册则抛 AgentNotFoundError）
        3. 判定是否首轮（用于决定是否触发标题生成）
        4. ainvoke 取完整回复，解析正文与工具调用
        5. 交给 MessageWriteService 写 Redis + MySQL，返回待落 Mongo 的消息

        归属校验必须在任何写入之前完成，否则任意用户拿到他人 session_id 就能
        往其会话里写消息，造成数据污染与隐私泄露。

        Agent 调用失败时直接向上抛出，由 Router 转成 5xx；此时不写任何存储，
        避免落下「有用户消息但没有回复」的半轮对话。
        """
        if self._messages is None:
            raise RuntimeError(
                "invoke_chat 需要 MessageWriteService，当前实例未装配（仅 SSE 流式路径允许为空）"
            )

        if self._session_service is None:
            raise RuntimeError(
                "invoke_chat 需要 SessionService 以校验会话归属，当前实例未装配"
            )

        # —— 1. 归属校验：会话必须存在且属于该用户 ——
        await self.ensure_session_owned(session_id, user_id)

        agent = self.resolve_agent(agent_key)

        is_first_turn = await self._is_first_turn(session_id)

        logger.info(
            "开始调用 Agent: session_id=%s user_id=%s agent_key=%s 首轮=%s",
            session_id, user_id, agent_key, is_first_turn,
        )

        reply_text, tool_calls = await self._invoke_agent(agent, session_id, user_id, content)

        turn: TurnRecord = await self._messages.record_turn(
            session_id=session_id,
            user_id=user_id,
            user_content=content,
            assistant_content=reply_text,
            assistant_tool_calls=tool_calls,
        )

        logger.info(
            "Agent 回复已写入 Redis+MySQL: session_id=%s 回复长度=%s 工具调用=%s",
            session_id, len(reply_text), len(tool_calls),
        )

        return ChatTurnResult(
            session_id=session_id,
            user_message=turn.user_message,
            assistant_message=turn.assistant_message,
            message_count=turn.message_count,
            is_first_turn=is_first_turn,
            tool_calls=tool_calls,
        )

    async def _is_first_turn(self, session_id: str) -> bool:
        """判断本轮是否为会话首轮（首轮才需要生成标题）

        以 MySQL message_count 为准而非 Redis 上下文长度：Redis 有 TTL 且属于
        可失效的加速层，过期或故障时长度会变 0，导致老会话被误判为首轮并重复
        覆盖已由 LLM 生成的标题。
        """
        return await self._messages.message_count(session_id) == 0

    async def _invoke_agent(
        self,
        agent: Any,
        session_id: str,
        user_id: str,
        content: str,
    ) -> tuple[str, list[ToolCall]]:
        """调用 Agent 并解析回复正文与工具调用"""
        stream_input = {"messages": [{"role": "user", "content": content}]}
        stream_config = {
            "configurable": {
                # thread_id 与 session_id 一致，Agent 侧记忆按会话隔离
                "thread_id": session_id,
                "context": {"user_id": user_id},
            }
        }

        result = await agent.ainvoke(stream_input, stream_config)
        last_message = self._last_message(result)

        if last_message is None:
            logger.warning("Agent 未返回消息: session_id=%s", session_id)
            return "", []

        return (
            self._extract_text(self._get_field(last_message, "content")),
            self._extract_tool_calls(self._get_field(last_message, "tool_calls")),
        )

    # ==================== 回复解析辅助 ====================

    @staticmethod
    def _last_message(result: Any) -> Optional[Any]:
        """从 Agent 返回值中取出最后一条消息

        LangGraph 的 ainvoke 返回状态字典，消息在 "messages" 键下；
        这里对非字典返回也做兜底，避免 Agent 实现变化时直接崩溃。
        """
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
        if not raw:
            return []
        if not isinstance(raw, list):
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

    # ==================== 后台任务：LLM 生成标题 ====================

    async def generate_title(self, session_id: str, user_id: str, content: str) -> Optional[str]:
        """首轮对话后由 LLM 生成会话标题（后台任务，不阻塞响应）

        失败时返回 None 并保留占位标题，不影响聊天主流程。
        """
        if self._title_generator is None:
            return None

        try:
            title = await self._title_generator(content)
        except Exception:
            logger.exception("LLM 生成标题失败，保留占位标题: session_id=%s", session_id)
            return None

        title = (title or "").strip()
        if not title:
            return None

        # 防御 LLM 输出过长标题，截断到列宽以内
        title = title[:200]
        logger.info("标题生成完成: session_id=%s title=%s", session_id, title)
        return title

    # ==================== 保留路径：SSE 流式聊天 ====================

    async def stream_chat(
        self,
        agent: Any,
        content: str,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """发起一次流式聊天，yield SSE 格式字符串

        供既有 POST /chat 接口使用（前端 useAgentChat.ts 依赖此格式），
        行为与原实现保持一致。
        """

        def format_sse(data: dict) -> str:
            return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        try:
            stream_input = {"messages": [{"role": "user", "content": content}]}
            stream_config = {
                "configurable": {
                    "thread_id": session_id,
                    "context": {"user_id": user_id or ""},
                }
            }

            logger.info("开始流式聊天: user_id=%s session_id=%s", user_id, session_id)

            async for chunk_type, chunk_data in agent.astream(
                stream_input,
                stream_config,
                stream_mode=["messages", "updates"],
            ):
                # 模式A：模型回复内容（前端打字机效果）
                if chunk_type == "messages":
                    msg = chunk_data[0]
                    if hasattr(msg, "content") and msg.content:
                        yield format_sse({"type": "content", "content": msg.content})

                # 模式B：节点状态更新（前端展示思考链）
                elif chunk_type == "updates":
                    node_name = list(chunk_data.keys())[0]
                    if node_name not in ("__start__", "__end__"):
                        node_output = chunk_data[node_name]
                        yield format_sse({
                            "type": "node_update",
                            "node": node_name,
                            "data": str(node_output)[:200],
                        })

            yield format_sse({"type": "done"})

        except Exception as e:
            logger.exception("流式聊天异常: %s", e)
            yield format_sse({"type": "error", "message": str(e)})


    # ==================== 前置校验（供流式路由复用） ====================

    async def ensure_session_owned(self, session_id: str, user_id: str) -> None:
        """校验会话存在且归属该用户

        供 SSE 流式路由在返回 StreamingResponse **之前**调用——响应一旦开始
        发送，HTTP 状态码已固定，越权与不存在就再无法正确表达。

        Raises:
            SessionNotFoundError: 会话不存在或已软删除
            SessionAccessDeniedError: 会话属于其他用户
            RuntimeError: 未装配 session_service
        """
        if self._session_service is None:
            raise RuntimeError(
                "ensure_session_owned 需要 SessionService，当前实例未装配"
            )
        await self._session_service.get_session_for_user(session_id, user_id)

    def resolve_agent(self, agent_key: str) -> Any:
        """按 key 获取 Agent 实例（同步版，供路由在流式响应前做前置校验）

        Raises:
            AgentNotFoundError: agent_key 未注册
        """
        try:
            return self._agent_getter(agent_key)
        except KeyError as e:
            raise AgentNotFoundError(str(e)) from e

    # ==================== 流式 + 持久化（前端主链路） ====================

    async def stream_chat_persist(
        self,
        agent: Any,
        content: str,
        session_id: str,
        user_id: str,
        on_complete: Any,
    ) -> AsyncIterator[str]:
        """流式聊天并在结束后持久化，yield SSE 格式字符串

        与 stream_chat 的区别：本方法在推送完回复后执行三存储写入，
        让前端既能看到打字机效果，刷新后又能从 MongoDB 读回历史。

        关键时序：done 事件必须在持久化**完成之后**才发送。前端收到 done
        通常会立刻刷新会话列表/历史，若落库尚未完成就会读到旧数据，出现
        "刚发的消息不见了"的竞态。done 事件携带 persisted 与 message_count，
        供前端直接更新本地状态，省去一次列表请求。

        客户端中途断连时不持久化半截回复（用 completed 标志位控制），
        避免落库一条没有结尾的助手消息。

        Args:
            on_complete: 异步回调，签名 (assistant_text, tool_calls) -> Optional[int]，
                         返回最新 message_count。由路由层注入 persist_turn，
                         使服务层不依赖 FastAPI 的 app 对象。
        """
        accumulated = ""
        tool_calls: list[ToolCall] = []
        completed = False

        try:
            stream_input = {"messages": [{"role": "user", "content": content}]}
            stream_config = {
                "configurable": {
                    # thread_id 与 session_id 一致：新建会话即新建 thread
                    "thread_id": session_id,
                    "context": {"user_id": user_id},
                }
            }

            logger.info(
                "开始流式聊天(持久化): user_id=%s session_id=%s", user_id, session_id
            )

            async for chunk_type, chunk_data in agent.astream(
                stream_input,
                stream_config,
                stream_mode=["messages", "updates"],
            ):
                # 模式A：模型回复增量（前端打字机效果）
                if chunk_type == "messages":
                    msg = chunk_data[0]
                    piece = self._extract_text(self._get_field(msg, "content"))
                    if piece:
                        # 累积口径与前端 accumulated 完全一致，
                        # 保证「屏幕上看到的」和「落库的」是同一份文本
                        accumulated += piece
                        yield _format_sse({"type": "content", "content": piece})

                    # 工具调用：流式 chunk 可能分片，这里取最后一次非空的完整值
                    calls = self._extract_tool_calls(self._get_field(msg, "tool_calls"))
                    if calls:
                        tool_calls = calls

                # 模式B：节点状态更新（前端展示思考链）
                elif chunk_type == "updates":
                    node_name = list(chunk_data.keys())[0]
                    if node_name not in ("__start__", "__end__"):
                        node_output = chunk_data[node_name]
                        yield _format_sse({
                            "type": "node_update",
                            "node": node_name,
                            "data": str(node_output)[:200],
                        })

            completed = True

        except Exception as e:
            logger.exception("流式聊天异常: session_id=%s", session_id)
            yield _format_sse({"type": "error", "message": str(e)})
            return

        if not completed:
            # 理论上到不了这里：异常已 return，客户端断连会触发 CancelledError
            # 直接冒泡（刻意不捕获，避免把取消当成功持久化半截回复）
            return

        # —— 流式推送完毕，执行三存储持久化 ——
        message_count: Optional[int] = None
        persisted = True
        try:
            message_count = await on_complete(accumulated, tool_calls)
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
        })


def _format_sse(data: dict) -> str:
    """格式化为 SSE data 帧（ensure_ascii=False 保证中文原样传输）"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


__all__ = ["AgentNotFoundError", "ChatService", "ChatTurnResult"]
