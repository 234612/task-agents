'use client';

import { useState, KeyboardEvent, useRef, useEffect } from 'react';
import {
  Send,
  Square,
  Plus,
  GitBranch,
  ChevronDown,
  Mic,
  Loader2,
  ShieldCheck,
  FileText,
  Image as ImageIcon,
  Code2,
  type LucideIcon,
} from 'lucide-react';
import { AgentRole, AGENTS } from '@/types';
import { cn } from '@/lib/utils';

export interface ContextUsage {
  /** 已使用占比（0–100） */
  percent: number;
  /** 已使用 token 数 */
  used: number;
  /** 上下文窗口总 token 数 */
  total: number;
}

interface InputAreaProps {
  onSend: (content: string, agentRole: AgentRole) => void;
  onStop: () => void;
  isLoading: boolean;
  currentAgentRole: AgentRole;
  onAgentChange: (role: AgentRole) => void;
  /** 上下文 / Token 使用量；未接入后端时由父组件估算传入 */
  contextUsage?: ContextUsage;
}

const PERMISSION_OPTIONS = ['允许完全访问', '仅读文件', '仅对话'] as const;
const ATTACHMENT_OPTIONS: { icon: LucideIcon; label: string }[] = [
  { icon: FileText, label: '上传文件' },
  { icon: ImageIcon, label: '上传图片' },
  { icon: Code2, label: '引用代码仓库' },
];

/** 把 token 数格式化为 K 单位，保留 1 位小数 */
function formatK(n: number): string {
  return `${(n / 1000).toFixed(1)}K`;
}

export function InputArea({
  onSend,
  onStop,
  isLoading,
  currentAgentRole,
  onAgentChange,
  contextUsage = { percent: 0, used: 0, total: 1_000_000 },
}: InputAreaProps) {
  const [input, setInput] = useState('');
  const [showAgentMenu, setShowAgentMenu] = useState(false);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [showPermissionMenu, setShowPermissionMenu] = useState(false);
  const [permission, setPermission] = useState<(typeof PERMISSION_OPTIONS)[number]>(
    PERMISSION_OPTIONS[0]
  );

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const agentMenuRef = useRef<HTMLDivElement>(null);
  const attachMenuRef = useRef<HTMLDivElement>(null);
  const permissionMenuRef = useRef<HTMLDivElement>(null);

  // 点击空白处关闭所有浮层菜单
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (agentMenuRef.current && !agentMenuRef.current.contains(e.target as Node)) {
        setShowAgentMenu(false);
      }
      if (attachMenuRef.current && !attachMenuRef.current.contains(e.target as Node)) {
        setShowAttachMenu(false);
      }
      if (
        permissionMenuRef.current &&
        !permissionMenuRef.current.contains(e.target as Node)
      ) {
        setShowPermissionMenu(false);
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
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  };

  const currentAgent = AGENTS.find((a) => a.role === currentAgentRole)!;

  return (
    <div className="border-t border-slate-200 bg-slate-50 px-4 py-3">
      <div className="mx-auto max-w-3xl">
        {/* 整体圆角卡片 */}
        <div className="relative rounded-2xl border border-slate-200 bg-white shadow-sm transition focus-within:border-indigo-300 focus-within:shadow-md">
          <div className="p-3">
            {/* ===== 顶部：输入框主体 ===== */}
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onInput={handleInput}
              placeholder="今天帮你做些什么？"
              rows={1}
              className="block w-full resize-none bg-transparent px-1 py-1 text-sm leading-relaxed text-slate-800 placeholder-slate-400 focus:outline-none"
            />
            <p className="px-1 text-xs text-slate-400">
              @ 引用对话文件 / 调用技能与指令
            </p>

            {/* ===== 中部（右侧）：上下文 / Token 使用量状态胶囊 ===== */}
            <div className="pointer-events-none absolute bottom-[58px] right-3">
              <div className="group relative">
                <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-500">
                  {contextUsage.percent.toFixed(1)}% ·{' '}
                  {formatK(contextUsage.used)} / {formatK(contextUsage.total)} 上下文已使用
                </span>
                {/* 悬停 Tooltip */}
                <div className="pointer-events-none absolute bottom-full right-0 mb-1 hidden whitespace-nowrap rounded-md bg-slate-800 px-2 py-1 text-[11px] text-white shadow-md group-hover:block">
                  上下文窗口：{contextUsage.used.toLocaleString()} /{' '}
                  {contextUsage.total.toLocaleString()} tokens
                </div>
              </div>
            </div>

            {/* ===== 底部：工具栏 ===== */}
            <div className="mt-2 flex items-center justify-between">
              {/* 左侧：+ 附件 / Git 分支 / 权限下拉 */}
              <div className="flex items-center gap-1">
                {/* + 附件菜单 */}
                <div className="relative" ref={attachMenuRef}>
                  <button
                    onClick={() => {
                      setShowAttachMenu(!showAttachMenu);
                      setShowAgentMenu(false);
                    }}
                    className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
                    title="添加附件"
                  >
                    <Plus className="h-4 w-4" />
                  </button>
                  {showAttachMenu && (
                    <div className="absolute bottom-full left-0 z-20 mb-1 w-40 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
                      {ATTACHMENT_OPTIONS.map((opt) => (
                        <button
                          key={opt.label}
                          onClick={() => setShowAttachMenu(false)}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-slate-600 transition hover:bg-slate-50"
                        >
                          <opt.icon className="h-4 w-4 text-slate-400" />
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* Git 分支 */}
                <button
                  className="flex h-8 items-center gap-1.5 rounded-lg px-2 text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
                  title="当前分支"
                >
                  <GitBranch className="h-4 w-4" />
                  <span className="text-xs font-medium">main</span>
                </button>

                {/* 权限下拉 */}
                <div className="relative" ref={permissionMenuRef}>
                  <button
                    onClick={() => {
                      setShowPermissionMenu(!showPermissionMenu);
                      setShowAttachMenu(false);
                      setShowAgentMenu(false);
                    }}
                    className="flex h-8 items-center gap-1 rounded-lg px-2 text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
                    title="访问权限"
                  >
                    <ShieldCheck className="h-4 w-4" />
                    <span className="text-xs font-medium">{permission}</span>
                    <ChevronDown className="h-3 w-3" />
                  </button>
                  {showPermissionMenu && (
                    <div className="absolute bottom-full left-0 z-20 mb-1 w-36 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
                      {PERMISSION_OPTIONS.map((opt) => (
                        <button
                          key={opt}
                          onClick={() => {
                            setPermission(opt);
                            setShowPermissionMenu(false);
                          }}
                          className={cn(
                            'flex w-full items-center px-3 py-2 text-left text-sm transition',
                            permission === opt
                              ? 'bg-indigo-50 text-indigo-700'
                              : 'text-slate-600 hover:bg-slate-50'
                          )}
                        >
                          {opt}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* 右侧：思考圈 / 模型·Agent 选择器 / 语音 / 发送 */}
              <div className="flex items-center gap-1.5">
                {/* 思考加载圈 */}
                {isLoading && (
                  <Loader2 className="h-4 w-4 animate-spin text-indigo-500" />
                )}

                {/* 模型 / Agent 选择器 */}
                <div className="relative" ref={agentMenuRef}>
                  <button
                    onClick={() => {
                      setShowAgentMenu(!showAgentMenu);
                      setShowAttachMenu(false);
                    }}
                    className="flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50"
                    title="切换 Agent / 模型"
                  >
                    <span>{currentAgent.avatar}</span>
                    <span>{currentAgent.name}</span>
                    <ChevronDown className="h-3 w-3 text-slate-400" />
                  </button>
                  {showAgentMenu && (
                    <div className="absolute bottom-full right-0 z-20 mb-1 w-56 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
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
                          <div className="min-w-0">
                            <div className="font-medium">{agent.name}</div>
                            <div className="truncate text-xs text-slate-400">
                              {agent.description}
                            </div>
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* 语音输入 */}
                <button
                  className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
                  title="语音输入"
                >
                  <Mic className="h-4 w-4" />
                </button>

                {/* 发送按钮（圆形品牌色，空时禁用） */}
                {isLoading ? (
                  <button
                    onClick={onStop}
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-red-500 text-white transition hover:bg-red-600"
                    title="停止生成"
                  >
                    <Square className="h-4 w-4 fill-current" />
                  </button>
                ) : (
                  <button
                    onClick={handleSend}
                    disabled={!input.trim()}
                    className={cn(
                      'flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition',
                      input.trim()
                        ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                        : 'cursor-not-allowed bg-slate-200 text-slate-400'
                    )}
                    title="发送"
                  >
                    <Send className="h-4 w-4" />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
