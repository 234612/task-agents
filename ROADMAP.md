# Task Agents 待实现功能路线图

> 本文记录当前架构下**尚未实现、但已确认要做**的功能，以及每个功能的现状、方案要点和验收标准。
> 背景：后端 FastAPI + deepagents + Daytona 云沙箱，存储分三层（MySQL 会话元数据 / Redis 短期记忆 / MongoDB 消息与长期记忆），前端 Next.js 通过 SSE 流式渲染。
>
> 优先级：**P0** = 不做的持续损失（烧钱、丢数据、排不了障）；**P1** = 体验与对外集成；**P2** = 规模化后的治理项。

---

## 第零节：已拍板的前提与约束

| 项 | 结论 | 直接后果 |
|---|---|---|
| 数据出网 | **不出网**（开源项目） | LangSmith **不可用**，观测必须全自研；自建 Jaeger/Tempo 也只能本地 Docker 部署 |
| MQ | **Kafka**（Docker 部署） | 见第六节；分区、消费组、幂等按 Kafka 语义设计 |
| 并发量级 | **1000 并发** | 见第七节，这是当前最大的架构风险 |
| 步骤展示 | **工具调用 + 模型思考都要**，子 Agent **默认折叠** | 见第三节 |
| 登录 | **不做**，前端 mock 切换用户 | 多租户数据隔离必须保留（user_id 仍是隔离键），仅入口 mock；需在 README 明确"无鉴权，勿直接暴露公网" |
| 沙箱 | **云沙箱，现在和未来都是** | 文件读写必须搬进沙箱（见 7.2），本地 `FilesystemBackend` 路线放弃 |
| id 规范 | **谁发起谁生成，该 id 即幂等键** | 前端生成 `run_id`（uuid v4）用于重连与幂等；业务侧生成 Kafka `task_id`；服务端不再单独发号 |

### 命名与 id 层级

| id | 层 | 生成方 | 生命周期 | 用途 |
|---|---|---|---|---|
| `request_id` | 接入层 | 服务端中间件 | 一次 HTTP 请求 | 仅排障，记为 run 的属性，**不参与串联** |
| `run_id` | 执行层 | **客户端 uuid v4** | 一次 Agent 执行（一轮对话） | 主串联键：span、SSE 事件、指标、快照、幂等 |
| `task_id` | 业务层 | 业务侧（Kafka） | 一次业务任务 | 幂等键与结果回写键；平台侧对应 1..N 个 run |
| `session_id` | 会话层 | 服务端 | 长期 | `session : run = 1 : N`，**同时最多 1 个 active**；异步 job 可为 NULL；同时是 LangGraph `thread_id` |

> `request_id` 不能当串联键：SSE 重连是一次**新的 HTTP 请求**，id 必变，按 `seq` 拉增量会失效。
> `run_id` 不叫 `trace_id`：它除了串联观测，还承载状态机（`running / succeeded / ...`），语义比 trace 更重。

---

## 一、全链路日志观测（P0）

### 现状

- 沙箱链路：已加 `[sandbox]` 前缀日志（创建 / 执行开始 / 执行完成 / 拒绝 / 销毁），但只落控制台，不落库、不可查询。
- Agent 链路：**完全没有观测**——不知道一轮对话调了几次模型、用了多少 token、走了几步、工具失败几次。
- 没有 `run_id`，两层日志无法关联。

### 方案

**两层，不要合并：**

| 层 | 实现位置 | 能看到什么 |
|---|---|---|
| Agent 链路 | **middleware**（新增观测中间件） | 模型调用次数、`usage_metadata`（input/output token）、工具调用序列、每步耗时 |
| 沙箱链路 | `execute()` 内部 + `SandboxManager`（span） | sandbox uuid、翻译后的真实代码、远端耗时、exit_code |

middleware 拿不到 sandbox uuid 与翻译后的代码，`execute()` 拿不到 token，两层必须都有。

**关键设计点：**

1. **`run_id` 串联**：`chat_service._thread_config()` 现在传入 `{"user_id": ...}`，加一个 `run_id`；middleware 从 `runtime.context` 取，backend 从 `get_config()` 取。`session : run = 1 : N`（同时最多一个 active），每轮对话一个 run。HTTP 入口额外生成 `request_id` 记为 run 的属性（排障用），**不参与串联**。
2. **三个 span 边界**：`sandbox.acquire`（创建/复用）、`sandbox.exec`（远端执行）、`sandbox.release`（销毁）。只做 exec 不完整——创建常 1~3 秒，销毁失败会漏资源。
3. **观测与控制分离**：熔断（`ToolFailureCircuitBreaker`）是控制流，会改变执行结果；观测只记录。必须拆成两个独立中间件。
4. **载荷默认只记指纹和长度**（`code_sha1`、`out_len`），原文走开关 + 截断。
5. **错误分类**：`rejected` / `infra_error` / `timeout` / `ok`，分类后才能做有意义的告警。

**存储分层（不出网，全部自建）：**

| 数据 | 存储 | 保留 | 1000 并发下的处理 |
|---|---|---|---|
| span 明细 | MongoDB `agent_spans`，按 `run_id` 索引 | TTL 7~30 天 | **必须采样**：失败/超时/超阈值慢轮次存全量，成功轮次按百分比采样或只存汇总 |
| 每轮汇总 | MySQL `chat_run_metrics`（一请求一行） | 长期 | 量可控（1000 并发 ≈ 每秒数百行，需批量写入 + 分区表） |
| 本轮实时计数 | Redis / 进程内 | 请求结束即弃 | — |
| 链路可视化 | 本地 Jaeger + OTLP（可选，Docker） | 短周期 | 采样率压到 1% 以下 |

> MySQL 不适合存明细：表结构手动迁移，而 span 字段必然持续演进。
> span 明细不要塞进 messages 数组，会撑爆历史会话、拖慢前端。
> **LangSmith 已出局**（数据不出网），自有 trace 若需要可视化，用本地 Docker 的 Jaeger/Tempo 接 OTLP。

### 验收

- 一条 `[sandbox]` 日志和一个 middleware 事件能用 `run_id` 对上；
- 任一会话可查到：模型调用次数、总 token、工具调用列表、沙箱 uuid 与耗时；
- 采样配置可动态调整（出问题临时开到 100%）。

---

## 二、沙箱执行结果的前端展示优化（P1）

### 现状

SSE 的 `tool_result` 是一段纯文本（stdout 原文），用户看到的是一行 `0` 加模型转述的"命令执行成功，退出码 0"——信息密度低、不可复制、看不出跑的代码、不知道耗时。

### 方案

工具结果升级为**结构化结果卡片**，后端补字段、前端按字段渲染：

| 区块 | 内容 | 来源 |
|---|---|---|
| 头部 | 工具名 + 状态标签（成功/失败/超时/被拒绝）+ 耗时 | `sandbox.exec` span |
| 代码区 | 实际执行的代码（语法高亮、可折叠、可复制） | `execute()` 翻译后的 code |
| 输出区 | stdout / stderr 分区，超长折叠（后端已有 `CODER_MAX_OUTPUT_CHARS=8000`） | `ExecuteResponse.output` |
| 元信息 | exit_code、sandbox_id（折叠，排障用） | span 字段 |
| 图表 | Daytona `artifacts.charts`（matplotlib 图表元数据）直接渲染 | `ExecuteResponse.artifacts` |

错误态用可折叠堆栈块 + "复制错误"按钮；多轮执行的结果卡片按时间堆叠而非覆盖。

### 验收

- 不依赖模型转述即可看到"跑了什么代码 → 输出什么 → 花了多久"；
- 长输出默认折叠，展开不丢内容；失败信息一眼可见且可复制。

---

## 三、任务执行步骤可视化（P0 设计 / P1 实现）

### 需求已明确

**工具调用和模型思考都要展示**，子 Agent **默认折叠**（可点击展开）。

### 现状

SSE 已有 `meta` / `content` / `tool_call` / `tool_result` / `thinking` / `done` / `error`，但事件**平铺**：无步骤编号、无父子关系、无状态，前端画不出"执行到哪一步"；子 Agent 内部完全不可见。

### 方案

**两层结构**：阶段（planning / researching / coding / verifying / summarizing）+ 步骤（单次工具调用或模型轮次）。

**事件协议扩展**（向后兼容，只加字段）：

| 字段 | 说明 |
|---|---|
| `run_id` | 一次执行唯一标识（客户端生成，同时是幂等键） |
| `seq` | 全局递增序号，用于断线重连拉增量 |
| `step_id` / `parent_step_id` | 步骤标识与父子关系；子 Agent 的步骤 `parent` 指向委派它的 `task` 调用 |
| `status` | `start` / `end` / `error` |
| `kind` | `model` / `tool` / `subagent` / `stage` |

同一步骤发两次事件（start / end），前端据此渲染"进行中 / 已完成"。

**前端形态**：可折叠时间线。子 Agent 作为嵌套节点**默认折叠**，显示名称 + 耗时 + 内部步骤数，点击展开。

### 验收

- 任务进行中能看到"当前第 N 步"，不是只有转圈；
- 模型思考与工具调用都可见；子 Agent 默认收起、可见委派对象与耗时；
- 刷新后可重建进度（联动第五节）。

---

## 四、资源消耗 / token 用量展示（P1）

### 方案

1. **采集**：只有 middleware 的 `after_model` 能稳定拿到 `AIMessage.usage_metadata`。按 `run_id` 累加，请求结束输出汇总。
2. **落库**：MySQL `chat_run_metrics`（`run_id`、`session_id`、`user_id`、`agent_key`、`model`、`tokens_in`、`tokens_out`、`steps`、`tool_calls`、`sandbox_ms`、`sandbox_count`、`status`、`finished_at`）。1000 并发下需批量/异步写入，避免拖慢主链路。**与第五节 `agent_run` 是同一行**，不另建表。
3. **前端**：会话底部常驻状态条——本轮 token（输入/输出）、会话累计、步数、沙箱耗时、可选成本估算。
4. **配额**：用户级 / 会话级 token 上限，超限给明确提示。

### 验收

- 每轮结束能看到确定的 token 数字（非估算）；
- 可按用户/时间聚合出报表；配额可配置且超限行为明确。

---

## 五、任务状态持久化与中断恢复（P0）—— L2 完整续流

### 现状（四个真实风险）

1. **刷新页面 = 这一轮彻底消失，连用户消息都丢。**
   根因：生成任务跑在 SSE 生成器的栈里（`ChatService.stream_chat_persist`），
   连接断开时 Starlette 会 `aclose()` 生成器 → `completed` 停在 `False` →
   `on_complete`（即 `persist_turn`）不执行。而 `persist_turn` 是**用户消息与助手
   消息一起写**的，所以不只是回复丢了，刚发出去那条也没进 MySQL / Mongo / Redis。
   前端 `api.ts` 用 `fetch` + `getReader`，无重连、无 `Last-Event-ID`、无心跳。
2. SSE 断开后服务端不知道任务是否还在跑（没有 run 实体）。
3. 裸 Redis 无 RedisJSON，checkpointer 回落 `InMemorySaver`，**进程重启后 LangGraph 状态全丢**。
4. 系统里没有"任务（run）"概念，只有"会话 + 消息"，无法表达运行中/已取消/失败。

> **顺带必修**：`chat_service.py` 的 `finally` 里 `await pool.release(session_id)`
> 当前是**注释掉的**。任务后台化后必须恢复，否则每个 run 跑完都不释放沙箱，
> 1000 并发下池子几分钟就打满。

### 目标档位（已选 L2）

| 档 | 刷新后能看到什么 | 结论 |
|---|---|---|
| L0 消息先落库 | 历史里有这条消息，标"生成中断"，可一键重发 | **必做**，是 L1/L2 的前提 |
| L1 后台跑完 | 任务在后台跑完落库，重连后拿到最终结果 | 被 L2 覆盖 |
| **L2 完整续流** | 重连后**补发已生成内容 + 步骤时间线**，继续流式输出 | **已选** |

### 方案

**1. 执行与连接解耦（核心）**

run 不再活在 SSE 生成器里，而是**后台 `asyncio.Task`**，SSE 只是它的订阅者：

| 接口 | 职责 |
|---|---|
| `POST /api/runs` | 启动：用户消息**立即落库** → 建 run 记录 → 起后台 task → 返回 `run_id` |
| `GET /api/runs/{run_id}/events` | 纯订阅，支持 `Last-Event-ID`，可断可重连 |
| `DELETE /api/runs/{run_id}` | 取消：取消 task、置 `cancelled`、回收沙箱 |
| `GET /api/sessions/{id}/runs?status=active` | 加载会话时查在跑的 run |

拆成两段式是白捡的收益：`EventSource` 只支持 GET，且**原生自动重连 + 自动带
`Last-Event-ID`**，前端可直接替掉现在 `fetch` + `getReader` 的手写实现。
旧的 `POST /chat/stream` 保留一段时间兼容，内部转成"启动 run + 直接订阅"。

**2. 事件落库策略：低频事件进库，正文走快照**

content 增量**绝不逐条落库**（一条回复数百 chunk × 1000 并发 = 写爆）：

| 数据 | 方式 | 存储 |
|---|---|---|
| `thinking` / `tool_call` / `tool_result` / 状态变更 | 逐条 append（每轮几十条） | MongoDB `agent_runs` |
| 正文 draft | **快照 upsert**（约 1s 或每 N 字符一次） | Redis `run:draft:{run_id}`，TTL 30 分钟 |
| 最终结果 | run 结束时写一次 | MongoDB messages（沿用 `append_messages`） |

重连时：先读快照补齐全文 → 再订阅增量。花 L1 的存储成本拿到 L2 的体验。

**3. 加载会话时的恢复流程**

```
GET /api/sessions/{id}/messages           ← 已完成的历史轮次
GET /api/sessions/{id}/runs?status=active ← 0 或 1 个（唯一，见下）
  ├─ 0 个 → 正常渲染历史
  └─ 1 个 → 订阅它：补快照 → 续流
```

**不变量：一个 session 同时最多一个 active run。** 对话是串行的——用户发一条、
看完、再发下一条，不存在并发生成。所以这不是"先不加锁"的权宜之计，而是应被
**强制**的约束：MySQL 加 `active_flag`（活跃时 1，终态置 NULL）配合
`UNIQUE (session_id, active_flag)` 唯一索引——MySQL 唯一索引允许多个 NULL，
终态行天然互不冲突。第二个 run 进来直接 **409「上一条还在生成」，不排队**。

> **两步请求的竞态**：拉完历史后 run 恰好结束并落库，会同时出现在历史消息和
> active 查询里，被渲染两遍。解法：assistant 消息落库时带上 `run_id`，前端发现
> 某个 active run 的 id 已出现在历史中就直接跳过它。
>
> **只查 active，不查全部**：加载会话只需要"有没有还没跑完的"。已完成的轮次内容
> 在 messages 里，run 记录是历史归档性质，全量拉只会越拉越大。

**4. 恢复边界：只做"续流"，不做"续跑"**

| 情况 | 判定 | 行为 |
|---|---|---|
| run 在跑且心跳新鲜 | `status=running`，`heartbeat_at` 未过期 | **续流**：补快照 + 继续推送 |
| run 标 running 但心跳已死 | 进程重启/崩溃，后台 task 随进程消失 | 置 `interrupted`，展示"已中断" + **重新生成**按钮（复用同一 `run_id` 重投，幂等） |
| run 已完成但前端没收到 done | `status=succeeded` | 直接拉最终结果，秒开 |

**不做断点续跑**（从 LangGraph checkpoint 恢复 agent 继续执行）：恢复点语义复杂
（工具调用执行到一半怎么算）、成本不可控，而用户真正要的是"别丢已生成的内容"——
内容续展已经覆盖。中断时**清掉该 thread 的 checkpoint**，避免留下"AIMessage 带
tool_calls 却没有对应 ToolMessage"的半截状态把会话废掉；下一轮靠已有的
`_seed_context` + Redis 冷启动回灌恢复——这也是 **L0 必须先落用户消息**的原因。

**5. 僵尸回收与心跳**

- SSE 心跳帧 15 秒一次（`:ping` 注释帧）：防 Nginx / 代理掐空闲连接，也让服务端
  及时感知死订阅者。
- run 记录带 `worker_id` + `heartbeat_at`；进程启动时把"本 worker 名下仍标
  running"的 run 批量置 `interrupted`。
- **订阅者全部断开 ≠ 停止任务**（这正是本方案的目的），但不能无限跑：任务级超时
  （区别于 `CODER_EXEC_TIMEOUT_SECONDS`）。

**6. 取消与幂等**

- 取消：取消按钮 → 取消后台 task → 状态置 `cancelled`，**已产出内容保留并落库**。
  需验证中断信号如何透传到阻塞的沙箱调用。
- 幂等：`run_id` 由客户端生成并随请求带上，服务端校验唯一 → 既是主键也是幂等键。
  重复提交不重复执行，页面刷新/重连天然安全。Kafka 入口同理用业务侧 `task_id`。

### 两类执行：交互式 run 与异步 job

**异步任务不是"用户的 agent 任务"**——其他部门投递 job 只是想拿结果，平台侧
负责执行完回写队列即可，没有 UI、没有会话、没有续流需求。因此明确分成两类：

| | 交互式 run（用户对话） | 异步 job（Kafka / 其他部门） |
|---|---|---|
| `source` | `chat` | `kafka` |
| 会话 | 必有 `session_id` | `session_id` **可空**（一次性任务不建会话、不进用户历史） |
| SSE 订阅 | 有，需续流 | **无** |
| draft 快照 | 写 Redis | **不写**——没人看中间态，省掉 1000 并发下的 Redis 写放大 |
| 产物 | 落 MongoDB messages | 结果回写 `agent.task.result` topic；**不进用户消息历史**，平台侧只留采样 span（第一节） |
| 取消 / 重试 | 用户可取消；任务级超时 | 任务级超时；**重试与死信由 Kafka 侧负责**，不是 run 的职责 |

两者**共用执行内核与状态机**（同一张 `agent_run`），差异只在"有没有订阅者"和
"结果投递到哪里"——不写两套代码。任务状态查询 API 因此天然统一（第六节复用第五节）。

> **无会话 job 的 `thread_id` 怎么取**：当前 `thread_id = session_id`。`session_id`
> 为 NULL 时改用 **`run_id` 当 thread_id**——LangGraph 隔离与沙箱池
> （`pool.acquire(thread_id)`）都因此落到**单次任务粒度**，一次性 job 跑完沙箱即
> 销毁、不参与会话级复用，反而更干净。

### 存储

| 数据 | 存储 | 保留 |
|---|---|---|
| run 元数据 + 状态机 | MySQL `agent_run`（`run_id` PK，索引 `(session_id, status)`，唯一索引 `(session_id, active_flag)`） | 终态行 7~30 天后归档清理；1000 并发下需分区表 + 批量写 |
| draft 快照 + 低频事件 | Redis（TTL 30 分钟） | 请求结束即弃 |
| 本轮明细 span | MongoDB `agent_spans`，按 `run_id` 索引 | TTL 7~30 天 + 采样（见第一节） |

> 第四节的 `chat_run_metrics` 与这里的 `agent_run` 是**同一行**，不建两张表：
> run 结束时一次写入 token / 步数 / 沙箱耗时等指标。
>
> 状态机：`running → succeeded / failed / cancelled / interrupted`。**没有 `queued`**——
> 排队由入口负责：HTTP 侧同会话第二个 run 直接 409 拒绝，Kafka 侧由分区与消费组排队。
> run 层再叠一层排队只会把两处的超时语义搅乱。

### 验收

- 生成中刷新页面：回来后**已生成的文字还在**，并接着往下出，不是空白等到结束；
- 步骤时间线能重建（子 Agent 折叠状态、"当前第 N 步"）；
- 关页面再打开，正在跑的任务能恢复进度；
- 进程重启后能识别"中断"状态，不会永远转圈，且会话能继续对话（不被半截 checkpoint 卡死）；
- 取消后内容不丢、沙箱被回收；
- 刷新后用户消息不会消失（L0）。

---

## 六、Kafka 任务入口 + 钩子（P1）

### 目标

业务系统投递任务到 Kafka，Agent 异步执行，结果回写 Kafka（可选 webhook），支持上下游串联，对业务侧无侵入。全部 Docker 部署，不出网。

### Topic 设计

| Topic | 用途 | 分区键 | 说明 |
|---|---|---|---|
| `agent.task.submit` | 任务提交 | `task_id` | 保证同一任务顺序 |
| `agent.task.result` | 结果回写 | `task_id` | 成功/失败都写，带终态 |
| `agent.task.event` | 过程事件（可选） | `task_id` | 步骤级事件流，供下游做实时响应 |
| `agent.task.dlq` | 死信 | — | 重试耗尽后进入 |

分区数按 1000 并发设计（建议起始 12~24 分区，可扩）；消费组 `agent-worker`，worker 无状态可水平扩容。

### 任务协议

| 字段 | 说明 |
|---|---|
| `task_id` | 业务侧生成，幂等键 |
| `agent_key` | 目标引擎 |
| `payload` | 任务输入（对应现在 `message`） |
| `session_id` | 可选，**异步 job 通常不传**（不建会话、不进用户历史、不写 draft）；传了才续接会话 |
| `callback` | 结果目标（topic 或 webhook URL） |
| `metadata` | 业务透传，原样带回 |
| `priority` / `timeout` | 调度与超时控制 |

### Worker

独立进程消费 → **复用现有 `ChatService`**（不重写执行逻辑）→ 结果回写 result topic + 可选 webhook（签名、指数退避、死信）。

**无侵入的含义**：业务侧只做"投递 + 订阅结果（或注册 webhook）"，不需要理解 Agent 内部；上下游串联由业务侧编排器根据结果的 `metadata` 决定下一步投什么，平台侧不耦合业务。

### 必须配套

幂等（同 `task_id` 只执行一次）、重试与死信、任务超时、任务状态查询 API（复用第五节）、`run_id` 贯通（复用第一节）。Kafka 入口由 worker 用 `task_id` 派生 `run_id`，保证与 HTTP 入口共用同一套续流、观测与幂等机制。

### 验收

- 投递后业务侧能在 Kafka / webhook 收到结构化结果；
- 重复投递不重复执行；失败进死信可查；任务状态可实时查询。

---

## 七、1000 并发下的容量与成本设计（P0，新增）

**这是当前最大的架构风险**：1000 并发 × 云沙箱，如果每个并发请求都持有一个沙箱实例，账单和资源都会失控。必须在实现上述功能前先定策略。

### 7.1 沙箱并发治理（最高优先级）

> **配置项已就位**（`core/config.py` + `.env` + `.env.example`）：
> `SANDBOX_MAX_CONCURRENT` / `SANDBOX_MAX_PER_USER` / `SANDBOX_IDLE_TTL_SECONDS` /
> `SANDBOX_ACQUIRE_TIMEOUT_SECONDS` / `SANDBOX_AUTO_STOP_MINUTES`。
> 约定 **0 = 关闭 / 不限，保持现状**，所以加了字段不改变线上行为；接线逻辑按下面逐项做。

- **沙箱池上限**：全局同时存活沙箱数设上限（先用 50 压测），超过则**排队等待**（`SANDBOX_ACQUIRE_TIMEOUT_SECONDS` 控制最多等多久）而非无限创建。
- **空闲 TTL + 会话级复用**：`release` 改标记空闲，空闲超 N 分钟（建议 10）销毁；`auto_stop_interval` 改为 15 做远端兜底；lifespan shutdown 全量清理；启动时按 `labels.thread_id` 回收孤儿。
- **降级策略**：沙箱不可用时，明确告知模型"执行环境繁忙/不可用"，而不是让请求无限等待或反复重试（配合现有熔断）。
- **单用户配额**：同一 user_id 同时持有的沙箱数上限，防止单用户打满池子。

### 7.2 文件读写全部搬进云沙箱（P0）

现状是真 bug：`write_file` 落本地 `workspace/`，`execute` 跑在远端云沙箱，`python script.py` 找不到刚写的文件。既然确定长期用云沙箱：

- 用 Daytona 的 filesystem API 实现 `read` / `write` / `ls` / `glob` / `grep`，`RestrictedSandboxBackend` 不再继承本地 `FilesystemBackend`，改为全部走 `sandbox.fs`。
- 好处：文件与执行同环境；配合会话级复用，跨轮文件自然保留。
- 注意：文件传输要走沙箱 SDK（网络往返），需评估大文件的超时与大小限制。

**路径语义**（实现时定下的关键约定）：模型给的路径一律按**沙箱工作目录的相对路径**
处理，前导 `/` 剥掉。这样 `write_file("/a.py")` 与 `execute` 里的 `open("a.py")`
指向同一个文件——否则"写进去了但执行时找不到"会以另一种形式复发。

**仍未覆盖（P0 遗留，需单独处理）**：子 Agent 用的 `SANDBOX_TOOLS`
（即 `market_researcher_engine/tools/coding_tools.py`）是**另一条执行路径**——
`run_python_code` 走本地 `subprocess.run`，文件读写落在本地 `workspace/`。
`code_engineer` / `data_analyst` / `market_researcher` 的子 Agent 都在用它。
结果就是：主链路已经在云沙箱、子 Agent 还在本地跑，两个世界的问题依然存在，
而且本地 `subprocess` 与"数据不出网 + 云沙箱"的前提相悖。
改法：让 `coding_tools` 通过 `get_config()` 取 thread_id 后路由到
`SandboxManager`（与 backend 同一环境），或直接废弃它、让子 Agent 也用
deepagents 内置的 backend 工具。**动这块前先摸清子 Agent 现有行为，避免回归。**

### 7.3 预算硬闸

现有：步数上限 `AGENT_RECURSION_LIMIT=30` + 连续失败熔断 `TOOL_FAILURE_BREAK_THRESHOLD=3`。
还需补：单请求 token 上限、单请求工具调用总次数上限（抓"参数每次都不同"的慢速空转）、并发沙箱上限、用户级配额。

### 7.4 1000 并发的其它承载点

| 点 | 要求 |
|---|---|
| 事件循环 | 所有同步阻塞调用（Daytona SDK）必须 `to_thread`，已做但需覆盖新增路径 |
| FastAPI | worker/uvicorn 实例数与沙箱排队模型匹配；SSE 长连接的超时与心跳 |
| MySQL | 连接池上限、`chat_run_metrics` 批量写入、索引与分区 |
| MongoDB | span 采样；`agent_spans` TTL 索引 |
| Redis | 连接池；1000 并发下 checkpointer 若启用 Redis 需 Redis Stack（当前回落内存，多实例部署时状态不一致问题会被放大） |
| Kafka | 分区数、消费者数量与 worker 扩容策略 |

### 7.5 多租户隔离（mock 用户切换）

登录不做，但**数据隔离必须保留**：`user_id` 仍是唯一隔离键。前端加一个 mock 用户切换器（下拉选择），便于验证隔离。README 需写明"无鉴权，禁止直接暴露公网"。

### 7.6 其它 P1/P2

- **取消与超时控制**（P1，与第五节联动）。
- **回归评测流水线**（P1）：`AGENT_TEST_CASES.md` 跑成可重复回归集，改 prompt / 换模型 / 改工具后自动跑，防退化。
- **安全性复核**（P1）：文件路径逃逸、产物目录越界、`execute` 拒绝策略的绕过写法。
- **上下文与长期记忆治理**（P2）：`REDIS_CONTEXT_MAX_TURNS=3` 是否够、超长会话 summarization、长期记忆召回与去重。
- **部署与运维**（P2）：docker-compose 编排（Kafka / MySQL / Mongo / Redis / 后端 / 前端 / 可选 Jaeger）、健康检查、优雅关闭（含沙箱清理）、日志落盘轮转、Daytona 配额告警。

---

## 实施进度

> 做完一条更新一条。已完成的项在此标注，正文保留方案全文供后续维护参考。

| 项 | 状态 | 落地要点 |
|---|---|---|
| **7.1 沙箱并发治理与 TTL** | ✅ 已实现（默认关闭） | 容量闸门下沉到 `SandboxManager`（同步层，两条路径都管得住）；池负责空闲 TTL、回收器、池满轮询等待、关闭全量清理；`SANDBOX_RECLAIM_ORPHANS` 默认 0（多实例会误删）。新增配置 3 项 |
| **7.2 文件读写搬进云沙箱** | ⚠️ 部分实现 | `RestrictedSandboxBackend` 的 `ls/read/write/edit/glob/grep/delete/upload/download` 已全部走 `sandbox.fs`（此前是 `NotImplementedError`，文件工具根本不可用）。**遗留**：子 Agent 的 `SANDBOX_TOOLS` 仍在本地跑 `subprocess`，见 7.2 备注 |
| **7.3 预算硬闸** | ✅ 已实现（默认关闭） | `AGENT_MAX_TOKENS_PER_RUN` / `AGENT_MAX_TOOL_CALLS_PER_RUN`，在 model call **之前**抛 `BudgetExceeded`；与熔断是独立中间件 |
| **第一节 观测（middleware + span）** | ✅ 已实现 | `agent/middleware/observability.py`：模型调用次数、`usage_metadata` token、工具序列与耗时；`run_id` 串联（当前服务端生成）；沙箱耗时由 backend 反向汇入；Mongo `agent_spans` + TTL 索引 + 采样（失败/慢轮次全采） |
| **第五节 L0：用户消息先落库** | ✅ 已实现 | 流式路径在生成**前**落用户消息（Mongo 同步 + Redis + MySQL），结束时只补助手消息；`_seed_context` 加了去重（否则本轮用户消息会被喂两遍） |
| 第五节 L1/L2 续流 | ⬜ 未开始 | 依赖 run 实体（`agent_run` 表）与两段式接口，属阶段 2 |
| 第三节 步骤事件协议 | ⬜ 未开始 | 依赖上一行的 run 实体 |
| 第三节/二节 前端 | ⬜ 未开始 | `EventSource` 替换 `fetch` + `getReader` |
| 第六节 Kafka 入口 | ⬜ 未开始 | 依赖状态机与幂等 |
| 7.4 / 7.5 / 7.6 | ⬜ 未开始 | — |

**顺带修掉的两个既有缺陷**：
1. `chat_service.stream_chat_persist` 的 `finally` 里 `await pool.release(session_id)`
   是**注释掉的** → 沙箱从不回收。已恢复，无论成功/异常/断连都会释放。
2. `SANDBOX_*` 治理配置此前只有字段、没有接线。已接线，且全部默认 0（不改变现有行为）。

**校验**：`backend/scripts/verify_p0.py`（不依赖外部服务，用假 Daytona / 假仓储），
覆盖容量闸门、TTL 与回收器、观测计数、预算硬闸、路径解析、L0 落库与上下文去重、
流式链路端到端（含"刷新断连仍释放沙箱"）。跑法：
`cd backend && .venv/Scripts/python.exe scripts/verify_p0.py`。

---

## 实施顺序

| 阶段 | 内容 | 说明 |
|---|---|---|
| **阶段 1（止血与容量）** | 7.1 沙箱并发治理与 TTL、7.2 文件搬进沙箱、7.3 预算硬闸、第一节观测（middleware + span） | 都是"不改就持续亏"的项；1000 并发下沙箱治理必须先做 |
| **阶段 2（状态与度量）** | 第五节 run 实体 + L2 续流（含 L0 用户消息先落库）、第四节 token 采集、第三节步骤事件协议 | 步骤与续流都依赖 run 实体；token 依赖观测中间件 |
| **阶段 3（前端体验）** | 第二节结果卡片、第三节步骤时间线、第四节用量展示、7.5 mock 用户切换、`EventSource` 改造（替换 `fetch` + `getReader`） | 后端协议先定，前端才好做 |
| **阶段 4（对外集成）** | 第六节 Kafka 入口与钩子 | 依赖阶段 2 的状态机与幂等 |

---

## 仍待确认

1. **沙箱并发上限的具体数值**（取决于预算；建议先用 50 压测，看 Daytona 侧表现）。
2. **排队策略**：超出上限时是排队等待、还是直接降级为"无沙箱执行"（后者体验差但不阻塞）。
3. **span 采样率**：默认百分比 + 失败/慢请求全采，具体阈值待定。
4. **Kafka 分区数与 worker 副本数**：需压测后定。
5. **checkpointer 持久化后端**：`docker-compose.yml` 当前是 `redis:7-alpine`（官方 OSS，
   无 RedisJSON / RediSearch，实测 `JSON.SET` 报 unknown command）→ auto 探测失败回落
   内存。已定：换 **`redis:8-alpine`**（实测 Redis 8.10.2，自带 search / ReJSON 等模块，
   Redis 8 起 Stack 已被官方取代）。
   **长期风险**：云托管 Redis（ElastiCache / Memorystore / Azure Cache 非 Enterprise 档）
   均不支持这些模块，若未来上托管，checkpointer 必须改走 MongoDB
   （需装 `langgraph-checkpoint-mongodb`）。本项目自部署 Docker，不受影响。
6. ~~同一会话并发 run 是否排队~~ **已定**：不排队。一个 session 同时最多一个 active run，
   用 `UNIQUE (session_id, active_flag)` 强制，第二个直接 409。异步 job 不占会话
   （`session_id` 可空），与交互式 run 分开，不存在"同一会话并发"的场景。
7. **`agent_run` 终态行保留时长**：暂定 30 天后归档清理，需与分区策略和磁盘一起压测确认。
