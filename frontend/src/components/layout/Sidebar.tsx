'use client';

import { useState } from 'react';

import { AscWordmark } from '@/components/AscWordmark';
import { cn } from '@/lib/utils';
import type { ChatSession } from '@/types';

interface SidebarProps {
  sessions: ChatSession[];
  activeSessionId: string | null;
  /** 正在从服务端拉取会话列表 */
  isLoading?: boolean;
  /** 当前用户 ID（现阶段为 hardcode，登录后展示真实用户） */
  userId?: string;
  onNewChat: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession?: (id: string) => void | Promise<void>;
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前 / 具体日期 */
function formatRelativeTime(timestamp: number): string {
  const diff = Date.now() - timestamp;
  if (Number.isNaN(diff)) return '';

  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;

  if (diff < minute) return '刚刚';
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`;
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`;
  if (diff < 7 * day) return `${Math.floor(diff / day)} 天前`;

  return new Date(timestamp).toLocaleDateString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
  });
}

export function Sidebar({
  sessions,
  activeSessionId,
  isLoading = false,
  userId,
  onNewChat,
  onSelectSession,
  onDeleteSession,
}: SidebarProps) {
  // 悬停才显示删除按钮，避免列表被图标挤满
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  return (
    <aside className="flex h-screen w-64 flex-col border-r border-hairline bg-canvas">
      {/* Logo：块像素 wordmark + 产品名 */}
      <div className="flex items-center gap-2.5 px-4 py-5">
        <AscWordmark className="text-ink" />
        <div className="min-w-0">
          <div className="truncate text-sm font-bold text-ink">task_agents</div>
          <div className="text-[11px] text-ash">multi-agent terminal</div>
        </div>
      </div>

      {/* New Chat Button */}
      <div className="px-3 py-2">
        <button
          onClick={onNewChat}
          className="flex h-9 w-full items-center justify-center gap-2 rounded-sm bg-ink text-sm font-medium text-canvas transition hover:bg-charcoal active:bg-ink-deep"
        >
          <span aria-hidden>[+]</span>
          新建对话
        </button>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        <div className="mb-2 flex items-baseline justify-between px-1 text-[11px] font-medium uppercase tracking-[0.2em] text-mute">
          <span>[sessions]</span>
          {!isLoading && sessions.length > 0 && (
            <span className="normal-case tracking-normal text-ash">{sessions.length}</span>
          )}
        </div>

        {isLoading ? (
          // 骨架屏：比转圈更接近最终布局，减少视觉跳动
          <ul className="space-y-1.5 px-1">
            {[0, 1, 2, 3].map((i) => (
              <li key={i} className="h-9 animate-pulse rounded-sm bg-surface-card" />
            ))}
          </ul>
        ) : sessions.length === 0 ? (
          <div className="mt-6 flex flex-col items-center gap-2 px-4 text-center">
            <p className="text-xs leading-relaxed text-mute">
              [-] 还没有会话记录
              <br />
              点击上方 [新建对话] 开始
            </p>
          </div>
        ) : (
          <ul className="space-y-0.5">
            {sessions.map((session) => {
              const isActive = activeSessionId === session.id;
              const isHovered = hoveredId === session.id;

              return (
                <li
                  key={session.id}
                  onMouseEnter={() => setHoveredId(session.id)}
                  onMouseLeave={() => setHoveredId(null)}
                  className="group relative"
                >
                  <button
                    onClick={() => onSelectSession(session.id)}
                    className={cn(
                      'w-full rounded-none py-2 pl-1 pr-8 text-left transition',
                      isActive
                        ? 'bg-surface-soft text-ink'
                        : 'text-body hover:bg-surface-soft hover:text-ink'
                    )}
                  >
                    <div className="truncate pl-2 text-sm">
                      {isActive && <span aria-hidden className="mr-1.5 text-mute">❯</span>}
                      {session.title}
                    </div>
                    <div
                      className={cn(
                        'mt-0.5 truncate pl-2 text-[11px]',
                        isActive ? 'text-mute' : 'text-ash'
                      )}
                    >
                      {formatRelativeTime(session.lastMessageAt)}
                      {session.messageCount != null && session.messageCount > 0
                        ? ` · ${session.messageCount} 条`
                        : ''}
                    </div>
                  </button>

                  {onDeleteSession && isHovered && (
                    <button
                      onClick={(e) => {
                        // 阻止冒泡，否则会同时触发选中该会话
                        e.stopPropagation();
                        void onDeleteSession(session.id);
                      }}
                      title="删除会话"
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-sm p-1 text-mute transition hover:bg-surface-card hover:text-danger"
                    >
                      <span aria-hidden className="text-xs">[x]</span>
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* User Section */}
      <div className="border-t border-hairline px-3 py-3">
        <button className="flex w-full items-center gap-2 rounded-sm px-2 py-2 text-sm text-body transition hover:bg-surface-soft hover:text-ink">
          <span aria-hidden className="text-mute">[user]</span>
          <span className="flex-1 truncate text-left">{userId ?? '未登录'}</span>
          <span aria-hidden className="shrink-0 text-xs text-mute">[cfg]</span>
        </button>
        <p className="mt-1 px-2 text-[10px] leading-relaxed text-ash">
          登录功能开发中，当前为固定演示账号
        </p>
      </div>
    </aside>
  );
}