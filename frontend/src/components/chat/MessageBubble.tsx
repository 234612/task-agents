'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Message, AGENTS } from '@/types';
import { cn } from '@/lib/utils';

interface MessageBubbleProps {
  message: Message;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'user';
  const agent = message.agentRole
    ? AGENTS.find((a) => a.role === message.agentRole)
    : null;

  if (isUser) {
    // 用户消息 = 深色墨块（系统内唯一允许的深色小表面），右对齐
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] rounded-sm bg-ink px-4 py-2.5 text-sm leading-relaxed text-canvas">
          <div className="whitespace-pre-wrap">{message.content}</div>
          <div className="mt-1.5 text-right text-[10px] text-on-dark/50">
            {new Date(message.timestamp).toLocaleTimeString()}
          </div>
        </div>
      </div>
    );
  }

  const time = message.isStreaming
    ? null
    : new Date(message.timestamp).toLocaleTimeString();

  return (
    <div className="border-b border-hairline pb-4">
      {/* 元信息行：[agent] 标签 + 时间 + 流式光标 */}
      <div className="mb-1.5 flex items-baseline gap-2 text-xs">
        <span className="font-bold text-ink">[{agent?.name ?? 'agent'}]</span>
        {time && <span className="text-ash">{time}</span>}
        {message.isStreaming && (
          <span className="text-mute">
            生成中 <span aria-hidden className="cursor-blink">▋</span>
          </span>
        )}
      </div>

      {/* 正文：纯文本排版，不发气泡 */}
      <div className="text-sm leading-relaxed text-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({ children }) => (
              <h1 className="mb-2 mt-4 text-lg font-bold text-ink">{children}</h1>
            ),
            h2: ({ children }) => (
              <h2 className="mb-2 mt-4 text-base font-bold text-ink">{children}</h2>
            ),
            h3: ({ children }) => (
              <h3 className="mb-1.5 mt-3 text-sm font-bold text-ink">{children}</h3>
            ),
            p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
            ul: ({ children }) => (
              <ul className="my-2 list-none space-y-0.5 pl-0">
                {children}
              </ul>
            ),
            ol: ({ children }) => (
              <ol className="my-2 list-decimal pl-8">{children}</ol>
            ),
            li: ({ children }) => (
              <li>
                <span aria-hidden className="mr-1.5 text-mute">[-]</span>
                {children}
              </li>
            ),
            a: ({ children, ...props }) => (
              <a
                className="text-accent underline underline-offset-2 hover:text-accent-hover"
                {...props}
              >
                {children}
              </a>
            ),
            blockquote: ({ children }) => (
              <blockquote className="my-2 border-l-2 border-hairline-strong pl-3 text-body/80">
                {children}
              </blockquote>
            ),
            hr: () => <hr className="my-3 border-hairline" />,
            strong: ({ children }) => (
              <strong className="font-bold text-ink">{children}</strong>
            ),
            code({ className, children, ...props }) {
              // 注意：react-markdown v9 中无语言标注的围栏代码块（```）不会带
              // className，不能用 !className 判断行内代码，否则整块代码会误套
              // 行内的浅色样式，嵌进深色 pre 里变成"白底浅字"看不清。
              // 因此额外把"含换行"的内容视为块级代码。
              const text = String(children ?? '');
              const isInline = !className && !text.includes('\n');
              return isInline ? (
                <code
                  className="rounded-sm bg-surface-card px-1.5 py-0.5 text-xs text-ink"
                  {...props}
                >
                  {children}
                </code>
              ) : (
                <code className={cn('font-mono', className)} {...props}>
                  {children}
                </code>
              );
            },
            pre({ children }) {
              return (
                <pre className="my-2 overflow-x-auto rounded-sm bg-surface-dark p-3 text-xs leading-relaxed text-on-dark [&_code]:block [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-inherit">
                  {children}
                </pre>
              );
            },
            table({ children }) {
              return (
                <div className="my-2 overflow-x-auto">
                  <table className="min-w-full divide-y divide-hairline border border-hairline text-sm">
                    {children}
                  </table>
                </div>
              );
            },
            th({ children }) {
              return (
                <th className="bg-surface-soft px-3 py-2 text-left font-bold text-ink">
                  {children}
                </th>
              );
            },
            td({ children }) {
              return (
                <td className="border-t border-hairline px-3 py-2 text-body">
                  {children}
                </td>
              );
            },
          }}
        >
          {message.content}
        </ReactMarkdown>
      </div>

      {/* 工具调用记录：展示本轮助手调用过哪些工具及其入参 */}
      {message.toolCalls && message.toolCalls.length > 0 && (
        <div className="mt-2.5 space-y-1 rounded-sm bg-surface-card p-2.5">
          {message.toolCalls.map((call, index) => (
            <div key={call.id ?? index} className="flex items-start gap-1.5 text-[11px] leading-relaxed text-mute">
              <span aria-hidden className="shrink-0">↻</span>
              <span className="min-w-0 flex-1 break-all">
                <span aria-hidden className="text-stone">[</span>
                <span className="font-semibold text-ink">{call.name ?? '未知工具'}</span>
                <span aria-hidden className="text-stone">]</span>
                {call.args && Object.keys(call.args).length > 0 && (
                  <span className="ml-1.5 text-stone">{JSON.stringify(call.args)}</span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 完成标志：与 TUI 输出行呼应 */}
      {!message.isStreaming && message.toolCalls && message.toolCalls.length > 0 && (
        <div className="mt-1.5 text-[10px] text-stone">[✓] {message.toolCalls.length} 个工具调用已完成</div>
      )}
    </div>
  );
}