'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { ChatArea } from '@/components/chat/ChatArea';
import { Sidebar } from '@/components/layout/Sidebar';
import { useAgentChat } from '@/hooks/useAgentChat';
import { useSessions } from '@/hooks/useSessions';
import { CURRENT_USER_ID } from '@/lib/config';
import { AGENT_KEY_MAP, type AgentRole } from '@/types';

export default function Home() {
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [currentAgentRole, setCurrentAgentRole] = useState<AgentRole>('researcher');

  const {
    sessions,
    isLoading: isLoadingSessions,
    error: sessionError,
    createNewSession,
    markSessionActive,
    removeSession,
  } = useSessions();

  // 一轮对话完成（服务端已持久化）后，把该会话移到侧边栏首位并更新计数
  const handleTurnCompleted = useCallback(
    (sessionId: string, messageCount: number | null) => {
      markSessionActive(sessionId, messageCount);
    },
    [markSessionActive]
  );

  const {
    messages,
    isLoading,
    isSwitching,
    error: chatError,
    sendMessage,
    loadHistory,
    clearMessages,
    stopGeneration,
  } = useAgentChat({ onTurnCompleted: handleTurnCompleted });

  /** 新建对话：仅清空视图，不立即创建会话 */
  const handleNewChat = useCallback(() => {
    clearMessages();
    setActiveSessionId(null);
  }, [clearMessages]);

  /** 点击侧边栏会话：加载该会话的完整历史 */
  const handleSelectSession = useCallback(
    (id: string) => {
      if (id === activeSessionId) return;
      setActiveSessionId(id);
      void loadHistory(id, currentAgentRole);
    },
    [activeSessionId, currentAgentRole, loadHistory]
  );

  /**
   * 发送消息
   *
   * 懒创建：若当前没有活跃会话，先调 POST /api/sessions 建会话，
   * 拿到 session_id（即 Agent 的 thread_id）后再发消息。
   * 这样点「新建对话」不会在侧边栏留下一堆空会话。
   */
  const handleSend = useCallback(
    async (content: string, agentRole: AgentRole) => {
      setCurrentAgentRole(agentRole);

      let sessionId = activeSessionId;

      if (!sessionId) {
        try {
          const session = await createNewSession(AGENT_KEY_MAP[agentRole]);
          sessionId = session.id;
          setActiveSessionId(session.id);
        } catch (err) {
          // 建会话失败就无法发消息，直接提示，不做静默降级
          console.error('创建会话失败', err);
          return;
        }
      }

      sendMessage(sessionId, content, agentRole);
    },
    [activeSessionId, createNewSession, sendMessage]
  );

  const handleAgentChange = useCallback((role: AgentRole) => {
    setCurrentAgentRole(role);
  }, []);

  const handleDeleteSession = useCallback(
    async (id: string) => {
      try {
        await removeSession(id);
        // 删掉的正是当前打开的会话时，回到新建对话状态
        if (id === activeSessionId) {
          setActiveSessionId(null);
          clearMessages();
        }
      } catch (err) {
        console.error('删除会话失败', err);
      }
    },
    [activeSessionId, clearMessages, removeSession]
  );

  // 首次进入自动选中最近的会话，让演示数据直接可见。
  //
  // 用一次性标志而非 [sessions.length] 依赖：发消息会创建新会话使长度变化，
  // 若 effect 再次触发就会调 loadHistory，而 loadHistory 内部会中断正在进行的
  // SSE 流，导致「首次发消息时输出被打断」。故仅在首轮加载完成时执行一次，
  // 无论当时列表是否为空都置位标志。
  const autoSelectedRef = useRef(false);
  useEffect(() => {
    if (autoSelectedRef.current || isLoadingSessions) return;

    autoSelectedRef.current = true;
    if (sessions.length === 0) return;

    const first = sessions[0];
    setActiveSessionId(first.id);
    void loadHistory(first.id, currentAgentRole);
    // 只在首次加载完成时运行一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoadingSessions, sessions]);

  const errorMessage = chatError ?? sessionError;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-white">
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        isLoading={isLoadingSessions}
        userId={CURRENT_USER_ID}
        onNewChat={handleNewChat}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
      />
      <ChatArea
        messages={messages}
        isLoading={isLoading}
        isSwitching={isSwitching}
        errorMessage={errorMessage}
        currentAgentRole={currentAgentRole}
        activeSessionId={activeSessionId}
        onAgentChange={handleAgentChange}
        onSend={handleSend}
        onStop={stopGeneration}
      />
    </div>
  );
}
