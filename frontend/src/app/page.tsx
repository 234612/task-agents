'use client';

import { useState, useCallback } from 'react';
import { Sidebar } from '@/components/layout/Sidebar';
import { ChatArea } from '@/components/chat/ChatArea';
import { useAgentChat } from '@/hooks/useAgentChat';
import { AgentRole, ChatSession } from '@/types';

// Mock session data
const MOCK_SESSIONS: ChatSession[] = [
  { id: '1', title: 'AI Agent 架构讨论', lastMessageAt: Date.now() - 3600000 },
  { id: '2', title: 'Python 代码优化', lastMessageAt: Date.now() - 86400000 },
  { id: '3', title: '市场调研报告', lastMessageAt: Date.now() - 172800000 },
  { id: '4', title: 'API 接口设计评审', lastMessageAt: Date.now() - 259200000 },
];

export default function Home() {
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [currentAgentRole, setCurrentAgentRole] =
    useState<AgentRole>('researcher');
  const [sessions, setSessions] = useState<ChatSession[]>(MOCK_SESSIONS);

  const { messages, isLoading, sendMessage, stopGeneration, clearMessages } =
    useAgentChat();

  const handleNewChat = useCallback(() => {
    clearMessages();
    setActiveSessionId(null);
  }, [clearMessages]);

  const handleSelectSession = useCallback((id: string) => {
    setActiveSessionId(id);
    // In real app: load session messages from API
    clearMessages();
  }, [clearMessages]);

  const handleSend = useCallback(
    (content: string, agentRole: AgentRole) => {
      sendMessage(content, agentRole);
      setCurrentAgentRole(agentRole);
    },
    [sendMessage]
  );

  const handleAgentChange = useCallback((role: AgentRole) => {
    setCurrentAgentRole(role);
  }, []);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-white">
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onNewChat={handleNewChat}
        onSelectSession={handleSelectSession}
      />
      <ChatArea
        messages={messages}
        isLoading={isLoading}
        currentAgentRole={currentAgentRole}
        onAgentChange={handleAgentChange}
        onSend={handleSend}
        onStop={stopGeneration}
      />
    </div>
  );
}