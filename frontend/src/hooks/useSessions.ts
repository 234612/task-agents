'use client';

/**
 * 会话列表管理 Hook
 *
 * 负责侧边栏数据：拉取列表、新建会话、删除会话、本地乐观更新。
 * user_id 现阶段由 lib/config.ts 统一 hardcode 提供（登录功能留待下一阶段）。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  createSession,
  deleteSession,
  fetchSessions,
  ApiError,
} from '@/lib/api';
import { CURRENT_USER_ID, SESSION_PAGE_SIZE } from '@/lib/config';
import type { ChatSession } from '@/types';

export function useSessions() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 卸载标志：避免组件卸载后 setState 触发 React 警告
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // 会话列表镜像：供乐观更新的回滚读取最新值。
  // 不能依赖 setSessions 的 updater 参数取快照——updater 不保证同步执行，
  // 请求失败时读到的可能仍是空数组，导致回滚后列表被清空。
  const sessionsRef = useRef<ChatSession[]>([]);
  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);

  /** 从服务端拉取会话列表（按最后活跃时间倒序） */
  const loadSessions = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const { items } = await fetchSessions(1, SESSION_PAGE_SIZE, CURRENT_USER_ID);
      if (mountedRef.current) setSessions(items);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : '加载会话列表失败';
      if (mountedRef.current) {
        setError(message);
        setSessions([]);
      }
    } finally {
      if (mountedRef.current) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  /** 新建会话：返回带 session_id 的会话，该 ID 即 Agent 的 thread_id */
  const createNewSession = useCallback(
    async (agentKey: string): Promise<ChatSession> => {
      const session = await createSession(agentKey, CURRENT_USER_ID);
      if (mountedRef.current) {
        // 新会话插到列表首位（它此刻就是最活跃的）
        setSessions((prev) => [session, ...prev.filter((s) => s.id !== session.id)]);
      }
      return session;
    },
    []
  );

  /**
   * 会话有新消息后，把它移到列表首位并更新计数与时间
   *
   * 本地直接更新而非重新拉列表：用户刚看到自己发的消息，
   * 侧边栏应当立刻响应，不必等一次网络往返。
   */
  const markSessionActive = useCallback(
    (sessionId: string, messageCount?: number | null) => {
      if (!mountedRef.current) return;
      setSessions((prev) => {
        const target = prev.find((s) => s.id === sessionId);
        if (!target) return prev;

        const updated: ChatSession = {
          ...target,
          lastMessageAt: Date.now(),
          ...(messageCount != null ? { messageCount } : {}),
        };
        return [updated, ...prev.filter((s) => s.id !== sessionId)];
      });
    },
    []
  );

  /** 首轮对话后标题由 LLM 生成，这里用于回写本地标题 */
  const updateSessionTitle = useCallback((sessionId: string, title: string) => {
    if (!mountedRef.current) return;
    setSessions((prev) =>
      prev.map((s) => (s.id === sessionId ? { ...s, title } : s))
    );
  }, []);

  /** 删除会话（软删除），成功后从本地列表移除 */
  const removeSession = useCallback(async (sessionId: string) => {
    // 乐观更新：先移除，失败再用镜像快照回滚
    const snapshot = sessionsRef.current;
    setSessions((prev) => prev.filter((s) => s.id !== sessionId));

    try {
      await deleteSession(sessionId, CURRENT_USER_ID);
    } catch (err) {
      if (mountedRef.current) {
        setSessions(snapshot);
        setError(err instanceof ApiError ? err.message : '删除会话失败');
      }
      throw err;
    }
  }, []);

  return {
    sessions,
    isLoading,
    error,
    loadSessions,
    createNewSession,
    markSessionActive,
    updateSessionTitle,
    removeSession,
    clearError: useCallback(() => setError(null), []),
  };
}
