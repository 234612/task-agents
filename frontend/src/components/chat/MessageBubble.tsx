'use client';

import { User, Wrench } from 'lucide-react';
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

  return (
    <div
      className={cn(
        'flex gap-3',
        isUser ? 'flex-row-reverse' : 'flex-row'
      )}
    >
      {/* Avatar */}
      <div
        className={cn(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm',
          isUser
            ? 'bg-indigo-600 text-white'
            : 'bg-slate-200 text-slate-600'
        )}
      >
        {isUser ? (
          <User className="h-4 w-4" />
        ) : (
          <span>{agent?.avatar ?? '🤖'}</span>
        )}
      </div>

      {/* Bubble */}
      <div
        className={cn(
          'max-w-[75%] rounded-2xl px-4 py-3 text-sm leading-relaxed',
          isUser
            ? 'bg-indigo-600 text-white'
            : 'bg-white text-slate-800 shadow-sm border border-slate-200'
        )}
      >
        {!isUser && agent && (
          <div className="mb-1 text-xs font-medium text-indigo-600">
            {agent.name}
          </div>
        )}
        {isUser ? (
          <div className="whitespace-pre-wrap">{message.content}</div>
        ) : (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              code({ className, children, ...props }) {
                const isInline = !className;
                return isInline ? (
                  <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs" {...props}>
                    {children}
                  </code>
                ) : (
                  <code className={className} {...props}>
                    {children}
                  </code>
                );
              },
              pre({ children }) {
                return (
                  <pre className="my-2 overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">
                    {children}
                  </pre>
                );
              },
              table({ children }) {
                return (
                  <div className="my-2 overflow-x-auto">
                    <table className="min-w-full divide-y divide-slate-200 border text-sm">
                      {children}
                    </table>
                  </div>
                );
              },
              th({ children }) {
                return (
                  <th className="bg-slate-50 px-3 py-2 text-left font-medium text-slate-700">
                    {children}
                  </th>
                );
              },
              td({ children }) {
                return (
                  <td className="border-t border-slate-200 px-3 py-2 text-slate-600">
                    {children}
                  </td>
                );
              },
            }}
          >
            {message.content}
          </ReactMarkdown>
        )}

        {/* 工具调用记录：展示本轮助手调用过哪些工具及其入参 */}
        {message.toolCalls && message.toolCalls.length > 0 && (
          <div className="mt-2 space-y-1 border-t border-slate-100 pt-2">
            {message.toolCalls.map((call, index) => (
              <div
                key={call.id ?? index}
                className="flex items-start gap-1.5 text-[10px] text-slate-500"
              >
                <Wrench className="mt-0.5 h-3 w-3 shrink-0 text-slate-400" />
                <span className="min-w-0 flex-1">
                  <span className="font-medium text-slate-600">
                    {call.name ?? '未知工具'}
                  </span>
                  {call.args && Object.keys(call.args).length > 0 && (
                    <span className="ml-1 break-all font-mono text-slate-400">
                      {JSON.stringify(call.args)}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
        <div
          className={cn(
            'mt-1.5 text-[10px]',
            isUser ? 'text-indigo-200' : 'text-slate-400'
          )}
        >
          {message.isStreaming ? '生成中…' : new Date(message.timestamp).toLocaleTimeString()}
        </div>
      </div>
    </div>
  );
}