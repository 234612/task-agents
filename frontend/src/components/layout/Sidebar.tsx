'use client';

import { Plus, History, Settings, User } from 'lucide-react';
import { ChatSession } from '@/types';

interface SidebarProps {
  sessions: ChatSession[];
  activeSessionId: string | null;
  onNewChat: () => void;
  onSelectSession: (id: string) => void;
}

export function Sidebar({
  sessions,
  activeSessionId,
  onNewChat,
  onSelectSession,
}: SidebarProps) {
  return (
    <aside className="flex h-screen w-64 flex-col border-r border-slate-200 bg-slate-50">
      {/* Logo */}
      <div className="flex items-center gap-2 px-4 py-5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-white font-bold text-sm">
          MA
        </div>
        <span className="text-base font-semibold text-slate-800">
          Multi-Agent
        </span>
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
        <ul className="space-y-0.5">
          {sessions.map((session) => (
            <li key={session.id}>
              <button
                onClick={() => onSelectSession(session.id)}
                className={`w-full truncate rounded-md px-2.5 py-2 text-left text-sm transition ${
                  activeSessionId === session.id
                    ? 'bg-white text-indigo-600 shadow-sm'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                }`}
              >
                {session.title}
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* User Section */}
      <div className="border-t border-slate-200 px-3 py-3">
        <button className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-sm text-slate-600 transition hover:bg-slate-100 hover:text-slate-900">
          <User className="h-4 w-4" />
          <span className="flex-1 text-left">用户设置</span>
          <Settings className="h-4 w-4" />
        </button>
      </div>
    </aside>
  );
}