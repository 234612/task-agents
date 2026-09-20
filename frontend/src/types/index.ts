export type AgentRole = 'researcher' | 'coder' | 'reviewer';

export interface Agent {
  role: AgentRole;
  name: string;
  avatar: string;
  description: string;
}

export type MessageRole = 'user' | 'assistant';

/** 工具调用记录，结构与后端 schemas.mongo.ToolCall 对齐 */
export interface ToolCall {
  id?: string | null;
  name?: string | null;
  args?: Record<string, unknown>;
}

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  agentRole?: AgentRole;
  timestamp: number;
  /** 助手消息可能携带的工具调用；为空表示本轮未调用工具 */
  toolCalls?: ToolCall[];
  /** 仅流式生成中为 true，用于渲染「正在输入」指示 */
  isStreaming?: boolean;
}

export interface ChatSession {
  /** 会话 ID，同时作为 Agent 的 thread_id */
  id: string;
  title: string;
  /** 最后活跃时间（毫秒时间戳），侧边栏按此倒序 */
  lastMessageAt: number;
  /** 累计消息条数，来自后端 message_count */
  messageCount?: number;
}

export const AGENTS: Agent[] = [
  {
    role: 'researcher',
    name: '研究员',
    avatar: '🔍',
    description: '擅长信息检索、资料整理与深度分析',
  },
  {
    role: 'coder',
    name: '程序员',
    avatar: '💻',
    description: '擅长代码编写、调试与技术实现',
  },
  {
    role: 'reviewer',
    name: '审查员',
    avatar: '✅',
    description: '擅长代码审查、质量把控与风险评估',
  },
];

/**
 * 前端 AgentRole → 后端 agent_key
 *
 * 后端当前只注册了 market_researcher 一个 Agent，三个角色暂时都映射到它。
 * 后续新增 Agent 时，只改这张表即可。
 */
export const AGENT_KEY_MAP: Record<AgentRole, string> = {
  researcher: 'market_researcher',
  coder: 'market_researcher',
  reviewer: 'market_researcher',
};
