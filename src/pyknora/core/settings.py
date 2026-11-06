"""应用配置。所有可调参数单点定义，不在代码里散落魔法数字。

这里还有两条**安全护栏**，它们不是配置项，而是"配置的合法性检查"：

1. `jwt_secret`：非 dev/test/local 环境下必须显式覆盖，且不能等于开发默认值。
   默认密钥一旦生效，任何人都能自行签发任意 `sub` / `default_workspace_id` 的令牌，
   前面所有权限校验（Scope 只信 JWT、404 不泄漏、RLS 兜底）在同一刻全部归零。
2. `env`：决定上面那条护栏是否生效，也决定启动时是否检查数据库连接角色
   （见 `api/main.py` 的 `_warn_if_db_role_bypasses_rls`）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 开发默认密钥集中定义一次：settings 的护栏与 jwt.py 的兜底都引用这里，
# 避免"两处各写一个字符串、改了一处忘了另一处"。
DEV_JWT_SECRET = "dev-only-insecure-secret"
DEV_CURSOR_SECRET = "dev-only-insecure-cursor-secret"

# 检索策略的取值集中定义一次：`Settings.retrieval_strategy` 的字段类型与
# `services/retrieval/search.py` 的运行时校验都引用这两个常量，否则"配置里写
# baseline、服务里再写一遍字符串"迟早有一处改漏，而漏了只会表现为策略静默不生效。
RETRIEVAL_STRATEGY_BASELINE = "baseline"
RETRIEVAL_STRATEGY_CANDIDATE = "candidate"

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
    # 部分网关（实测 opencode.ai）要求请求带 `x-opencode-session`，否则回
    # 400 `MissingSessionID`；标准 OpenAI 会忽略未知头，所以默认为空、不发送。
    #
    # 为什么做成配置而不是代码里写死常量：这个头是**部署方**的约定，换一个网关
    # 就不存在；写死会让别的部署凭空多出一个无意义的头。
    chat_session_id: str = ""

    # 视觉理解（多模态）：**一律走外部模型接口，不做本地部署**。
    #
    # 为什么和 chat_* 分开而不是复用：视觉调用常换更贵的模型、可能走另一家网关、
    # 也可能要单独计量；但部署方只想配一套时不该被逼着填两遍——三项留空即回落到
    # chat_* 的对应值（见 `resolve_vision`），于是"不配也能跑"与"能单独换"同时成立。
    # 三者都不配且 chat_api_key 也为空时，视觉通道应整体跳过（不发空令牌）。
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = ""
    # 单张图的调用上限。不用 chat_timeout_s（60s）：视觉比文本慢一个量级，一页吃满
    # 60s 就把整份文档的预算耗光了；20s 与 OCR 的页预算对齐（ADR-0008 D10-8）。
    vision_page_timeout_s: float = 20.0

    # 一次问答的运行预算（计划 §7.3 / T5.1，实现在 services/rag/budget.py）。
    #
    # 这两个值是计划 §7.3 给出的**教学建议值，不是压测结论**：口径要求
    # "timeout = max(3 × P95, 10s) 且 ≤ chat_timeout_s"，但本项目还没测出 P95，
    # 所以先用 chat_timeout_s × 0.4 = 24s 的保守替代，再取整成 25s 留一点余量——
    # 它必须小于 chat_timeout_s（60s），否则上游的 60s 超时会先触发，预算形同虚设。
    # 拿到 W1 的 P95 后按上式重算，并把实测值写进文档；在那之前任何"够用/不够用"
    # 的结论都缺依据。
    chat_budget_timeout_s: float = 25.0
    # 当前一次问答只调用一次 search_hybrid，所以 2 是**预留**（给"门控判不足时补搜"
    # 留位），不是当前会触发的上限。设 1 会退化成"只要用了这一次检索就算超限"，
    # 语义反而错；设 3 以上则等于放弃上限。
    chat_max_retrieval_calls: int = 2

    # 检索策略：A/B 的**唯一**切换点（PYKNORA_RETRIEVAL_STRATEGY）。
    #
    # `baseline` 是改造前的线上行为，`candidate` 是待对照的候选实现（本轮只放大
    # 候选池，其余参数不动）。默认必须留在 baseline：开关的价值在于"随时能切回
    # 已验证的那条路"，若默认走 candidate，一次回归就是拿未验证路径当线上。
    # 切换只改环境变量并重启，不改代码——这正是它作为对照实验前提的意义。
    #
    # 非法值**启动即失败**（`Literal` 校验），不静默退化成 baseline：退化会让运维
    # 以为 A/B 在跑 candidate，实际两臂都是 baseline，整轮评测到看报告时才发现作废。
    # 服务层 `_resolve_candidate_k` 对脚本/评测的按次传参做同样的拒绝（见 search.py）。
    retrieval_strategy: Literal["baseline", "candidate"] = RETRIEVAL_STRATEGY_BASELINE

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

    # 浏览器会话：刷新串的绝对有效期（秒，默认 7 天）。轮换时原样复制
    # expires_at，绝不滑动延长——否则偷到一次刷新串就能无限续期。
    refresh_ttl_seconds: int = 7 * 24 * 60 * 60

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

    @property
    def cookie_secure(self) -> bool:
        """会话 Cookie 是否强制 Secure。

        dev/test/local 走 http://127.0.0.1，强开 Secure 浏览器不会回传，
        表现是「登录成功但下一个请求仍 401」，所以按环境判定。
        """
        return self.env.strip().lower() not in _INSECURE_ENVS

    def resolve_vision(self) -> tuple[str, str, str]:
        """返回视觉通道的 `(base_url, api_key, model)`，空串项逐个回落到 `chat_*`。

        回落是**逐项**的，不是"三项要么全配要么全不配"：网关地址与 key 常是同一套，
        只有模型名会换成更贵的那个——逐项回落正好服务这个最常见的情况。
        """
        return (
            self.vision_base_url or self.chat_base_url,
            self.vision_api_key or self.chat_api_key,
            self.vision_model or self.chat_model,
        )

    @property
    def vision_configured(self) -> bool:
        """有 API key 才算配置好。

        没有 key 时调用方必须**跳过**视觉通道并标注降级原因，而不是发一个空令牌上去
        换 401——那种失败看起来像网络故障，实际是没配置，会把排障引向错误的方向。
        """
        return bool(self.resolve_vision()[1])


settings = Settings()
