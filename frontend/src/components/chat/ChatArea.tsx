'use client';

import { useEffect, useRef } from 'react';

import { AscWordmark } from '@/components/AscWordmark';
import { InputArea, type ContextUsage } from './InputArea';
import { MessageBubble } from './MessageBubble';
import { type AgentRole, type Message } from '@/types';

/**
 * 上下文 / Token 使用量估算
 *
 * 后端当前未下发真实 token 用量（SSE done 事件只带 message_count），
 * 此处按「累计字符数 / 2.5」做混合中英文的粗略估算，仅用于胶囊展示。
 * 待后端在 done 事件中附带 usage 后，可直接替换为真实数值。
 */
function estimateContextUsage(messages: Message[]): ContextUsage {
  const chars = messages.reduce((sum, m) => sum + (m.content?.length ?? 0), 0);
  const used = Math.ceil(chars / 2.5);
  const total = 1_000_000; // 1M token 上下文窗口（示例量级）
  const percent = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  return { percent, used, total };
}

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
    title: '行业调研',
    prompt: '帮我梳理一下 AI Agent 领域近半年的主要进展与代表性产品',
  },
  {
    agentRole: 'developer' as AgentRole,
    title: '代码实现',
    prompt: '用 Python 写一个快速排序算法，写完后实际运行验证并给出结果',
  },
  {
    agentRole: 'writer' as AgentRole,
    title: '内容创作',
    prompt: '帮我写一篇关于「小团队如何引入 AI 编程助手」的公众号文章，1200 字左右',
  },
  {
    agentRole: 'analyst' as AgentRole,
    title: '数据分析',
    prompt: '我有一份销售数据 CSV，帮我做清洗、算出月度趋势并画成折线图',
  },
];

const KEY_HINTS = ['[enter] 发送', '[shift+enter] 换行', '[+] 附件与权限'];

/** 欢迎页深色 TUI 卡：全系统唯一的深色表面，仿 opencode 终端首屏 */
function WelcomeHero({ onSend }: { onSend: ChatAreaProps['onSend'] }) {
  return (
    <div className="mx-auto w-full max-w-2xl">
      {/* hero-tui-mockup */}
      <div className="bg-surface-dark px-8 py-16 text-center">
        <p className="mb-6 text-[11px] tracking-[0.2em] text-on-dark-mute">
          [v0.1] [4 agents] [sse streaming]
        </p>
        <AscWordmark className="mx-auto text-[13px] text-on-dark" />
        <h2 className="mt-6 text-2xl font-bold leading-normal text-on-dark">
          task_agents
          <span className="ml-2 text-base font-medium text-on-dark-mute">
            多 Agent 协作终端
          </span>
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-on-dark-mute">
          四种专职引擎 · 会话隔离 · 工具调用可观测
        </p>

        {/* tui-prompt-row */}
        <div className="mx-auto mt-8 inline-block rounded-sm bg-surface-dark-elevated px-3 py-2 text-sm text-on-dark">
          <span aria-hidden className="mr-2 text-on-dark-mute">│</span>
          ❯ task [调研] --联网查证 行业近况
          <span aria-hidden className="cursor-blink">▋</span>
        </div>

        {/* keybinding hints */}
        <div className="mt-8 flex flex-wrap items-center justify-center gap-x-5 gap-y-1 text-xs text-on-dark-mute">
          {KEY_HINTS.map((hint) => (
            <span key={hint}>{hint}</span>
          ))}
        </div>
      </div>

      {/* 快捷提示：发丝线方框 [+] 列表 */}
      <div className="mt-6 grid w-full grid-cols-1 gap-3 sm:grid-cols-2">
        {QUICK_PROMPTS.map((item) => (
          <button
            key={item.agentRole}
            onClick={() => onSend(item.prompt, item.agentRole)}
            className="rounded-none border border-hairline bg-canvas p-4 text-left transition hover:border-hairline-strong"
          >
            <div className="mb-1 text-sm font-bold text-ink">
              <span aria-hidden className="mr-1.5 text-mute">[+]</span>
              {item.title}
              <span className="ml-2 text-xs font-normal text-ash">[{item.agentRole}]</span>
            </div>
            <div className="truncate text-xs leading-relaxed text-mute">
              {item.prompt}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

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

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // 有消息、正在切换加载、或流式生成中，都算「已进入对话态」
  const hasConversation = messages.length > 0 || isSwitching;

  return (
    <div className="flex h-screen flex-1 flex-col bg-canvas">
      {/* Header：hairline 底边，右侧为键位提示 */}
      <header className="flex h-14 items-center justify-between border-b border-hairline bg-canvas px-6">
        <div className="flex items-center gap-2.5">
          <h1 className="text-sm font-bold text-ink">多 Agent 协作对话</h1>
          {activeSessionId && (
            <span
              className="hidden max-w-[220px] truncate rounded-sm bg-surface-soft px-2 py-0.5 text-[11px] text-mute sm:inline-block"
              title={`session / thread id: ${activeSessionId}`}
            >
              [thread {activeSessionId.slice(0, 8)}…]
            </span>
          )}
        </div>
        <div className="hidden items-center gap-4 text-xs text-mute md:flex">
          <span>[enter] 发送</span>
          <span>[shift+enter] 换行</span>
          <span className="text-ash">{currentAgentRole} engine</span>
        </div>
      </header>

      {/* Error Banner */}
      {errorMessage && (
        <div className="mx-6 mt-3 flex items-start gap-2 rounded-sm border border-danger/40 bg-canvas px-3 py-2 text-xs leading-relaxed text-danger">
          <span aria-hidden>[!]</span>
          <span className="flex-1">{errorMessage}</span>
        </div>
      )}

      {/* Messages Area */}
      <div className="flex-1 overflow-y-auto">
        {isSwitching && messages.length === 0 ? (
          /* 切换会话加载中 */
          <div className="flex h-full flex-col items-center justify-center gap-3">
            <p className="animate-pulse text-sm text-mute">[···] 正在加载对话历史…</p>
          </div>
        ) : !hasConversation ? (
          /* Welcome Page：深色 TUI 卡 + 快捷提示 */
          <div className="flex h-full items-center justify-center px-6">
            <WelcomeHero onSend={onSend} />
          </div>
        ) : (
          /* Message List */
          <div className="mx-auto max-w-3xl space-y-5 px-4 py-6">
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))}

            {/* 已发出请求但还没有任何增量时，显示等待指示 */}
            {isLoading && messages.length > 0 && messages[messages.length - 1].content === '' && (
              <div className="flex items-center gap-2 pl-2 text-xs text-mute">
                <span aria-hidden className="cursor-blink">▋</span>
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
        contextUsage={estimateContextUsage(messages)}
      />
    </div>
  );
}