'use client';

import { useEffect, useRef } from 'react';
import { ChevronDown, Bot } from 'lucide-react';
import { Message, AgentRole, AGENTS } from '@/types';
import { MessageBubble } from './MessageBubble';
import { InputArea } from './InputArea';

interface ChatAreaProps {
  messages: Message[];
  isLoading: boolean;
  currentAgentRole: AgentRole;
  onAgentChange: (role: AgentRole) => void;
  onSend: (content: string, agentRole: AgentRole) => void;
  onStop: () => void;
}

const QUICK_PROMPTS = [
  {
    agentRole: 'researcher' as AgentRole,
    title: '🔍 市场调研',
    prompt: '帮我调研一下当前 AI Agent 领域的最新发展趋势',
  },
  {
    agentRole: 'coder' as AgentRole,
    title: '💻 代码实现',
    prompt: '用 Python 写一个快速排序算法，并添加详细注释',
  },
  {
    agentRole: 'reviewer' as AgentRole,
    title: '✅ 代码审查',
    prompt: '帮我审查这段代码的安全性和性能问题',
  },
  {
    agentRole: 'researcher' as AgentRole,
    title: '📊 竞品分析',
    prompt: '分析一下主流大模型 API 的优缺点对比',
  },
];

export function ChatArea({
  messages,
  isLoading,
  currentAgentRole,
  onAgentChange,
  onSend,
  onStop,
}: ChatAreaProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const currentAgent = AGENTS.find((a) => a.role === currentAgentRole)!;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  return (
    <div className="flex h-screen flex-1 flex-col bg-slate-50">
      {/* Header */}
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
        <h1 className="text-base font-semibold text-slate-800">
          多 Agent 协作对话
        </h1>
        <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5">
          <span className="text-sm">{currentAgent.avatar}</span>
          <span className="text-sm font-medium text-slate-700">
            {currentAgent.name}
          </span>
          <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
        </div>
      </header>

      {/* Messages Area */}
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          /* Welcome Page */
          <div className="flex h-full flex-col items-center justify-center px-6">
            <div className="mb-6 flex h-14 w-14 items-center justify-center rounded-2xl bg-indigo-100 text-2xl">
              <Bot className="h-7 w-7 text-indigo-600" />
            </div>
            <h2 className="mb-2 text-xl font-semibold text-slate-800">
              欢迎使用多 Agent 协作助手
            </h2>
            <p className="mb-8 text-sm text-slate-500">
              选择下方卡片快速开始，或直接输入你的问题
            </p>
            <div className="grid w-full max-w-2xl grid-cols-1 gap-3 sm:grid-cols-2">
              {QUICK_PROMPTS.map((item, index) => (
                <button
                  key={index}
                  onClick={() => onSend(item.prompt, item.agentRole)}
                  className="group rounded-xl border border-slate-200 bg-white p-4 text-left transition hover:border-indigo-300 hover:shadow-md"
                >
                  <div className="mb-1 text-sm font-medium text-slate-700 group-hover:text-indigo-600">
                    {item.title}
                  </div>
                  <div className="text-xs text-slate-400">
                    {item.prompt.slice(0, 30)}...
                  </div>
                </button>
              ))}
            </div>
          </div>
        ) : (
          /* Message List */
          <div className="mx-auto max-w-3xl space-y-4 px-4 py-6">
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Input Area */}
      <InputArea
        onSend={onSend}
        onStop={onStop}
        isLoading={isLoading}
        currentAgentRole={currentAgentRole}
        onAgentChange={onAgentChange}
      />
    </div>
  );
}