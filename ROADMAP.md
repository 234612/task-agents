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

1. **`run_id` 串联**：`chat_service._thread_config()` 现在传入 `{"user_id": ...}`，加一个 `run_id`；middleware 从 `runtime.context` 取，backend 从 `get_config()` 取。
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
| `run_id` | 一轮任务唯一标识 |
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
2. **落库**：MySQL `chat_run_metrics`（`run_id`、`session_id`、`user_id`、`agent_key`、`model`、`tokens_in`、`tokens_out`、`steps`、`tool_calls`、`sandbox_ms`、`sandbox_count`、`status`、`finished_at`）。1000 并发下需批量/异步写入，避免拖慢主链路。
3. **前端**：会话底部常驻状态条——本轮 token（输入/输出）、会话累计、步数、沙箱耗时、可选成本估算。
4. **配额**：用户级 / 会话级 token 上限，超限给明确提示。

### 验收

- 每轮结束能看到确定的 token 数字（非估算）；
- 可按用户/时间聚合出报表；配额可配置且超限行为明确。

---

## 五、任务状态持久化与中断恢复（P0）

### 现状（三个真实风险）

1. SSE 断开后前端什么都拿不回，服务端也不知道任务是否还在跑；
2. 裸 Redis 无 RedisJSON，checkpointer 回落 `InMemorySaver`，**进程重启后 LangGraph 状态全丢**；
3. 系统里没有"任务（run）"概念，只有"会话 + 消息"，无法表达运行中/已取消/失败。

### 方案

1. **`task_run` 实体**：MySQL 存元数据（状态机 + 时间），MongoDB 存明细（复用第一节 `agent_spans`）。状态机 `queued → running → succeeded / failed / cancelled / timeout`。
2. **事件持久化**：SSE 事件带 `run_id` + `seq` 落 Mongo（至少关键节点），重连时按 `seq` 拉增量。
3. **取消**：取消按钮 → 服务端中断 → 状态置 `cancelled`，**已产出内容保留并落库**。需验证中断信号如何透传到阻塞的沙箱调用。
4. **超时**：任务级超时（区别于 `CODER_EXEC_TIMEOUT_SECONDS`）。
5. **幂等**：`run_id` 复用，重复提交不重复执行。

### 验收

- 关页面再打开，正在跑的任务能恢复进度；
- 重启后能识别"中断"状态的任务；
- 取消后内容不丢、沙箱被回收。

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
| `session_id` | 可选；传了续接会话，不传新建 |
| `callback` | 结果目标（topic 或 webhook URL） |
| `metadata` | 业务透传，原样带回 |
| `priority` / `timeout` | 调度与超时控制 |

### Worker

独立进程消费 → **复用现有 `ChatService`**（不重写执行逻辑）→ 结果回写 result topic + 可选 webhook（签名、指数退避、死信）。

**无侵入的含义**：业务侧只做"投递 + 订阅结果（或注册 webhook）"，不需要理解 Agent 内部；上下游串联由业务侧编排器根据结果的 `metadata` 决定下一步投什么，平台侧不耦合业务。

### 必须配套

幂等（同 `task_id` 只执行一次）、重试与死信、任务超时、任务状态查询 API（复用第五节）、`run_id` 贯通（复用第一节）。

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

## 实施顺序

| 阶段 | 内容 | 说明 |
|---|---|---|
| **阶段 1（止血与容量）** | 7.1 沙箱并发治理与 TTL、7.2 文件搬进沙箱、7.3 预算硬闸、第一节观测（middleware + span） | 都是"不改就持续亏"的项；1000 并发下沙箱治理必须先做 |
| **阶段 2（状态与度量）** | 第五节任务状态持久化、第四节 token 采集、第三节步骤事件协议 | 步骤依赖任务状态；token 依赖观测中间件 |
| **阶段 3（前端体验）** | 第二节结果卡片、第三节步骤时间线、第四节用量展示、7.5 mock 用户切换 | 后端协议先定，前端才好做 |
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
