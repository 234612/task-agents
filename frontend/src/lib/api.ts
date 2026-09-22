/**
 * 后端 API 封装
 *
 * 职责：
 * 1. 统一请求地址、错误处理与 JSON 解析
 * 2. 把后端 DTO 适配为前端视图模型（字段名与时间格式差异在此消化）
 * 3. 提供 SSE 流式聊天的解析器
 *
 * 所有接口都显式传 user_id —— 现阶段无登录态，服务端依据它做数据隔离。
 */

import { API_BASE_URL, CURRENT_USER_ID } from './config';
import type { AgentRole, ChatSession, Message } from '@/types';

// ==================== 后端 DTO ====================

export interface PaginationMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface SessionDTO {
  session_id: string;
  user_id: string;
  title: string;
  agent_key: string;
  /** 会话状态：1-活跃 2-已结束（后端 SessionStatusValue） */
  status: number;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface SessionListDTO {
  items: SessionDTO[];
  pagination: PaginationMeta;
}

export interface ToolCallDTO {
  id?: string | null;
  name?: string | null;
  args?: Record<string, unknown>;
}

export interface MessageDTO {
  seq: number;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  tool_calls: ToolCallDTO[];
  ts: string;
}

export interface MessageListDTO {
  session_id: string;
  total: number;
  messages: MessageDTO[];
}

/** POST /api/chat/stream 的 done 事件负载 */
export interface StreamDoneEvent {
  persisted: boolean;
  message_count: number | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// ==================== 基础请求 ====================

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });

  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try {
      const body = await response.json();
      // FastAPI 校验错误的 detail 是数组，取第一条的 msg 更易读
      if (Array.isArray(body?.detail)) {
        detail = body.detail.map((d: { msg?: string }) => d.msg ?? '').join('; ') || detail;
      } else if (typeof body?.detail === 'string') {
        detail = body.detail;
      }
    } catch {
      // 响应体非 JSON，保留默认文案
    }
    throw new ApiError(detail, response.status);
  }

  return response.json() as Promise<T>;
}

// ==================== DTO → 视图模型 ====================

/**
 * 后端时间戳解析为毫秒
 *
 * 后端存储的是 naive UTC（ISO 字符串不带 Z 后缀），若直接交给 Date.parse
 * 会被按本地时区解释而偏移。这里显式补 Z；解析失败回退当前时间，
 * 避免前端渲染出 Invalid Date。
 */
function parseUtc(iso: string): number {
  if (!iso) return Date.now();
  const normalized = iso.endsWith('Z') ? iso : `${iso}Z`;
  const ms = Date.parse(normalized);
  return Number.isNaN(ms) ? Date.now() : ms;
}

export function toChatSession(dto: SessionDTO): ChatSession {
  return {
    id: dto.session_id,
    title: dto.title,
    lastMessageAt: parseUtc(dto.updated_at),
    messageCount: dto.message_count,
    agentKey: dto.agent_key,
  };
}

/**
 * 后端消息 → 前端 Message
 *
 * 前端 Message.id 需要稳定唯一，用 `${session_id}-${seq}` 组合，
 * 这样切换会话与刷新时 React key 都不会冲突。
 */
export function toMessage(dto: MessageDTO, sessionId: string, agentRole: AgentRole): Message {
  return {
    id: `${sessionId}-${dto.seq}`,
    role: dto.role === 'user' ? 'user' : 'assistant',
    content: dto.content,
    agentRole: dto.role === 'user' ? undefined : agentRole,
    timestamp: parseUtc(dto.ts),
    toolCalls: dto.tool_calls?.length ? dto.tool_calls : undefined,
  };
}

// ==================== 接口封装 ====================

/** 分页查询会话列表（按最后活跃时间倒序） */
export async function fetchSessions(
  page = 1,
  pageSize = 30,
  userId = CURRENT_USER_ID
): Promise<{ items: ChatSession[]; pagination: PaginationMeta }> {
  const params = new URLSearchParams({
    user_id: userId,
    page: String(page),
    page_size: String(pageSize),
  });
  const data = await request<SessionListDTO>(`/api/sessions?${params}`);
  return {
    items: data.items.map(toChatSession),
    pagination: data.pagination,
  };
}

/** 创建新会话（服务端会初始化 MySQL + MongoDB + Redis） */
export async function createSession(
  agentKey: string,
  userId = CURRENT_USER_ID,
  title?: string
): Promise<ChatSession> {
  const dto = await request<SessionDTO>('/api/sessions', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, agent_key: agentKey, title: title ?? null }),
  });
  return toChatSession(dto);
}

/** 加载指定会话的完整历史消息 */
export async function fetchMessages(
  sessionId: string,
  agentRole: AgentRole,
  userId = CURRENT_USER_ID
): Promise<Message[]> {
  const params = new URLSearchParams({ user_id: userId });
  const data = await request<MessageListDTO>(
    `/api/sessions/${encodeURIComponent(sessionId)}/messages?${params}`
  );
  return data.messages.map((m) => toMessage(m, sessionId, agentRole));
}

/** 删除会话（MySQL 元数据 + MongoDB 历史 + Redis 上下文一并清理） */
export async function deleteSession(
  sessionId: string,
  userId = CURRENT_USER_ID
): Promise<void> {
  const params = new URLSearchParams({ user_id: userId });
  await request<{ message: string }>(
    `/api/sessions/${encodeURIComponent(sessionId)}?${params}`,
    { method: 'DELETE' }
  );
}

// ==================== SSE 流式聊天 ====================

export interface StreamHandlers {
  /** 收到回复增量 */
  onContent: (piece: string) => void;
  /** 节点状态更新（思考链展示） */
  onNodeUpdate?: (node: string, data: string) => void;
  /** 流结束，携带持久化结果 */
  onDone: (done: StreamDoneEvent) => void;
  /** 服务端下发 error 事件 */
  onError: (message: string) => void;
}

/**
 * 发送消息并消费 SSE 流
 *
 * 走 POST /api/chat/stream：既有打字机效果，服务端也会在推送完毕后完成
 * 三存储持久化。done 事件在持久化**之后**才下发，因此收到 done 时刷新
 * 历史一定能读到本轮消息，不存在竞态。
 *
 * @returns 供中断使用的 AbortController
 */
export function streamChat(
  payload: { sessionId: string; userId: string; agentKey: string; content: string },
  handlers: StreamHandlers
): AbortController {
  const controller = new AbortController();

  // 不 await：流式消费在后台进行，调用方通过 handlers 接收结果
  void (async () => {
    let response: Response;
    try {
      response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: payload.sessionId,
          user_id: payload.userId,
          agent_key: payload.agentKey,
          // 后端 ChatRequest 的字段名是 message，不是 content
          message: payload.content,
        }),
        signal: controller.signal,
      });
    } catch (error) {
      if (!isAbort(error)) handlers.onError(errorMessage(error));
      return;
    }

    if (!response.ok || !response.body) {
      let detail = `请求失败 (${response.status})`;
      try {
        const body = await response.json();
        if (typeof body?.detail === 'string') detail = body.detail;
      } catch {
        // 忽略解析失败
      }
      handlers.onError(detail);
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // SSE 帧以空行分隔；保留最后一段不完整的帧到下一轮
        const frames = buffer.split('\n\n');
        buffer = frames.pop() ?? '';

        for (const frame of frames) {
          const line = frame.split('\n').find((l) => l.startsWith('data: '));
          if (!line) continue;

          const raw = line.slice(6).trim();
          if (!raw || raw === '[DONE]') continue;

          let event: { type?: string; [key: string]: unknown };
          try {
            event = JSON.parse(raw);
          } catch {
            continue;
          }

          switch (event.type) {
            case 'content':
              if (typeof event.content === 'string') handlers.onContent(event.content);
              break;
            case 'node_update':
              handlers.onNodeUpdate?.(
                String(event.node ?? ''),
                String(event.data ?? '')
              );
              break;
            case 'done':
              handlers.onDone({
                persisted: Boolean(event.persisted),
                message_count:
                  typeof event.message_count === 'number' ? event.message_count : null,
              });
              break;
            case 'error':
              handlers.onError(String(event.message ?? '未知错误'));
              break;
          }
        }
      }
    } catch (error) {
      if (!isAbort(error)) handlers.onError(errorMessage(error));
    }
  })();

  return controller;
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '未知错误';
}
