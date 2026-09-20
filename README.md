# Task Agents — 多 Agent 协作对话系统

基于 LangGraph + Next.js 构建的多 Agent 协作聊天平台，支持研究员、程序员、审查员三种专业角色，通过 SSE 流式传输实现实时对话体验。

---

## 功能特性

- **多角色 Agent**：内置研究员（🔍）、程序员（💻）、审查员（✅）三种专业角色，各司其职
- **流式响应**：基于 Server-Sent Events (SSE) 的实时流式输出，逐字呈现回答
- **Markdown 渲染**：助手回复支持完整的 Markdown 格式，包括代码高亮、表格、列表等
- **会话管理**：侧边栏展示历史会话列表，支持新建对话和切换会话
- **快捷提示**：首页提供预设场景卡片，一键快速开始对话
- **Agent 切换**：输入区域可随时切换当前对话的 Agent 角色
- **中断生成**：支持随时停止正在生成中的回答

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **前端** | Next.js 14 + React 18 | App Router 架构，TypeScript 类型安全 |
| **UI** | Tailwind CSS | 原子化 CSS，配合 lucide-react 图标库 |
| **Markdown** | react-markdown + remark-gfm | 支持 GFM 扩展语法 |
| **后端** | FastAPI | 高性能异步 Web 框架 |
| **Agent 框架** | LangGraph + LangChain | 多 Agent 编排与状态管理 |
| **包管理** | uv (后端) / npm (前端) | 快速依赖管理 |

---

## 项目结构

```
task-agents/
├── backend/                    # 后端服务
│   ├── pyproject.toml          # Python 项目配置 (uv)
│   └── uv.lock                 # 依赖锁文件
│
└── frontend/                   # 前端应用
    ├── package.json            # Node.js 依赖配置
    ├── tsconfig.json           # TypeScript 配置
    ├── postcss.config.mjs      # PostCSS 配置
    └── src/
        ├── app/
        │   └── page.tsx        # 主页面（会话与聊天编排）
        ├── components/
        │   ├── layout/
        │   │   └── Sidebar.tsx # 侧边栏（会话列表、新建对话）
        │   └── chat/
        │       ├── ChatArea.tsx      # 聊天主区域（消息列表、欢迎页）
        │       ├── MessageBubble.tsx # 消息气泡（Markdown 渲染）
        │       └── InputArea.tsx     # 输入区域（Agent 选择、发送控制）
        ├── hooks/
        │   └── useAgentChat.ts # 聊天核心逻辑（SSE 流式通信）
        ├── types/
        │   └── index.ts        # 类型定义（Agent、Message、Session）
        └── lib/
            └── utils.ts        # 工具函数（cn 类名合并）
```

---

## 快速开始

### 环境要求

- **Node.js** >= 18
- **Python** >= 3.11
- **uv**（Python 包管理器）

### 1. 克隆项目

```bash
git clone <your-repo-url>
cd task-agents
```

### 2. 启动后端

```bash
cd backend
uv sync          # 安装依赖
uv run uvicorn app.main:app --reload --port 8000
```

后端默认运行在 `http://localhost:8000`。

### 3. 启动前端

```bash
cd frontend
npm install      # 安装依赖
npm run dev      # 启动开发服务器
```

前端默认运行在 `http://localhost:3000`。

---

## Agent 角色说明

| 角色 | 名称 | 描述 |
|------|------|------|
| `researcher` | 🔍 研究员 | 擅长信息检索、资料整理与深度分析 |
| `coder` | 💻 程序员 | 擅长代码编写、调试与技术实现 |
| `reviewer` | ✅ 审查员 | 擅长代码审查、质量把控与风险评估 |

用户可在输入区域通过 Agent 选择器自由切换角色，不同角色会携带各自的系统提示词参与对话。

---

## API 接口

### POST `/api/chat`

发送消息并获取流式响应（SSE）。

**请求体：**

```json
{
  "messages": [
    {
      "id": "user-1720000000",
      "role": "user",
      "content": "帮我写一个快速排序算法",
      "timestamp": 1720000000000
    }
  ],
  "agentRole": "coder"
}
```

**响应格式：** `text/event-stream`

```
data: {"content": "好的"}
data: {"content": "，下面是"}
data: {"content": "快速排序的实现"}
data: [DONE]
```

---

## 核心流程

```
用户输入 → 选择 Agent 角色 → 前端 POST /api/chat (SSE)
                                    ↓
                          后端 LangGraph 编排 Agent
                                    ↓
                          流式返回 → 前端逐字渲染 Markdown
```

---

## 开发指南

### 添加新 Agent 角色

1. 在 `frontend/src/types/index.ts` 的 `AgentRole` 联合类型中添加新角色
2. 在 `AGENTS` 数组中定义角色的名称、头像和描述
3. 在后端为对应角色配置系统提示词

### 前端常用命令

```bash
npm run dev      # 开发模式
npm run build    # 生产构建
npm run start    # 启动生产服务
npm run lint     # ESLint 检查
```

### 后端依赖管理

```bash
uv add <package>    # 添加依赖
uv remove <package> # 移除依赖
uv sync             # 同步依赖
```

---

## 技术亮点

- **前后端完全分离**：前端 Next.js 负责 UI 交互，后端 FastAPI 负责 Agent 编排，职责清晰
- **SSE 流式通信**：相比 WebSocket 更轻量，天然支持 HTTP/2 多路复用，适合单向流式场景
- **LangGraph Agent 编排**：基于有向图的状态机管理 Agent 执行流程，支持条件分支、循环和人机交互
- **类型安全**：前端全链路 TypeScript 类型覆盖，`AgentRole`、`Message`、`ChatSession` 等核心类型统一定义

---

## License

MIT
