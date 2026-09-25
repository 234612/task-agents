'use client';

import { useState, KeyboardEvent, useRef, useEffect } from 'react';
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
const ATTACHMENT_OPTIONS: { glyph: string; label: string }[] = [
  { glyph: '[file]', label: '上传文件' },
  { glyph: '[img]', label: '上传图片' },
  { glyph: '[repo]', label: '引用代码仓库' },
];

/** 把 token 数格式化为 K 单位，保留 1 位小数 */
function formatK(n: number): string {
  return `${(n / 1000).toFixed(1)}K`;
}

const MENU_BASE =
  'absolute bottom-full z-20 mb-1 overflow-hidden rounded-sm border border-hairline bg-canvas py-1';

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
    <div className="border-t border-hairline bg-canvas px-4 py-3">
      <div className="mx-auto max-w-3xl">
        {/* prompt-row：发丝线边框卡片，聚焦时边框转 ink */}
        <div className="relative rounded-sm border border-hairline bg-canvas transition focus-within:border-ink">
          <div className="p-3">
            {/* ===== 顶部：prompt 输入行 ===== */}
            <div className="flex items-start gap-1.5">
              <span aria-hidden className="select-none pt-1 text-sm text-mute">❯</span>
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                onInput={handleInput}
                placeholder={`task ${currentAgent.name} --输入任务描述`}
                rows={1}
                className="block w-full resize-none bg-transparent px-1 py-1 text-sm leading-relaxed text-ink placeholder-ash focus:outline-none"
              />
            </div>
            <div className="flex items-baseline justify-between pl-4 text-[11px]">
              <span className="text-ash">@ 引用对话文件 / 调用技能与指令</span>
              {/* 上下文 / Token 使用量状态 */}
              <span
                className="cursor-help text-mute"
                title={`上下文窗口：${contextUsage.used.toLocaleString()} / ${contextUsage.total.toLocaleString()} tokens`}
              >
                [ctx {contextUsage.percent.toFixed(1)}% · {formatK(contextUsage.used)} /{' '}
                {formatK(contextUsage.total)}]
              </span>
            </div>

            {/* ===== 底部：工具栏 ===== */}
            <div className="mt-2.5 flex items-center justify-between">
              {/* 左侧：+ 附件 / Git 分支 / 权限下拉 */}
              <div className="flex items-center gap-1">
                {/* + 附件菜单 */}
                <div className="relative" ref={attachMenuRef}>
                  <button
                    onClick={() => {
                      setShowAttachMenu(!showAttachMenu);
                      setShowAgentMenu(false);
                    }}
                    className="flex h-8 items-center rounded-sm px-2 text-xs text-mute transition hover:bg-surface-soft hover:text-ink"
                    title="添加附件"
                  >
                    [+]
                  </button>
                  {showAttachMenu && (
                    <div className={cn(MENU_BASE, 'left-0 w-44')}>
                      {ATTACHMENT_OPTIONS.map((opt) => (
                        <button
                          key={opt.label}
                          onClick={() => setShowAttachMenu(false)}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-body transition hover:bg-surface-soft hover:text-ink"
                        >
                          <span aria-hidden className="text-ash">{opt.glyph}</span>
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                
                {/* 权限下拉 */}
                <div className="relative" ref={permissionMenuRef}>
                  <button
                    onClick={() => {
                      setShowPermissionMenu(!showPermissionMenu);
                      setShowAttachMenu(false);
                      setShowAgentMenu(false);
                    }}
                    className="flex h-8 items-center rounded-sm px-2 text-xs text-mute transition hover:bg-surface-soft hover:text-ink"
                    title="访问权限"
                  >
                    [access:{permission} <span aria-hidden>▾</span>]
                  </button>
                  {showPermissionMenu && (
                    <div className={cn(MENU_BASE, 'left-0 w-40')}>
                      {PERMISSION_OPTIONS.map((opt) => (
                        <button
                          key={opt}
                          onClick={() => {
                            setPermission(opt);
                            setShowPermissionMenu(false);
                          }}
                          className={cn(
                            'flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition',
                            permission === opt
                              ? 'bg-surface-soft font-semibold text-ink'
                              : 'text-body hover:bg-surface-soft hover:text-ink'
                          )}
                        >
                          {permission === opt && (
                            <span aria-hidden className="text-accent">✓</span>
                          )}
                          {opt}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* 右侧：思考指示 / 模型·Agent 选择器 / 语音 / 发送 */}
              <div className="flex items-center gap-1.5">
                {/* 思考加载指示 */}
                {isLoading && (
                  <span aria-hidden className="animate-pulse text-sm text-accent">⟳</span>
                )}

                {/* 模型 / Agent 选择器 */}
                <div className="relative" ref={agentMenuRef}>
                  <button
                    onClick={() => {
                      setShowAgentMenu(!showAgentMenu);
                      setShowAttachMenu(false);
                    }}
                    className="flex h-8 items-center gap-1.5 rounded-sm border border-hairline bg-canvas px-2.5 text-xs text-body transition hover:bg-surface-soft hover:text-ink"
                    title="切换 Agent / 模型"
                  >
                    [agent:{currentAgent.name} <span aria-hidden>▾</span>]
                  </button>
                  {showAgentMenu && (
                    <div className={cn(MENU_BASE, 'right-0 w-64')}>
                      {AGENTS.map((agent) => {
                        const isActive = currentAgentRole === agent.role;
                        return (
                          <button
                            key={agent.role}
                            onClick={() => {
                              onAgentChange(agent.role);
                              setShowAgentMenu(false);
                            }}
                            className={cn(
                              'flex w-full items-start gap-2 px-3 py-2.5 text-left text-xs transition',
                              isActive
                                ? 'bg-surface-soft text-ink'
                                : 'text-body hover:bg-surface-soft hover:text-ink'
                            )}
                          >
                            <span aria-hidden className={cn('mt-px shrink-0', isActive ? 'text-accent' : 'text-ash')}>
                              {isActive ? '✓' : '·'}
                            </span>
                            <span className="min-w-0">
                              <span className="block font-bold">
                                {agent.name}
                                <span className="ml-1.5 font-normal text-ash">[{agent.role}]</span>
                              </span>
                              <span className="mt-0.5 block truncate leading-relaxed text-mute">
                                {agent.description}
                              </span>
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* 语音输入 */}
                <button
                  className="flex h-8 items-center rounded-sm px-2 text-xs text-mute transition hover:bg-surface-soft hover:text-ink"
                  title="语音输入"
                >
                  [voice]
                </button>

                {/* 发送按钮（墨色方块，空时禁用）；生成中变停止 */}
                {isLoading ? (
                  <button
                    onClick={onStop}
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-sm bg-danger text-white transition hover:bg-danger-hover active:bg-danger-active"
                    title="停止生成"
                  >
                    <span aria-hidden className="text-sm">■</span>
                  </button>
                ) : (
                  <button
                    onClick={handleSend}
                    disabled={!input.trim()}
                    className={cn(
                      'flex h-9 w-9 shrink-0 items-center justify-center rounded-sm text-sm transition',
                      input.trim()
                        ? 'bg-ink text-canvas hover:bg-charcoal active:bg-ink-deep'
                        : 'cursor-not-allowed bg-surface-card text-ash'
                    )}
                    title="发送"
                  >
                    <span aria-hidden>↵</span>
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