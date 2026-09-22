/**
 * 前端可选角色（与后端 agent_key 一一对应）
 *
 * 2026-09 重构：旧角色 researcher / coder / reviewer 三者都指向同一个后端引擎，
 * 切换只换头像、行为无差别。现按「任务域」重命名为四个角色，各自绑定独立引擎。
 * 注意：旧会话库里的 agent_key 仍是 market_researcher，它由「调研」角色继承，
 * 因此历史会话不会失配。
 */
export type AgentRole = 'researcher' | 'developer' | 'writer' | 'analyst';

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
  /** 创建该会话时使用的 agent_key，用于回显角色（旧会话也能对上） */
  agentKey?: string;
}

export const AGENTS: Agent[] = [
  {
    role: 'researcher',
    name: '调研',
    avatar: '🔍',
    description: '联网查证、竞品与行业调研、产出分析报告',
  },
  {
    role: 'developer',
    name: '开发',
    avatar: '💻',
    description: '架构设计、编码实现与运行验证，代码真跑过再交付',
  },
  {
    role: 'writer',
    name: '写作',
    avatar: '✍️',
    description: '大纲、正文撰写与审校润色，产出可直接用的成稿',
  },
  {
    role: 'analyst',
    name: '分析',
    avatar: '📊',
    description: '数据清洗、统计建模与图表呈现，结论来自实际计算',
  },
];

/**
 * 前端 AgentRole → 后端 agent_key
 *
 * 四个角色各自对应一个独立的主 Agent 引擎（见 backend/task_agents/agent/factory.py）。
 * 新增引擎时改这张表即可，组件层无需改动。
 */
export const AGENT_KEY_MAP: Record<AgentRole, string> = {
  researcher: 'market_researcher',
  developer: 'code_engineer',
  writer: 'content_writer',
  analyst: 'data_analyst',
};

/**
 * 后端 agent_key → 前端 AgentRole（AGENT_KEY_MAP 的反查表）
 *
 * 打开历史会话时用它把会话还原成创建时的角色，避免用当前选中的角色
 * 去读另一个引擎的 thread。未知 key 一律回落到「调研」。
 */
export const AGENT_ROLE_BY_KEY: Record<string, AgentRole> = Object.entries(
  AGENT_KEY_MAP
).reduce<Record<string, AgentRole>>((acc, [role, key]) => {
  acc[key] = role as AgentRole;
  return acc;
}, {});

export function roleOfAgentKey(agentKey?: string): AgentRole {
  return (agentKey && AGENT_ROLE_BY_KEY[agentKey]) || 'researcher';
}
