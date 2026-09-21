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
| **Agent 框架** | LangGraph + deepagents | 主 Agent 委派子 Agent 的多智能体编排 |
| **存储** | MySQL + MongoDB + Redis | 分层存储：元数据 / 对话历史 / 会话上下文 |
| **ORM & 驱动** | SQLAlchemy 2 (async) + motor + redis.asyncio | 全链路异步，避免阻塞事件循环 |
| **包管理** | uv (后端) / npm (前端) | 快速依赖管理 |

---

## 项目结构

```
task-agents/
├── backend/                        # 后端服务
│   ├── pyproject.toml              # Python 项目配置 (uv)
│   ├── uv.lock                     # 依赖锁文件
│   ├── docker-compose.yml          # MySQL / MongoDB / Redis
│   ├── startup.md                  # 详细启动文档与排障指南
│   ├── scripts/
│   │   └── seed_demo_data.py       # 演示数据种子脚本（幂等）
│   ├── workspace/                  # programmer 子 Agent 的代码工作区（不入库）
│   └── task_agents/
│       ├── main.py                 # 应用入口：lifespan 初始化三存储 + 路由注册
│       ├── core/
│       │   ├── config.py           # 配置（LLM / 三存储 / 分页 / 编程沙箱）
│       │   └── timeutils.py        # UTC 时间工具
│       ├── database/
│       │   ├── models.py           # SQLAlchemy: chat_sessions 表 + 联合索引
│       │   ├── engine.py           # MySQL 引擎与会话工厂
│       │   ├── mongo.py            # MongoDB 客户端（motor）
│       │   ├── redis.py            # Redis 客户端
│       │   └── redis_keys.py       # Redis Key 命名规范
│       ├── schemas/
│       │   ├── api.py              # HTTP 请求/响应模型
│       │   └── mongo.py            # MongoDB 文档模型
│       ├── repository/             # 仓储层：一个存储引擎一个类
│       ├── service/                # 服务层：跨引擎业务编排
│       ├── routers/
│       │   ├── session.py          # /api/sessions*
│       │   └── chat.py             # /api/chat、/api/chat/stream
│       └── agent/
│           ├── factory.py          # Agent 注册表
│           └── market_researcher_engine/
│               ├── agent.py        # 主 Agent 装配 + 子 Agent 注册
│               ├── prompts.py      # 主 Agent 系统提示词与委派规则
│               ├── subagents/
│               │   ├── data_collector/prompt.md
│               │   ├── analyst/prompt.md
│               │   └── programmer/prompt.md
│               └── tools/
│                   ├── web_research.py   # 数据采集工具
│                   └── coding_tools.py   # 程序员沙箱工具
│
└── frontend/                       # 前端应用
    ├── package.json
    ├── tsconfig.json
    └── src/
        ├── app/
        │   └── page.tsx            # 主页面（懒创建会话、会话切换编排）
        ├── components/
        │   ├── layout/
        │   │   └── Sidebar.tsx     # 侧边栏（列表、骨架屏、空状态、删除）
        │   └── chat/
        │       ├── ChatArea.tsx      # 聊天主区域（加载态、错误提示、欢迎页）
        │       ├── MessageBubble.tsx # 消息气泡（Markdown、工具调用、流式态）
        │       └── InputArea.tsx     # 输入区域（Agent 选择、发送控制）
        ├── hooks/
        │   ├── useSessions.ts      # 会话列表（拉取/新建/删除/本地更新）
        │   └── useAgentChat.ts     # 消息（SSE 流式接收、历史加载、并发防护）
        ├── types/
        │   └── index.ts            # 类型定义 + AGENT_KEY_MAP
        └── lib/
            ├── config.ts           # user_id 与后端地址（唯一 hardcode 处）
            ├── api.ts              # 接口封装：DTO 适配 + SSE 流解析
            └── utils.ts            # cn 类名合并
```

后端调用方向严格单向：`router → service → repository`，仓储之间互不调用，事务边界由服务层控制。前后端字段差异统一在 `frontend/src/lib/api.ts` 消化，组件层只认前端视图模型。

---

## 快速开始

详细的启动步骤、排障指南与 API 说明见 [backend/startup.md](backend/startup.md)。

### 环境要求

- **Node.js** >= 18
- **Python** >= 3.14（见 `backend/pyproject.toml` 的 `requires-python`）
- **uv**（Python 包管理器）
- **Docker Desktop**（运行 MySQL / MongoDB / Redis）

### 1. 克隆项目

```bash
git clone <your-repo-url>
cd task-agents
```

### 2. 启动数据存储

```bash
cd backend
docker compose up -d          # MySQL(3306) + MongoDB(27017) + Redis(6379)
docker compose ps             # 确认三个容器均为 running
```

### 3. 启动后端

```bash
cd backend
cp .env.example .env          # 然后填入真实的 LLM_API_KEY
uv sync                       # 安装依赖
uv run uvicorn task_agents.main:app --reload --port 8000
```

后端默认运行在 `http://localhost:8000`，接口文档见 `http://localhost:8000/docs`。

启动时可写入演示数据，便于直接看到侧边栏与对话历史效果：

```bash
uv run python scripts/seed_demo_data.py
```

### 4. 启动前端

```bash
cd frontend
npm install      # 安装依赖
npm run dev      # 启动开发服务器
```

前端默认运行在 `http://localhost:3000`。

> 用户登录尚未实现，`user_id` 由 `frontend/src/lib/config.ts` 中的 `CURRENT_USER_ID` 提供（当前为 `userid_1`，与种子脚本一致）。

---

## Agent 角色说明

前端提供三种角色供用户选择，当前均映射到后端的同一个主 Agent（`market_researcher`），由主 Agent 依据任务性质委派给对应子 Agent：

| 前端角色 | 名称 | 映射的 agent_key | 实际承接的子 Agent |
|------|------|------|------|
| `researcher` | 🔍 研究员 | `market_researcher` | `data_collector`（数据采集）/ `analyst`（商业分析） |
| `coder` | 💻 程序员 | `market_researcher` | `programmer`（代码编写与运行验证） |
| `reviewer` | ✅ 审查员 | `market_researcher` | 由主 Agent 视任务委派 |

映射关系定义在 `frontend/src/types/index.ts` 的 `AGENT_KEY_MAP`。新增后端 Agent 时改这张表即可，无需改动组件代码。

### 子 Agent 能力

- **data_collector**：联网信息搜集、网页内容提取
- **analyst**：SWOT / PESTEL 等框架的商业分析与报告撰写
- **programmer**：受限沙箱内的文件读写与 Python 执行，产出**经过真实运行验证**的代码（详见下文安全边界）

---

## API 接口

完整接口清单与分层存储设计见 [backend/startup.md](backend/startup.md)。核心接口：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sessions` | 分页查询会话列表，按最后活跃时间倒序 |
| POST | `/api/sessions` | 创建会话（`session_id` 即 Agent 的 `thread_id`） |
| GET | `/api/sessions/{session_id}/messages` | 加载会话历史消息 |
| POST | `/api/chat/stream` | **前端主链路**：SSE 流式推送 + 三存储持久化 |
| POST | `/api/chat` | 发送消息，一次性返回完整回复 |

### POST `/api/chat/stream`

**请求体：**

```json
{
  "session_id": "demo-sess-0001-arch-storage",
  "user_id": "userid_1",
  "agent_key": "market_researcher",
  "content": "帮我写一个快速排序算法"
}
```

**响应格式：** `text/event-stream`

```
data: {"type": "node_update", "node": "programmer", "data": "..."}
data: {"type": "content", "content": "好的"}
data: {"type": "content", "content": "，下面是实现"}
data: {"type": "done", "persisted": true, "message_count": 4}
```

`done` 事件在**持久化完成之后**才下发，因此前端收到它时刷新历史一定能读到本轮消息，不存在竞态。

> 会话归属校验在响应开始前完成：越权返回 403，会话不存在返回 404。SSE 一旦开始发送，状态码已固定为 200，此后无法再表达权限错误。

---

## 核心流程

```
用户输入 → 选择 Agent 角色
              ↓
   （无活跃会话时先 POST /api/sessions 创建，拿到 session_id）
              ↓
   POST /api/chat/stream  (SSE)
              ↓
   后端主 Agent 按任务委派子 Agent（数据采集 / 分析 / 编程）
              ↓
   流式推送 content 帧 → 前端逐字渲染 Markdown
              ↓
   推送完毕 → 写 Redis（上下文）+ MySQL（计数/活跃时间）+ MongoDB（历史）
              ↓
   下发 done 事件 → 前端把该会话移到侧边栏首位
```

---

## 开发指南

### 添加新 Agent 角色

**前端侧：**

1. 在 `frontend/src/types/index.ts` 的 `AgentRole` 联合类型中添加新角色
2. 在同文件的 `AGENTS` 数组中定义角色的名称、头像和描述
3. 在同文件的 `AGENT_KEY_MAP` 中把新角色映射到后端 `agent_key`

**后端侧（若需要新的子 Agent）：**

4. 在 `agent/market_researcher_engine/subagents/{name}/prompt.md` 写子 Agent 提示词（结构参照现有：`# Role` / `# Workflow` / `# Output Format` / `# Constraints`）
5. 如需专属工具，在 `tools/` 下新建模块并导出工具列表（用 `@tool` 装饰器定义）
6. 在 `agent/market_researcher_engine/agent.py` 的 `subagents` 列表中注册，填 `name` / `description` / `system_prompt` / `tools`
7. 在 `prompts.py` 的主提示词中补充该子 Agent 的委派规则，否则主 Agent 不知何时该用它

> `description` 要写清"什么情况下调用"，这是主 Agent 决定委派的唯一依据。

**新增主 Agent（独立引擎）：** 复制 `market_researcher_engine/` 的结构，在 `agent/factory.py` 的 `build_agents()` 中注册到 `_REGISTRY`，前端 `AGENT_KEY_MAP` 指向新 key 即可。

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

## 分层存储架构

不同数据特征匹配不同存储引擎，避免用单一数据库硬扛所有场景：

| 存储 | 承载内容 | 选型理由 |
|------|---------|---------|
| **MySQL** | 会话元数据（`chat_sessions` 表） | 列表页要排序、分页、筛选，ACID 与二级索引正好适配。`(user_id, updated_at)` 联合索引消除 filesort |
| **MongoDB** | 对话历史（内嵌 `messages` 数组） | 文档模型一次查询取回整条会话，免 JOIN；`$push` 追加写契合高频写入 |
| **Redis** | 会话短期记忆（最近 N 轮） | 亚毫秒读写 + TTL 自动过期，为 Prompt 注入提供低延迟上下文 |

写入路径：**同步**写 Redis（上下文即时生效）+ **同步**写 MySQL（`updated_at`、`message_count`，保证侧边栏排序即时正确）+ 写 MongoDB（历史详情）。

需要接受的取舍：MySQL 计数与 MongoDB 实际条数是**最终一致**而非强一致。若 MongoDB 写入失败会出现偏差，可用 `MessageWriteService.compare_counts()` 对账，以 MongoDB 实际条数为准回写。

---

## 程序员子 Agent 的安全边界

`programmer` 能读写文件并执行 LLM 生成的代码，因此**把模型输出当作不可信输入**来设防（实现在 `tools/coding_tools.py`）：

| 边界 | 做法 |
|------|------|
| **路径隔离** | 所有文件操作限制在工作区内。先 `resolve()` 消除 `..` 与符号链接，再校验前缀——顺序不能反，否则 `workspace/../secret` 会绕过字符串前缀判断 |
| **子进程执行** | 代码在独立进程运行并带超时强制终止，死循环不会挂住主服务 |
| **凭据隔离** | 子进程只继承环境变量白名单（`PATH`、`TEMP` 等），`LLM_API_KEY`、数据库口令一律不透传 |
| **输出限长** | 所有工具返回按配置截断，防止海量输出撑爆上下文与 Token |
| **类型与体积限制** | 仅允许文本类扩展名读写；单文件有大小上限 |

配置项（见 `.env.example`）：`CODER_WORKSPACE_DIR`、`CODER_EXEC_TIMEOUT_SECONDS`、`CODER_MAX_OUTPUT_CHARS`、`CODER_MAX_FILE_BYTES`。

**已知局限（务必留意）**：这是**进程级约束，不是容器级沙箱**。被执行的代码仍拥有运行后端的那个操作系统用户的权限，能发起网络请求、读取工作区外的文件。若要用于不可信的多租户生产环境，必须再套一层容器 / gVisor 隔离。

以上边界均有实测覆盖：4 类路径穿越拦截、凭据不可见、死循环超时终止、超长输出截断。

---

## 技术亮点

- **前后端完全分离**：前端 Next.js 负责 UI 交互，后端 FastAPI 负责 Agent 编排，职责清晰
- **分层存储**：按数据特征分配存储引擎，而非用单一数据库硬扛排序、追加写与低延迟上下文三种截然不同的负载
- **SSE 流式 + 持久化**：`done` 事件刻意安排在落库之后下发，消除"前端刷新历史时读不到刚发消息"的竞态
- **多 Agent 委派**：主 Agent 按任务性质分派给数据采集 / 分析 / 编程子 Agent，各司其职
- **类型安全**：前端全链路 TypeScript 类型覆盖；后端 Pydantic 定义 HTTP 契约，与内部领域模型解耦
- **并发防护**：`useAgentChat` 用递增令牌使过期回调失效，避免"在 A 会话流式输出途中切到 B，A 的增量写进了 B 的列表"

---

## License

MIT
