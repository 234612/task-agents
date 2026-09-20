export type AgentRole = 'researcher' | 'coder' | 'reviewer';

export interface Agent {
  role: AgentRole;
  name: string;
  avatar: string;
  description: string;
}

export type MessageRole = 'user' | 'assistant';

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  agentRole?: AgentRole;
  timestamp: number;
}

export interface ChatSession {
  id: string;
  title: string;
  lastMessageAt: number;
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