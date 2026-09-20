"""
ChatService — 聊天服务层
负责组装 runtime 配置、调用 Agent 并以 SSE 格式返回流式响应。
"""

import json
import logging
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


class ChatService:
    """
    封装一次聊天请求的完整生命周期：
    1. 根据 agent_key 获取 Agent 实例
    2. 组装 runtime config（user_id、thread_id）
    3. 调用 agent.astream() 流式生成
    4. 将输出格式化为 SSE 事件
    """

    def __init__(self, user_id: str):
        self.user_id = user_id

    async def stream_chat(
        self,
        agent: Any,
        content: str,
        session_id: str,
    ) -> AsyncIterator[str]:
        """
        发起一次流式聊天，yield SSE 格式的字符串。

        Args:
            agent: 从 factory 获取的 CompiledStateGraph 实例
            content: 用户输入的消息内容
            session_id: 会话 ID，用于 checkpoint 持久化

        Yields:
            SSE 格式的 data: {...}\n\n 字符串
        """

        def format_sse(data: dict) -> str:
            return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        try:
            # 1. 组装 runtime 配置
            stream_input = {
                "messages": [{"role": "user", "content": content}]
            }
            stream_config = {
                "configurable": {
                    "thread_id": session_id,
                    "context": {
                        "user_id": self.user_id,
                    },
                }
            }

            logger.info(
                "开始流式聊天: user_id=%s, session_id=%s",
                self.user_id,
                session_id,
            )

            # 2. 调用 Agent 流式生成
            async for chunk_type, chunk_data in agent.astream(
                stream_input,
                stream_config,
                stream_mode=["messages", "updates"],
            ):
                # 模式A：模型回复内容（前端打字机效果）
                if chunk_type == "messages":
                    msg = chunk_data[0]
                    if hasattr(msg, "content") and msg.content:
                        yield format_sse({
                            "type": "content",
                            "content": msg.content,
                        })

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

            # 3. 发送结束标志
            yield format_sse({"type": "done"})

        except Exception as e:
            logger.exception("流式聊天异常: %s", e)
            yield format_sse({
                "type": "error",
                "message": str(e),
            })
