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

项目使用 Docker 运行 MySQL 和 MongoDB。

```bash
cd backend
docker compose up -d
```

等待容器启动完成（约 10-30 秒），可以查看状态：

```bash
docker compose ps
```

应该看到两个容器状态为 `running`：
- `task-agents-mysql` (端口 3306)
- `task-agents-mongodb` (端口 27017)

### 数据库配置说明

- **MySQL 8.0**: 使用 `utf8mb4` 字符集 + `utf8mb4_unicode_ci` 排序规则，完整支持 Unicode 字符（包括 Emoji）
- **MongoDB**: 默认 UTF-8 编码，无需额外配置
- **数据持久化**: 使用 Docker volumes，重启容器数据不会丢失

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

# 应用配置
APP_DEBUG=false
CORS_ORIGINS=*
```

### 2.3 启动后端服务

```bash
uv run uvicorn src.main:app --reload --port 8000
```

首次启动时，系统会自动：
- 创建 MySQL 数据库表结构
- 初始化 MongoDB 连接
- 构建 Agent 实例

看到以下日志表示启动成功：
```
🚀 正在构建 Agent 实例...
✅ Agent 构建完成，已挂载到 app.state。已注册: ['market_researcher']
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 2.4 验证后端

打开浏览器访问以下接口：

- **健康检查**: http://localhost:8000/health
  ```json
  {
    "status": "ok",
    "llm": {
      "model": "qwen-plus",
      "provider": "阿里云通义千问"
    }
  }
  ```

- **API 文档**: http://localhost:8000/docs
  可以查看所有可用的 API 接口并进行测试。

- **会话列表**: http://localhost:8000/sessions?user_id=userid_1
  首次访问应该返回空列表 `[]`

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

**现象**: 后端启动时报错 `Connection refused` 或 `MongoNetworkError`

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
- 如果端口被占用: 修改 `.env` 中的 `MONGO_PORT` 为其他端口（如 27018）

### 3. LLM API 调用失败

**现象**: 发送消息后长时间无响应，或返回错误

**排查步骤**:
- 检查 `.env` 中的 `LLM_API_KEY` 是否正确
- 访问 https://localhost:8000/health/llm/verify 测试 LLM 连通性
- 检查网络连接是否正常

**解决方案**:
- 确认 API Key 有效且有足够额度
- 如果使用代理，确保环境变量 `HTTPS_PROXY` 已设置

### 4. 前端无法连接后端

**现象**: 前端页面加载正常，但发送消息时显示"请求失败"

**排查步骤**:
- 检查后端是否在运行: 访问 http://localhost:8000/health
- 检查浏览器控制台是否有 CORS 错误
- 确认 `.env` 中的 `CORS_ORIGINS` 包含前端地址

**解决方案**:
- 确保后端服务正在运行
- 如果前端运行在其他端口，更新 `.env` 中的 `CORS_ORIGINS`

### 5. 数据库字符集问题

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

---

## 下一步

项目启动成功后，可以：

1. **创建会话**: 点击左侧"新建对话"按钮
2. **发送消息**: 在输入框输入内容并发送
3. **查看历史**: 左侧边栏显示所有会话记录
4. **测试持久化**: 重启后端服务，验证会话和消息仍然存在

---

## 技术支持

如有问题，请检查：
- Docker Desktop 是否正常运行
- 端口 3306、27017、8000 是否被占用
- `.env` 配置文件是否正确
- 网络连接是否正常
