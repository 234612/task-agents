import { useState, useCallback, useRef } from 'react';
import { Message, AgentRole } from '@/types';

interface UseAgentChatOptions {
  onStreamUpdate?: (messageId: string, content: string) => void;
}

// ==================== 硬编码配置 ====================
// 本阶段临时配置，后续会从后端动态获取或从用户登录状态读取
const HARDCODED_USER_ID = 'userid_1';
const HARDCODED_SESSION_ID = 'session_1';
const API_BASE_URL = 'http://localhost:8000'; // FastAPI 后端地址

// Agent 角色映射表：前端 AgentRole -> 后端 agent_key
const AGENT_KEY_MAP: Record<AgentRole, string> = {
  researcher: 'market_researcher',
  coder: 'market_researcher',      // 暂时都映射到 market_researcher
  reviewer: 'market_researcher',   // 后续可以扩展其他 agent
};

export function useAgentChat(options?: UseAgentChatOptions) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (content: string, agentRole: AgentRole) => {
      const userMessage: Message = {
        id: `user-${Date.now()}`,
        role: 'user',
        content,
        timestamp: Date.now(),
      };

      setMessages((prev) => [...prev, userMessage]);
      setIsLoading(true);

      const assistantMessageId = `assistant-${Date.now()}`;
      const assistantMessage: Message = {
        id: assistantMessageId,
        role: 'assistant',
        content: '',
        agentRole,
        timestamp: Date.now(),
      };

      setMessages((prev) => [...prev, assistantMessage]);

      try {
        abortControllerRef.current = new AbortController();

        // 获取后端 agent_key
        const agentKey = AGENT_KEY_MAP[agentRole];

        // 构建请求体（匹配后端 ChatRequest 模型）
        const requestBody = {
          content,
          agent_key: agentKey,
          session_id: HARDCODED_SESSION_ID,
          user_id: HARDCODED_USER_ID,
        };

        const response = await fetch(`${API_BASE_URL}/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestBody),
          signal: abortControllerRef.current.signal,
        });

        if (!response.ok) throw new Error('请求失败');
        if (!response.body) throw new Error('无响应体');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let accumulated = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          const lines = chunk.split('\n');

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6).trim();
              if (data === '[DONE]') continue;
              
              try {
                const parsed = JSON.parse(data);
                
                // 处理不同类型的 SSE 事件
                if (parsed.type === 'content') {
                  accumulated += parsed.content;
                  setMessages((prev) =>
                    prev.map((msg) =>
                      msg.id === assistantMessageId
                        ? { ...msg, content: accumulated }
                        : msg
                    )
                  );
                  options?.onStreamUpdate?.(assistantMessageId, accumulated);
                } else if (parsed.type === 'node_update') {
                  // 可选：处理节点更新事件（思考链展示）
                  console.log('[Node Update]', parsed.node, parsed.data);
                } else if (parsed.type === 'error') {
                  throw new Error(parsed.message);
                }
                // type === 'done' 时循环自然结束
              } catch (parseError) {
                // 忽略解析错误，继续处理下一条
                console.warn('SSE 解析错误:', parseError, data);
              }
            }
          }
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return;
        }
        const errorMessage = error instanceof Error ? error.message : '未知错误';
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMessageId
              ? { ...msg, content: `抱歉，发生了错误：${errorMessage}` }
              : msg
          )
        );
      } finally {
        setIsLoading(false);
        abortControllerRef.current = null;
      }
    },
    [options]
  );

  const stopGeneration = useCallback(() => {
    abortControllerRef.current?.abort();
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    abortControllerRef.current?.abort();
    setIsLoading(false);
  }, []);

  return {
    messages,
    isLoading,
    sendMessage,
    stopGeneration,
    clearMessages,
  };
}
