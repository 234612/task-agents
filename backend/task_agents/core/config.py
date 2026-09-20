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
    LLM_MODEL: str = "qwen3.8-max"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_TEMPERATURE: float = 0.7

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
    REDIS_CONTEXT_MAX_TURNS: int = 10
    # 上下文 TTL（秒），默认 7 天；到期自动过期，无需手动清理
    REDIS_CONTEXT_TTL_SECONDS: int = 604800

    # ==================== 应用配置 ====================
    # FastAPI 服务配置
    APP_DEBUG: bool = False
    CORS_ORIGINS: str = "*"  # 逗号分隔的允许来源，* 表示全部允许

    # 会话列表分页默认值与上限
    SESSION_PAGE_SIZE: int = 20
    SESSION_PAGE_SIZE_MAX: int = 100

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


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例（缓存）"""
    return Settings()
