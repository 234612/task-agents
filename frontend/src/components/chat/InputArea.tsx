'use client';

import { useState, KeyboardEvent, useRef, useEffect } from 'react';
import { Send, Paperclip, Square } from 'lucide-react';
import { AgentRole, AGENTS } from '@/types';
import { cn } from '@/lib/utils';

interface InputAreaProps {
  onSend: (content: string, agentRole: AgentRole) => void;
  onStop: () => void;
  isLoading: boolean;
  currentAgentRole: AgentRole;
  onAgentChange: (role: AgentRole) => void;
}

export function InputArea({
  onSend,
  onStop,
  isLoading,
  currentAgentRole,
  onAgentChange,
}: InputAreaProps) {
  const [input, setInput] = useState('');
  const [showAgentMenu, setShowAgentMenu] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowAgentMenu(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSend = () => {
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;
    onSend(trimmed, currentAgentRole);
    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = () => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 160)}px`;
    }
  };

  const currentAgent = AGENTS.find((a) => a.role === currentAgentRole)!;

  return (
    <div className="border-t border-slate-200 bg-white px-4 py-3">
      <div className="mx-auto max-w-3xl">
        {/* Agent Selector */}
        <div className="relative mb-2" ref={menuRef}>
          <button
            onClick={() => setShowAgentMenu(!showAgentMenu)}
            className="flex items-center gap-1.5 rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-200"
          >
            <span>{currentAgent.avatar}</span>
            <span>{currentAgent.name}</span>
            <svg className="h-3 w-3" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z"
                clipRule="evenodd"
              />
            </svg>
          </button>

          {showAgentMenu && (
            <div className="absolute bottom-full left-0 z-10 mb-1 w-56 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
              {AGENTS.map((agent) => (
                <button
                  key={agent.role}
                  onClick={() => {
                    onAgentChange(agent.role);
                    setShowAgentMenu(false);
                  }}
                  className={cn(
                    'flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm transition',
                    currentAgentRole === agent.role
                      ? 'bg-indigo-50 text-indigo-700'
                      : 'text-slate-700 hover:bg-slate-50'
                  )}
                >
                  <span className="text-base">{agent.avatar}</span>
                  <div>
                    <div className="font-medium">{agent.name}</div>
                    <div className="text-xs text-slate-400">
                      {agent.description}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Input Box */}
        <div className="flex items-end gap-2 rounded-xl border border-slate-300 bg-white p-2 shadow-sm focus-within:border-indigo-400 focus-within:ring-1 focus-within:ring-indigo-400">
          <button className="shrink-0 rounded-lg p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600">
            <Paperclip className="h-4 w-4" />
          </button>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onInput={handleInput}
            placeholder="输入消息，Shift+Enter 换行..."
            rows={1}
            className="flex-1 resize-none bg-transparent py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none"
          />
          {isLoading ? (
            <button
              onClick={onStop}
              className="shrink-0 rounded-lg bg-red-500 p-2 text-white transition hover:bg-red-600"
            >
              <Square className="h-4 w-4 fill-current" />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!input.trim()}
              className={cn(
                'shrink-0 rounded-lg p-2 transition',
                input.trim()
                  ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                  : 'bg-slate-200 text-slate-400'
              )}
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}