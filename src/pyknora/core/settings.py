"""应用配置。所有可调参数单点定义，不在代码里散落魔法数字。"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PYKNORA_", env_file=".env", extra="ignore")

    # 数据库（本地 docker 容器映射到 5433）
    database_url: str = "postgresql+asyncpg://postgres:dev@127.0.0.1:5433/pyknora"
    db_container: str = "pyknora-pg"

    # Embedding：本地 HF 优先，可回落 OpenAI 兼容 HTTP
    embedding_backend: str = "local"  # local | openai
    embedding_model_key: str = "BAAI/bge-small-zh-v1.5"
    embedding_dim: int = 512
    embedding_batch_size: int = 8
    embedding_timeout_s: float = 30.0
    huggingface_endpoint: str = "https://huggingface.co"
    # 官方源不可达时回落的国内镜像
    huggingface_mirror: str = "https://hf-mirror.com"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"

    # 切片
    chunk_size: int = 512
    chunk_overlap: int = 64

    # 上传
    max_upload_bytes: int = 50 * 1024 * 1024

    # 队列
    lease_seconds: int = 90
    max_attempts: int = 3

    # 游标：签名防篡改，默认 24 小时失效。生产必须覆盖开发密钥。
    cursor_secret: str = "dev-only-insecure-cursor-secret"
    cursor_ttl_seconds: int = 24 * 60 * 60


settings = Settings()
