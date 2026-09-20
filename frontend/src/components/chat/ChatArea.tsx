'use client';

import { useEffect, useRef } from 'react';
import { AlertCircle, Bot, ChevronDown } from 'lucide-react';

import { InputArea } from './InputArea';
import { MessageBubble } from './MessageBubble';
import { AGENTS, type AgentRole, type Message } from '@/types';

interface ChatAreaProps {
  messages: Message[];
  isLoading: boolean;
  /** 正在切换会话加载历史，此时应显示加载态而非空白欢迎页 */
  isSwitching?: boolean;
  /** 错误提示（来自聊天或会话列表） */
  errorMessage?: string | null;
  currentAgentRole: AgentRole;
  /** 当前会话 ID，为空表示尚未创建会话（新建对话状态） */
  activeSessionId?: string | null;
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
  isSwitching = false,
  errorMessage = null,
  currentAgentRole,
  activeSessionId,
  onAgentChange,
  onSend,
  onStop,
}: ChatAreaProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const currentAgent = AGENTS.find((a) => a.role === currentAgentRole)!;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // 有消息、正在切换加载、或流式生成中，都算「已进入对话态」
  const hasConversation = messages.length > 0 || isSwitching;

  return (
    <div className="flex h-screen flex-1 flex-col bg-slate-50">
      {/* Header */}
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
        <div className="flex items-center gap-2">
          <h1 className="text-base font-semibold text-slate-800">多 Agent 协作对话</h1>
          {activeSessionId && (
            <span
              className="hidden max-w-[220px] truncate rounded bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-slate-400 sm:inline-block"
              title={`session / thread id: ${activeSessionId}`}
            >
              {activeSessionId}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5">
          <span className="text-sm">{currentAgent.avatar}</span>
          <span className="text-sm font-medium text-slate-700">{currentAgent.name}</span>
          <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
        </div>
      </header>

      {/* Error Banner */}
      {errorMessage && (
        <div className="mx-6 mt-3 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">{errorMessage}</span>
        </div>
      )}

      {/* Messages Area */}
      <div className="flex-1 overflow-y-auto">
        {isSwitching && messages.length === 0 ? (
          /* 切换会话加载中 */
          <div className="flex h-full flex-col items-center justify-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-600" />
            <p className="text-sm text-slate-400">正在加载对话历史…</p>
          </div>
        ) : !hasConversation ? (
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
                  <div className="text-xs text-slate-400">{item.prompt.slice(0, 30)}...</div>
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

            {/* 已发出请求但还没有任何增量时，显示等待指示 */}
            {isLoading && messages.length > 0 && messages[messages.length - 1].content === '' && (
              <div className="flex items-center gap-2 pl-11 text-xs text-slate-400">
                <span className="flex gap-1">
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.3s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.15s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400" />
                </span>
                正在思考…
              </div>
            )}

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
