"""知识库读取契约的数据形态（C1 的返回类型）。

成员1 拥有 knowledge_bases 表；成员2 只消费 EmbeddingProfile，不直连该表。
字段与 PRD:432 的差距见 docs/plans/成员2执行计划.md §9.3。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EmbeddingProfile(BaseModel):
    """创建一个 KB 时固化、之后只读的向量化与切片参数。

    这些值必须随 KB 一起固定，否则重建索引时无法判断旧索引用什么参数建的。
    """

    model_config = ConfigDict(frozen=True)

    embedding_model_key: str = Field(description="如 BAAI/bge-small-zh-v1.5")
    embedding_dim: int = Field(gt=0, description="必须与 chunks.embedding 的维度一致")
    chunk_size: int = Field(gt=0)
    chunk_overlap: int = Field(ge=0)
    normalize: bool = True


class KnowledgeBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    kb_id: UUID
    workspace_id: UUID
    name: str
    profile: EmbeddingProfile
    active_index_build_id: UUID | None = None
    row_version: int = Field(gt=0)
    generation: int = Field(gt=0)
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeBasePage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[KnowledgeBase]
    next_cursor: str | None = None
    limit: int = Field(ge=1, le=100)
