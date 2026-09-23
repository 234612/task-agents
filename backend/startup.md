# Task Agents 项目启动文档

本文档详细说明如何启动和运行 Task Agents 项目。

## 环境准备

### 必需软件

- **Python**: 3.14 或更高版本
- **Node.js**: 18 或更高版本
- **Docker Desktop**: 最新版本（确保已启动并运行）
- **uv**: Python 包管理器（推荐）

### 验证环境

```bash
# 检查 Python 版本
python --version  # 应该显示 3.14+

# 检查 Node.js 版本
node --version    # 应该显示 v18+

# 检查 Docker 是否运行
docker ps         # 应该正常执行，不报错
```

---

## 第一步：启动数据库

项目使用 Docker 运行 MySQL、MongoDB 和 Redis。

```bash
cd backend
docker compose up -d
```

等待容器启动完成（约 10-30 秒），可以查看状态：

```bash
docker compose ps
```

应该看到三个容器状态为 `running`：
- `task-agents-mysql` (端口 3306)
- `task-agents-mongodb` (端口 27017)
- `task-agents-redis` (端口 6379)

### 数据库配置说明

采用**分层存储**架构，不同数据特征匹配不同存储引擎：

- **MySQL 8.0**: 存会话元数据（列表页）。使用 `utf8mb4` 字符集 + `utf8mb4_unicode_ci` 排序规则，完整支持 Unicode 字符（包括 Emoji）。核心表 `chat_sessions` 建有 `(user_id, updated_at)` 联合索引，支撑「按用户查会话 + 按活跃时间倒序分页」
- **MongoDB**: 存对话历史详情（详情页）。集合 `chat_sessions`，文档内嵌 `messages` 数组，避免多表 JOIN，支撑高频追加写
- **Redis**: 存当前会话上下文（短期记忆）。Key 规范 `memory:context:{session_id}`，List 结构 + TTL 自动过期，为 LLM Prompt 注入提供低延迟读取
- **数据持久化**: 均使用 Docker volumes，重启容器数据不会丢失

> 注意：MySQL 侧的 `message_count` 与 MongoDB 的实际消息数是**最终一致**而非强一致。若 MongoDB 后台写入失败会出现偏差，可通过 `MessageWriteService.compare_counts()` 核对。

---

## 第二步：启动后端

### 2.1 安装依赖

```bash
cd backend
uv sync
```

这会安装 `pyproject.toml` 中定义的所有依赖包。

### 2.2 配置环境变量

确保 `backend/.env` 文件存在且配置正确：

```bash
# LLM 配置（通义千问）
LLM_MODEL=qwen3.8-max
LLM_API_KEY=your-dashscope-api-key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TEMPERATURE=0.7

# MySQL 配置
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=root123
MYSQL_DATABASE=task_agents

# MongoDB 配置
MONGO_URI=mongodb://localhost:27017
MONGO_DATABASE=task_agents
MONGO_SESSION_COLLECTION=chat_sessions

# Redis 配置
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
REDIS_CONTEXT_MAX_TURNS=10
REDIS_CONTEXT_TTL_SECONDS=604800

# 应用配置
APP_DEBUG=false
CORS_ORIGINS=*
SESSION_PAGE_SIZE=20
SESSION_PAGE_SIZE_MAX=100

# ==================== 云沙箱（代码执行能力，必填） ====================
DAYTONA_API_KEY=your-daytona-key
DAYTONA_API_URL=https://app.daytona.io/api
DAYTONA_TARGET=us

# ==================== 防失控 / 防烧钱（可选，均有默认值） ====================
AGENT_RECURSION_LIMIT=30            # 单请求最大步数
TOOL_FAILURE_BREAK_THRESHOLD=3      # 同一工具+同一参数连续失败几次熔断
CODER_EXEC_TIMEOUT_SECONDS=15       # 单次沙箱执行超时（秒）

# ==================== 沙箱治理（可选，0 = 关闭/不限） ====================
SANDBOX_MAX_CONCURRENT=0            # 全局同时存活沙箱上限
SANDBOX_MAX_PER_USER=0              # 单用户持有上限
SANDBOX_IDLE_TTL_SECONDS=0          # 空闲多久销毁；0=每轮请求结束即销毁
SANDBOX_ACQUIRE_TIMEOUT_SECONDS=0   # 池满时最多排队多久
SANDBOX_AUTO_STOP_MINUTES=0         # Daytona 远端兜底自动停止
```

> 配置项以 `backend/.env.example` 为准
> **注意**：`Settings` 是 `extra=forbid`，`.env` 里出现**未定义的键**（包括拼错的）会让启动直接失败并报 `ValidationError`。新增环境变量必须同时改 `.env`、`.env.example` 和 `core/config.py` 的字段。

### 2.3 启动后端服务

```bash
uv run uvicorn task_agents.main:app --reload --port 8000
```

首次启动时，系统会自动：
- 创建 MySQL 数据库表结构（`chat_sessions` 表 + 联合索引）
- 初始化 MongoDB 连接并创建集合索引
- 初始化 Redis 连接并做连通性探测
- 构建 Agent 实例与 LLM 标题生成器

看到以下日志表示启动成功：
```
INFO [task_agents.main] MySQL 初始化完成，表结构已就绪
INFO [task_agents.main] MongoDB 连接正常
INFO [task_agents.main] Redis 连接正常
INFO [task_agents.main] Agent 构建完成，已注册: ['market_researcher']
INFO:     Uvicorn running on http://0.0.0.0:8000
```

> MongoDB / Redis 启动时不可达**不会阻断**应用启动，只会降级并打印 WARNING：
> 聊天主流程仍可写 MySQL，历史落库转为后台重试，上下文读写降级。

### 2.4 验证后端

打开浏览器访问以下接口：

- **健康检查**: http://localhost:8000/health
  ```json
  {
    "status": "ok",
    "llm": { "model": "qwen3.8-max", "provider": "阿里云通义千问" },
    "storage": { "mysql": true, "mongodb": true, "redis": true }
  }
  ```
  `status` 为 `degraded` 表示有存储引擎不可用，看 `storage` 字段定位。

- **MySQL 探活**: http://localhost:8000/health/db

- **API 文档**: http://localhost:8000/docs
  可以查看所有可用的 API 接口并进行测试。

- **会话列表**: http://localhost:8000/api/sessions?user_id=userid_1
  首次访问应返回空列表：
  ```json
  {
    "items": [],
    "pagination": { "page": 1, "page_size": 20, "total": 0, "total_pages": 1 }
  }
  ```

---

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sessions?user_id=&page=&page_size=` | 分页查询会话列表，按 `updated_at` 倒序 |
| POST | `/api/sessions` | 创建会话（初始化 MySQL + MongoDB 骨架 + Redis 上下文） |
| GET | `/api/sessions/{session_id}/messages?user_id=&skip=&limit=` | 加载会话历史消息（读 MongoDB） |
| PATCH | `/api/sessions/{session_id}/title` | 手动更新标题 |
| POST | `/api/sessions/{session_id}/archive` | 归档会话 |
| DELETE | `/api/sessions/{session_id}?user_id=` | 软删除会话（Mongo 历史保留，Redis 上下文释放） |
| POST | `/api/chat` | 发送消息，一次性返回完整回复，执行三存储双写 |
| POST | `/api/chat/stream` | **前端主链路**：SSE 流式推送 + 三存储持久化 |

`POST /api/chat` 的写入路径：**同步**写 Redis（上下文即时生效）+ **同步**更新 MySQL（`updated_at`、`message_count`）+ **异步**写 MongoDB（`BackgroundTasks`，不阻塞响应）。首轮对话额外在后台由 LLM 生成标题并回写。

`POST /api/chat/stream` 的差异：回复以 SSE 增量推送（打字机效果），推送完毕后**同步 await** 写 MongoDB，再下发 `done` 事件。之所以不交给后台任务，是为了消除竞态——前端收到 `done` 通常会立刻刷新历史，若落库尚未完成就会读不到刚发的消息。`done` 事件负载：

```json
{"type": "done", "persisted": true, "message_count": 4}
```

SSE 事件类型：`content`（回复增量）、`node_update`（思考链）、`done`（结束）、`error`（失败）。

> 归属校验：`/api/chat`、`/api/chat/stream` 与 `/messages` 都会校验 `session_id` 是否属于请求的 `user_id`，越权返回 403，会话不存在返回 404。流式接口的校验**必须在响应开始前**完成——SSE 一旦开始发送，状态码已固定为 200，此后无法再表达权限错误。

---

## 第三步：启动前端

```bash
cd ../frontend
npm install      # 首次需要
npm run dev
```

访问 http://localhost:3000。

### 3.1 用户身份（当前阶段）

登录功能留待下一阶段实现，现阶段 `user_id` 由前端 hardcode 传递，服务端直接依据它返回名下会话。配置集中在 `frontend/src/lib/config.ts`：

```ts
export const CURRENT_USER_ID = 'userid_1';   // 接入登录后只需改这一处
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';
```

### 3.2 写入演示数据

首次启动时侧边栏是空的。执行种子脚本写入演示会话，即可看到列表与对话内容效果：

```bash
cd backend
uv run python scripts/seed_demo_data.py            # 写入 4 个演示会话
uv run python scripts/seed_demo_data.py --reset    # 先清空该用户全部会话再写入
```

脚本特性：
- **幂等且安全**：会话 ID 固定。默认只覆盖这些固定的演示会话，**不会**触碰用户通过前端真实创建的数据；只有显式传 `--reset` 才清空该用户全部会话
- 三存储同时写入并自我核对：MySQL `message_count` 与 MongoDB 实际消息数一致，Redis 上下文填充最近若干条
- `updated_at` 逐条拉开差距（50 分钟前 / 1 天 / 3 天 / 5 天），用于验证侧边栏倒序
- 含 `tool_calls` 样例，便于验证工具调用渲染

### 3.3 前端交互约定

- **懒创建会话**：点「新建对话」只清空视图，不立即建会话；等用户真正发出第一条消息时才调 `POST /api/sessions`，避免侧边栏堆积空会话
- **新建会话即新建 thread**：`session_id` 同时作为 LangGraph 的 `thread_id` 传给 Agent，会话间记忆天然隔离
- **点击会话**：调 `GET /api/sessions/{id}/messages` 拉取完整历史
- **发消息**：走 `POST /api/chat/stream`，收到 `done` 后本地把该会话移到侧边栏首位并更新计数（不重新拉列表，响应更快）
- **并发防护**：`useAgentChat` 用递增令牌使过期回调失效，避免「在 A 会话流式输出途中切到 B 会话，A 的增量写进了 B 的列表」

> 时间戳注意：后端 `datetime` 列存的是 naive UTC，序列化后**不带 `Z` 后缀**。前端 `lib/api.ts` 的 `parseUtc` 会补 `Z` 再解析；若不补，东八区会偏差 8 小时（显示"8 小时前"而非"50 分钟前"）。

---

## 常见问题排查

### 1. MySQL 连接失败

**现象**: 后端启动时报错 `Can't connect to MySQL server`

**排查步骤**:
```bash
# 检查 MySQL 容器是否运行
docker compose ps mysql

# 查看 MySQL 日志
docker compose logs mysql

# 检查端口是否被占用
netstat -ano | findstr :3306
```

**解决方案**:
- 如果容器未运行: `docker compose up -d mysql`
- 如果端口被占用: 修改 `.env` 中的 `MYSQL_PORT` 为其他端口（如 3307），并更新 docker-compose.yml

### 2. MongoDB 连接失败

**现象**: 启动日志出现 `MongoDB 暂不可用` 的 WARNING，或 `/health` 返回 `degraded`

> MongoDB 不可达不会阻断启动，但历史消息将无法落库（`/api/sessions/{id}/messages` 会返回空）。

**排查步骤**:
```bash
# 检查 MongoDB 容器是否运行
docker compose ps mongodb

# 查看 MongoDB 日志
docker compose logs mongodb

# 检查端口是否被占用
netstat -ano | findstr :27017
```

**解决方案**:
- 如果容器未运行: `docker compose up -d mongodb`
- 如果端口被占用: 修改 `.env` 中的 `MONGO_URI` 指向其他端口（如 `mongodb://localhost:27018`），并同步更新 docker-compose.yml 的端口映射

### 3. Redis 连接失败

**现象**: 启动日志出现 `Redis 暂不可用，上下文读写将降级` 的 WARNING

> Redis 属于**可重建的加速层**，不可用时聊天主流程仍正常：消息照常写入 MySQL 与 MongoDB，只是短期记忆上下文失效，`seq` 分配会自动降级为按 MySQL `message_count` 计算。

**排查步骤**:
```bash
# 检查 Redis 容器是否运行
docker compose ps redis

# 容器内直接探活
docker exec -it task-agents-redis redis-cli ping

# 检查端口是否被占用
netstat -ano | findstr :6379
```

**解决方案**:
- 如果容器未运行: `docker compose up -d redis`
- 如果设置了密码，确保 `.env` 中的 `REDIS_PASSWORD` 与之一致
- 上下文 Key 可用 `docker exec -it task-agents-redis redis-cli keys "memory:*"` 查看

### 4. LLM API 调用失败

**现象**: 发送消息后长时间无响应，或 `/api/chat` 返回 502

**排查步骤**:
- 检查 `.env` 中的 `LLM_API_KEY` 是否正确
- 访问 http://localhost:8000/health/llm/verify 测试 LLM 连通性
- 检查网络连接是否正常

**解决方案**:
- 确认 API Key 有效且有足够额度
- 如果使用代理，确保环境变量 `HTTPS_PROXY` 已设置

> 502 表示 Agent/LLM 侧异常，此时**未写入任何存储**，可安全重试；403 表示越权访问他人会话；404 表示会话不存在或 `agent_key` 未注册。

### 5. 前端无法连接后端

**现象**: 前端页面加载正常，但发送消息时显示"请求失败"

**排查步骤**:
- 检查后端是否在运行: 访问 http://localhost:8000/health
- 检查浏览器控制台是否有 CORS 错误
- 确认 `.env` 中的 `CORS_ORIGINS` 包含前端地址

**解决方案**:
- 确保后端服务正在运行
- 如果前端运行在其他端口，更新 `.env` 中的 `CORS_ORIGINS`

> 前端 `src/lib/api.ts` 统一调用 `/api/*` 接口：发消息走 `POST /api/chat/stream`（SSE 流式 + 持久化），列表走 `GET /api/sessions`，历史走 `GET /api/sessions/{id}/messages`。若后端地址不是默认的 `localhost:8000`，通过 `NEXT_PUBLIC_API_BASE_URL` 环境变量覆盖。

### 6. 数据库字符集问题

**现象**: 保存中文或 Emoji 时出现乱码

**排查步骤**:
```bash
# 进入 MySQL 容器
docker exec -it task-agents-mysql mysql -u root -p

# 检查字符集配置
SHOW VARIABLES LIKE 'character_set%';
SHOW VARIABLES LIKE 'collation%';
```

**解决方案**:
- docker-compose.yml 已配置 `utf8mb4`，正常情况下不会有字符集问题
- 如果仍有问题，重建容器: `docker compose down -v && docker compose up -d`

### 7. 代码执行报错 / Agent 反复重试

**现象**: 回答里反复出现"执行环境异常"，或一条请求跑很久才结束

**排查步骤**:
```bash
# 看沙箱链路日志（[sandbox] 前缀）
# 正常应能看到：新建沙箱 → 执行开始 → 执行完成（带 exit_code 与耗时）→ 销毁沙箱
```

**常见原因与处理**:
- **未配置 `DAYTONA_API_KEY`** → 补上后重启；不配也会有明确报错，不会静默失败
- **Daytona 额度/网络问题** → 熔断会在连续失败 3 次后终止整轮，避免无限重试烧 token
- **单条请求步数耗尽**（`AGENT_RECURSION_LIMIT`）→ 日志出现 `GraphRecursionError`，说明模型在空转，可调大步数或检查提示词
- **`.env` 键名写错**（如 `DAYTONA_KEY` 而非 `DAYTONA_API_KEY`）→ 启动直接 `ValidationError`，见 2.2 的注意事项

### 8. MySQL 计数与 MongoDB 消息数不一致

**现象**: 会话列表的 `message_count` 与加载出的历史消息条数对不上

**原因**: MongoDB 走 `BackgroundTasks` 异步落库，与 MySQL 计数是最终一致。后台写入失败（如 Mongo 瞬时不可用）会造成偏差。

**排查与修复**:
```python
# 调用对账接口核对
result = await message_service.compare_counts(session_id)
# {'session_id': ..., 'mysql_message_count': 4, 'mongo_message_count': 2, 'consistent': False}
```

- 日志中搜索 `MongoDB 异步落库` 关键字可定位失败记录
- 补偿方向：以 MongoDB 实际消息数为准回写 MySQL `message_count`，或按失败日志中的 `seqs` 重放写入

---

## 停止服务

### 停止后端

在终端窗口按 `Ctrl+C`

### 停止数据库

```bash
cd backend
docker compose down
```

**注意**: 这会停止容器但保留数据（使用 volumes）。如果要完全清除数据：

```bash
docker compose down -v
```

---

## 开发建议

### 后端开发

- 修改代码后会自动重载（`--reload` 参数）
- 查看日志: 终端直接输出
- 调试模式: 在 `.env` 中设置 `APP_DEBUG=true`

### 数据库管理

- **MySQL**: 可以使用 Navicat、DBeaver 或 MySQL Workbench 连接
  - Host: localhost
  - Port: 3306
  - User: root
  - Password: root123
  - Database: task_agents

- **MongoDB**: 可以使用 MongoDB Compass 或 Studio 3T 连接
  - URI: `mongodb://localhost:27017`
  - Database: task_agents
  - Collection: `chat_sessions`（对话历史，文档内嵌 `messages` 数组）

- **Redis**: 命令行或 RedisInsight 连接
  - Host: localhost / Port: 6379 / DB: 0
  - Key 规范：`memory:context:{session_id}`（List，消息）、`memory:meta:{session_id}`（Hash，含 `last_seq`）
  ```bash
  # 查看某会话的上下文消息
  docker exec -it task-agents-redis redis-cli LRANGE "memory:context:<session_id>" 0 -1
  # 查看剩余 TTL
  docker exec -it task-agents-redis redis-cli TTL "memory:context:<session_id>"
  ```

---

## 项目结构（后端分层）

```
task_agents/
├── main.py                  # 应用入口：lifespan 初始化三存储 + 路由注册
├── core/
│   ├── config.py            # 配置（LLM / MySQL / MongoDB / Redis / 分页）
│   └── timeutils.py         # UTC 时间工具（统一 naive UTC）
├── database/
│   ├── models.py            # SQLAlchemy: ChatSession 表 + 联合索引
│   ├── engine.py            # MySQL 引擎与会话工厂
│   ├── mongo.py             # MongoDB 客户端（motor）
│   ├── redis.py             # Redis 客户端（redis.asyncio）
│   └── redis_keys.py        # Redis Key 命名规范
├── schemas/
│   ├── api.py               # HTTP 请求/响应模型
│   └── mongo.py             # MongoDB 文档模型（SessionDocument / StoredMessage）
├── repository/              # 仓储层：一个存储引擎一个类
│   ├── mysql_session_repository.py
│   ├── mongo_message_repository.py
│   └── redis_context_repository.py
├── service/                 # 服务层：跨引擎业务编排
│   ├── session_service.py   # 会话生命周期（创建/分页/历史/删除）
│   ├── message_service.py   # 三存储写入编排（双写核心）
│   ├── chat_service.py      # Agent 调用与回复解析
│   ├── title_service.py     # LLM 生成会话标题
│   ├── background.py        # 后台任务（Mongo 落库 / 标题回写）
│   └── dependencies.py      # FastAPI 依赖注入装配
└── routers/
    ├── session.py           # /api/sessions*
    └── chat.py              # /api/chat、/api/chat/stream

scripts/
└── seed_demo_data.py        # 演示数据种子脚本（幂等，默认不碰真实数据）
```

调用方向严格单向：`router → service → repository`，仓储之间互不调用，事务边界由服务层控制。

### 前端结构

```
frontend/src/
├── app/page.tsx                    # 主页面：编排懒创建会话、会话切换
├── lib/
│   ├── config.ts                   # user_id 与后端地址（唯一 hardcode 处）
│   ├── api.ts                      # 接口封装：DTO 适配 + SSE 流解析
│   └── utils.ts
├── hooks/
│   ├── useSessions.ts              # 会话列表：拉取/新建/删除/本地更新
│   └── useAgentChat.ts             # 消息：流式接收/历史加载/并发防护
├── components/
│   ├── layout/Sidebar.tsx          # 侧边栏：列表/骨架屏/空状态/删除
│   └── chat/
│       ├── ChatArea.tsx            # 对话区：加载态/错误提示/欢迎页
│       ├── MessageBubble.tsx       # 消息气泡：Markdown/工具调用/流式态
│       └── InputArea.tsx
└── types/index.ts                  # 视图模型 + AGENT_KEY_MAP
```

前后端字段差异统一在 `lib/api.ts` 消化（如 `session_id` → `id`、ISO 字符串 → 毫秒时间戳），组件层只认前端视图模型，后端 DTO 变更不影响组件。

---

## 下一步

项目启动成功后，可以：

1. **创建会话**: 点击左侧"新建对话"按钮
2. **发送消息**: 在输入框输入内容并发送
3. **查看历史**: 左侧边栏显示所有会话记录
4. **测试持久化**: 重启后端服务，验证会话和消息仍然存在

前端已完整接入 `/api/*`：侧边栏列表来自 `GET /api/sessions`，点击会话走 `GET /api/sessions/{id}/messages`，发消息走 `POST /api/chat/stream`（流式 + 持久化），新建会话走 `POST /api/sessions`。

已清理的历史包袱：
- 旧 `POST /chat` 接口与 `ChatService.stream_chat` 方法已移除，聊天能力统一由 `/api/chat` 与 `/api/chat/stream` 提供
- 旧 `sessions` 表已删除（删除前核对为 0 行），会话元数据统一存于 `chat_sessions`

尚未完成的部分：
- **用户登录**：`user_id` 仍为 `frontend/src/lib/config.ts` 中 hardcode 的 `userid_1`，接入登录后改为从登录态取真实 ID 即可，其余代码无需改动

---

## 技术支持

如有问题，请检查：
- Docker Desktop 是否正常运行
- 端口 3306、27017、6379、8000 是否被占用
- `.env` 配置文件是否正确
- 网络连接是否正常
- 访问 `/health` 查看三个存储引擎的连通状态
