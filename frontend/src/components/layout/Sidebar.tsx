'use client';

import { useState } from 'react';
import { History, MessageSquarePlus, Plus, Settings, Trash2, User } from 'lucide-react';

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
    <aside className="flex h-screen w-64 flex-col border-r border-slate-200 bg-slate-50">
      {/* Logo */}
      <div className="flex items-center gap-2 px-4 py-5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-sm font-bold text-white">
          MA
        </div>
        <span className="text-base font-semibold text-slate-800">Multi-Agent</span>
      </div>

      {/* New Chat Button */}
      <div className="px-3 py-2">
        <button
          onClick={onNewChat}
          className="flex w-full items-center gap-2 rounded-lg bg-indigo-600 px-3 py-2.5 text-sm font-medium text-white transition hover:bg-indigo-700 active:bg-indigo-800"
        >
          <Plus className="h-4 w-4" />
          新建对话
        </button>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        <div className="mb-2 flex items-center gap-1.5 px-2 text-xs font-medium uppercase tracking-wider text-slate-400">
          <History className="h-3.5 w-3.5" />
          历史会话
        </div>

        {isLoading ? (
          // 骨架屏：比转圈更接近最终布局，减少视觉跳动
          <ul className="space-y-1.5 px-1">
            {[0, 1, 2, 3].map((i) => (
              <li key={i} className="h-9 animate-pulse rounded-md bg-slate-200/70" />
            ))}
          </ul>
        ) : sessions.length === 0 ? (
          <div className="mt-6 flex flex-col items-center gap-2 px-4 text-center">
            <MessageSquarePlus className="h-7 w-7 text-slate-300" />
            <p className="text-xs leading-relaxed text-slate-400">
              还没有会话记录
              <br />
              点击上方「新建对话」开始
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
                      'w-full rounded-md py-2 pl-2.5 pr-8 text-left transition',
                      isActive
                        ? 'bg-white text-indigo-600 shadow-sm'
                        : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                    )}
                  >
                    <div className="truncate text-sm">{session.title}</div>
                    <div
                      className={cn(
                        'mt-0.5 truncate text-[10px]',
                        isActive ? 'text-indigo-400' : 'text-slate-400'
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
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 transition hover:bg-red-50 hover:text-red-500"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* User Section */}
      <div className="border-t border-slate-200 px-3 py-3">
        <button className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-sm text-slate-600 transition hover:bg-slate-100 hover:text-slate-900">
          <User className="h-4 w-4" />
          <span className="flex-1 truncate text-left">
            {userId ?? '未登录'}
          </span>
          <Settings className="h-4 w-4 shrink-0" />
        </button>
        <p className="mt-1 px-2 text-[10px] text-slate-400">
          登录功能开发中，当前为固定演示账号
        </p>
      </div>
    </aside>
  );
}
