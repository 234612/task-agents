# 项目长期记忆（E:\2\task-agents）

## 架构

- `backend/task_agents`：FastAPI 分层（routers → service → repository），
  存储分工：MySQL 会话元数据 / Redis 短期记忆 / MongoDB 消息与长期记忆。
- `frontend`：Next.js，API 封装在 `src/lib/api.ts`。

## 约定

- 会话状态用**整型** 1-活跃 / 2-已结束（`SessionStatusValue`），不做软删除；
  MySQL 表结构变更要手动迁移，`create_all` 不会 ALTER 已有列。
- 会话标题规则：用户首句前 50 字（`core/titleutils.py`），不调 LLM。
- 聊天请求体字段是 **message**（不是 content）。
- LangGraph checkpointer 后端由 `CHECKPOINT_BACKEND` 控制（auto/redis/memory）；
  Redis 版需要 RedisJSON + RediSearch，ttl 单位是分钟。
- **四个主 Agent 引擎**：`market_researcher`(调研) / `code_engineer`(开发) /
  `content_writer`(写作) / `data_analyst`(分析)，`agent/factory.py` 用 `common`
  统一装配；新增引擎 = 复制 `*_engine/` + factory 加一行 + 前端两张映射表。
- 工具共享走 `agent/shared_tools.py`（`SANDBOX_TOOLS` / `WEB_TOOLS`），
  禁止跨引擎 import 其他引擎内部的 tools 模块。
- **当前无搜索工具**（Tavily 已停用），只有 `extract_web_content` 抓正文；
  采集类提示词要让模型如实说"无法检索"，禁止编造来源与 URL。
- `session_id` 同时是 LangGraph `thread_id`，**切换角色必须新建会话**。

## 排错要点

- 重构后必查 routers / service/dependencies 里的残留旧引用。
- 本地自测脚本用 httpx 时加 `trust_env=False`，否则走环境代理。
- venv 里**没有 pip**，装包用 `uv pip install --python <venv>\Scripts\python.exe`。
- bash 的 coreutils（ls/rm/head）在本沙箱不可用，用 PowerShell；PowerShell 的
  stdout 常被吞，需「写文件 + Read」取结果（`rm` 被 shim 拦截时用 python 删）。
- pydantic Settings 字段保持**大写**访问：`get_settings().AGENT_RECURSION_LIMIT`，
  小写会 AttributeError（`case_sensitive=False` 只影响环境变量匹配）。
- PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名 → 用 `curl.exe`；
  反斜杠不是续行符；单引号包 JSON 时内部双引号不要转义。

## deepagents 使用要点（踩坑成本高，改动前先看）

- 子 Agent 委派**只通过 `task` 工具**（参数 `description` + `subagent_type`）；
  子 Agent 名不是工具名，小模型极易调错 → 已在 4 个主 prompt +
  `agent/leaf_rules.py` 里双重约束。
- **不要再用 `StateBackend()`** 当 backend：它没有 `execute`，且文件系统是内存
  虚拟的（写了不落盘）。现在统一用 `agent/sandbox_backend.py` 的
  `RestrictedSandboxBackend`（真落盘 + 仅 Python 执行）。
- 也不要用官方 `LocalShellBackend`：`shell=True` 无隔离，能读 `.env` 密钥。
- `recursion_limit` 必须设（`AGENT_RECURSION_LIMIT`），否则小模型死循环
  能把一条请求跑到上千步、烧光额度。
