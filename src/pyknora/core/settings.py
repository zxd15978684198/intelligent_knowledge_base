"""应用配置。所有可调参数单点定义，不在代码里散落魔法数字。

这里还有两条**安全护栏**，它们不是配置项，而是"配置的合法性检查"：

1. `jwt_secret`：非 dev/test/local 环境下必须显式覆盖，且不能等于开发默认值。
   默认密钥一旦生效，任何人都能自行签发任意 `sub` / `default_workspace_id` 的令牌，
   前面所有权限校验（Scope 只信 JWT、404 不泄漏、RLS 兜底）在同一刻全部归零。
2. `env`：决定上面那条护栏是否生效，也决定启动时是否检查数据库连接角色
   （见 `api/main.py` 的 `_warn_if_db_role_bypasses_rls`）。
"""

from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 开发默认密钥集中定义一次：settings 的护栏与 jwt.py 的兜底都引用这里，
# 避免"两处各写一个字符串、改了一处忘了另一处"。
DEV_JWT_SECRET = "dev-only-insecure-secret"
DEV_CURSOR_SECRET = "dev-only-insecure-cursor-secret"

# 这几个环境下允许使用开发默认密钥；其余（staging/prod/...）一律要求显式配置。
_INSECURE_ENVS = frozenset({"dev", "test", "local"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PYKNORA_", env_file=".env", extra="ignore")

    # 运行环境：dev | test | local | staging | prod。只参与安全护栏，不影响业务分支。
    env: str = "dev"

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

    # Chat 生成：OpenAI 兼容的流式 Chat Completions 接口。
    chat_base_url: str = "https://api.openai.com/v1"
    chat_api_key: str = ""
    chat_model: str = "gpt-4o-mini"
    chat_timeout_s: float = 60.0
    chat_max_context_chunks: int = 6

    # 切片
    chunk_size: int = 512
    chunk_overlap: int = 64

    # 上传
    max_upload_bytes: int = 50 * 1024 * 1024

    # 队列
    lease_seconds: int = 90
    max_attempts: int = 3

    # 认证：默认留空表示"未配置"，由 `jwt.py` 退到 DEV_JWT_SECRET；
    # 非开发环境退到默认值会在构造时直接失败，而不是启动后默默接受伪造令牌。
    jwt_secret: str = ""

    # 游标：签名防篡改，默认 24 小时失效。生产必须覆盖开发密钥。
    cursor_secret: str = DEV_CURSOR_SECRET
    cursor_ttl_seconds: int = 24 * 60 * 60

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> Settings:
        """非开发环境禁止沿用开发默认密钥。

        做成"构造即失败"而不是"启动告警"：告警会被忽略，失败不会。伪造令牌的成本
        仅仅是知道这个默认字符串——而它就写在仓库里。
        """
        if self.env.strip().lower() in _INSECURE_ENVS:
            return self
        if not self.jwt_secret or self.jwt_secret == DEV_JWT_SECRET:
            raise ValueError(
                f"PYKNORA_JWT_SECRET 在 PYKNORA_ENV={self.env} 时必须显式设置，"
                f"且不能等于开发默认值 {DEV_JWT_SECRET!r}"
            )
        if self.cursor_secret == DEV_CURSOR_SECRET:
            raise ValueError(
                f"PYKNORA_CURSOR_SECRET 在 PYKNORA_ENV={self.env} 时必须显式设置，"
                "否则分页游标可被伪造（危害低于 JWT，但不该带上生产）"
            )
        return self


settings = Settings()
