"""
应用配置管理模块
使用 Pydantic BaseSettings 从环境变量和 .env 文件加载配置
配置分为三层：LLM / 数据库 / 应用
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
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
    # 用于存储对话 checkpoint（LangGraph 状态持久化）
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DATABASE: str = "task_agents"

    # ==================== 应用配置 ====================
    # FastAPI 服务配置
    APP_DEBUG: bool = False
    CORS_ORIGINS: str = "*"  # 逗号分隔的允许来源，* 表示全部允许

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
    def cors_origins_list(self) -> list[str]:
        """解析 CORS 来源为列表"""
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例（缓存）"""
    return Settings()
