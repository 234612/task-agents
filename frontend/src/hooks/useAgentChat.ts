'use client';

/**
 * 聊天消息管理 Hook
 *
 * 职责：维护当前会话的消息列表、驱动 SSE 流式接收、加载历史消息。
 * 会话的创建由 page.tsx 编排（懒创建），本 Hook 只接收已存在的 sessionId，
 * 避免「创建会话」的逻辑分散在两处。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchMessages, streamChat } from '@/lib/api';
import { CURRENT_USER_ID } from '@/lib/config';
import { AGENT_KEY_MAP, type AgentRole, type Message } from '@/types';

interface UseAgentChatOptions {
  /** 一轮对话完成且服务端已持久化后回调，父组件用它刷新侧边栏顺序与计数 */
  onTurnCompleted?: (sessionId: string, messageCount: number | null) => void;
}

export function useAgentChat(options: UseAgentChatOptions = {}) {
  const { onTurnCompleted } = options;

  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSwitching, setIsSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  /**
   * 流令牌：每次开始新流、切换会话、清空消息时自增。
   * 所有异步回调先比对令牌，不一致就丢弃。
   *
   * 这解决一个真实的竞态：用户在 A 会话流式输出途中切到 B 会话，
   * A 的增量回调若不失效，就会把 A 的内容写进 B 的消息列表。
   */
  const tokenRef = useRef(0);

  // 回调用 ref 持有，避免因父组件传入新函数而重建 sendMessage
  const onTurnCompletedRef = useRef(onTurnCompleted);
  useEffect(() => {
    onTurnCompletedRef.current = onTurnCompleted;
  }, [onTurnCompleted]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  /** 作废当前流：自增令牌并中断网络请求 */
  const invalidateStream = useCallback(() => {
    tokenRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  /** 加载指定会话的历史消息（点击侧边栏时调用） */
  const loadHistory = useCallback(
    async (sessionId: string, agentRole: AgentRole) => {
      invalidateStream();
      setIsSwitching(true);
      setError(null);
      const token = tokenRef.current;

      try {
        const history = await fetchMessages(sessionId, agentRole, CURRENT_USER_ID);
        // 令牌变化说明期间又切换了会话，丢弃这次结果
        if (!mountedRef.current || token !== tokenRef.current) return;
        setMessages(history);
      } catch (err) {
        if (!mountedRef.current || token !== tokenRef.current) return;
        setMessages([]);
        setError(err instanceof Error ? err.message : '加载历史消息失败');
      } finally {
        if (mountedRef.current && token === tokenRef.current) setIsSwitching(false);
      }
    },
    [invalidateStream]
  );

  /** 清空消息（新建对话时调用，此时尚无会话） */
  const clearMessages = useCallback(() => {
    invalidateStream();
    setMessages([]);
    setIsLoading(false);
    setError(null);
  }, [invalidateStream]);

  /** 停止生成：中断 SSE 流 */
  const stopGeneration = useCallback(() => {
    invalidateStream();
    if (mountedRef.current) {
      setIsLoading(false);
      // 把仍在流式态的助手消息标记为已完成，避免指示器一直转
      setMessages((prev) =>
        prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m))
      );
    }
  }, [invalidateStream]);

  /**
   * 发送消息
   *
   * 流程：本地乐观插入用户消息与助手占位 → 建立 SSE 流 →
   * 增量累积到占位消息 → done 事件后通知父组件刷新侧边栏。
   *
   * 后端 done 事件在持久化完成之后才下发，因此此处回调父组件时，
   * 刷新历史一定能读到本轮消息，不存在「刚发的消息看不到」的竞态。
   */
  const sendMessage = useCallback(
    (sessionId: string, content: string, agentRole: AgentRole) => {
      invalidateStream();
      const token = tokenRef.current;

      const now = Date.now();
      const userMessageId = `local-user-${now}`;
      const assistantMessageId = `local-assistant-${now}`;

      const userMessage: Message = {
        id: userMessageId,
        role: 'user',
        content,
        timestamp: now,
      };
      const assistantMessage: Message = {
        id: assistantMessageId,
        role: 'assistant',
        content: '',
        agentRole,
        timestamp: Date.now(),
        isStreaming: true,
      };

      setError(null);
      setIsLoading(true);
      setMessages((prev) => [...prev, userMessage, assistantMessage]);

      const controller = streamChat(
        {
          sessionId,
          userId: CURRENT_USER_ID,
          agentKey: AGENT_KEY_MAP[agentRole],
          content,
        },
        {
          onContent: (piece) => {
            if (!mountedRef.current || token !== tokenRef.current) return;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessageId ? { ...m, content: m.content + piece } : m
              )
            );
          },
          onDone: (done) => {
            if (!mountedRef.current || token !== tokenRef.current) return;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessageId ? { ...m, isStreaming: false } : m
              )
            );
            setIsLoading(false);
            onTurnCompletedRef.current?.(sessionId, done.message_count);
          },
          onError: (message) => {
            if (!mountedRef.current || token !== tokenRef.current) return;
            setIsLoading(false);
            setError(message);
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessageId
                  ? {
                      ...m,
                      isStreaming: false,
                      // 没有任何增量就失败时，占位气泡不该留空
                      content: m.content || `抱歉，发生了错误：${message}`,
                    }
                  : m
              )
            );
          },
        }
      );

      abortRef.current = controller;
    },
    [invalidateStream]
  );

  return {
    messages,
    isLoading,
    /** 正在加载历史（切换会话时），用于显示加载态而非空白欢迎页 */
    isSwitching,
    error,
    sendMessage,
    loadHistory,
    clearMessages,
    stopGeneration,
    clearError: useCallback(() => setError(null), []),
  };
}
