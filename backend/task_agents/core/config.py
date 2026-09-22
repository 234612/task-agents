"""
应用配置管理模块
使用 Pydantic BaseSettings 从环境变量和 .env 文件加载配置
配置分为四层：LLM / MySQL / MongoDB / Redis，外加应用运行参数。

分层存储职责：
- MySQL   会话元数据（列表页：排序、分页、筛选）
- MongoDB 对话历史详情（详情页：messages 数组，高频追加写）
- Redis   当前会话上下文（短期记忆：为 Prompt 注入提供低延迟读取，TTL 自动过期）
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（backend/），锚定 .env 位置，避免依赖启动时的 cwd
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用全局配置"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ==================== LLM 配置 ====================
    # 通义千问 / OpenAI 兼容接口配置
    LLM_MODEL: str = "qwen3-vl-flash"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_TEMPERATURE: float = 0.7

    # 单次请求内 LangGraph 允许的最大步数（防失控）。
    # 小模型偶尔会陷入「重复委派 / 反复重试失败工具」的死循环，一条请求能跑
    # 上千步、烧掉几十万 token。设上限后可被 GraphRecursionError 截断，
    # 由 chat_service 转成 error 事件返回，而不是无限跑下去。
    AGENT_RECURSION_LIMIT: int = 60



    # ==================== MySQL 配置 ====================
    # 用于存储会话元数据（会话列表、用户隔离）
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "task_agents"

    # ==================== MongoDB 配置 ====================
    # 用于存储对话历史详情（messages 数组，高频追加写）
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DATABASE: str = "task_agents"
    MONGO_SESSION_COLLECTION: str = "chat_sessions"

    # ==================== Redis 配置 ====================
    # 用于存储当前会话上下文（短期记忆，为 LLM Prompt 注入提供低延迟读取）
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # 上下文窗口：保留最近 N 轮对话（1 轮 = user + assistant 各一条消息）
    # 3 轮 = 6 条消息，控制 Prompt 注入的 Token 规模
    REDIS_CONTEXT_MAX_TURNS: int = 3
    # 上下文 TTL（秒），默认 1 小时；非活跃会话自动过期，与 checkpointer
    # 的 1 小时 TTL 对齐，两层短期记忆同生同灭
    REDIS_CONTEXT_TTL_SECONDS: int = 3600

    # LangGraph checkpointer（短期记忆）后端：auto / redis / memory
    # - auto（默认）：探测 Redis 是否装了 RedisJSON；有则用 Redis，无则回落内存
    # - redis：强制用 Redis（要求 Redis Stack / Redis 8，含 RedisJSON + RediSearch）
    # - memory：进程内内存，服务重启后 thread 状态丢失（业务侧 Redis 上下文
    #   仍在，冷启动由 _seed_context 回灌，只是 LangGraph 侧的 state 重建）
    CHECKPOINT_BACKEND: str = "auto"

    # 会话标题：取首句前 N 字（标题截断长度）
    SESSION_TITLE_MAX_CHARS: int = 50

    # ==================== 应用配置 ====================
    # FastAPI 服务配置
    APP_DEBUG: bool = False
    CORS_ORIGINS: str = "*"  # 逗号分隔的允许来源，* 表示全部允许

    # 会话列表分页默认值与上限
    SESSION_PAGE_SIZE: int = 20
    SESSION_PAGE_SIZE_MAX: int = 100

    # ==================== 程序员工具配置 ====================
    # 程序员子 Agent 的文件读写与代码执行能力，全部受沙箱约束。
    # 空字符串表示使用默认值：工作区默认为项目下的 workspace/ 目录。
    CODER_WORKSPACE_DIR: str = ""

    # 代码执行超时（秒）。必须设上限，否则 Agent 写出死循环会挂住整个请求
    CODER_EXEC_TIMEOUT_SECONDS: int = 15

    # 单次工具输出的最大字符数。防止 Agent 打印海量内容撑爆上下文与 Token
    CODER_MAX_OUTPUT_CHARS: int = 8000

    # 单文件读写上限（字节）。既是安全防护，也避免误读大文件
    CODER_MAX_FILE_BYTES: int = 2_000_000

    # ==================== 计算属性 ====================
    @property
    def mysql_dsn(self) -> str:
        """构建 MySQL 异步连接字符串"""
        return (
            f"mysql+aiomysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
            "?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        """构建 Redis 连接字符串

        未配置密码时省略认证段，避免生成 redis://:@host 这类非法形式。
        """
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def cors_origins_list(self) -> list[str]:
        """解析 CORS 来源为列表"""
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    @property
    def coder_workspace_dir(self) -> Path:
        """程序员子 Agent 的工作区根目录

        未配置 CODER_WORKSPACE_DIR 时，默认落在项目根目录下的 workspace/，
        与源代码目录隔离，避免 Agent 误改工程文件。

        返回已展开为绝对路径的 Path；目录不存在时由调用方负责创建，
        这里刻意不产生副作用，保证读取配置是纯函数。
        """
        raw = (self.CODER_WORKSPACE_DIR or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return (PROJECT_ROOT / "workspace").resolve()


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例（缓存）"""
    return Settings()
