"""文档与索引构建的数据形态（C2 的返回类型）。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ActiveDocument(BaseModel):
    """指针所指的那一版——不是最新写入的那一版。"""

    model_config = ConfigDict(frozen=True)

    document_id: UUID
    workspace_id: UUID
    kb_id: UUID
    active_document_version_id: UUID | None
    active_build_id: UUID | None
    active_index_version: int | None
    generation: int
    row_version: int
    created_at: datetime


class IndexBuild(BaseModel):
    model_config = ConfigDict(frozen=True)

    build_id: UUID
    workspace_id: UUID
    document_id: UUID
    index_version: int
    attempt_id: UUID
    embedding_model_key: str
    embedding_dim: int
    chunk_size: int
    chunk_overlap: int
    normalize: bool = True
    chunk_count: int
    state: str
    created_at: datetime
    activated_at: datetime | None = None
    retired_at: datetime | None = None


class ActiveChunk(BaseModel):
    """视图 v_active_chunks 的一行，**但刻意不含 `tsvector` 与 `embedding`**。

    视图共 21 列，本模型 19 个字段，差集正好是这两列。原因不是遗漏：

    - 向量是 512 个 float，一个文档 100 块就是 5 万个数字。用在"按文档取正文"
      这个场景上白白撑爆响应体，而调用方要的只是文字与位置。
    - 需要向量的场景只有相似度检索，那条路走 `ChunkSearchRepo.search_similar`，
      由它自己在 SQL 里用 `embedding`，返回值同样不含向量。

    所以成员3 不要写"逐列对应"的假设，也不要指望从这里拿到向量去自己算距离。
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: UUID
    workspace_id: UUID
    kb_id: UUID
    document_id: UUID
    build_id: UUID
    index_version: int
    attempt_id: UUID
    chunk_ordinal: int
    content: str
    page_no: int | None = None
    heading_path: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    token_count: int | None = None
    locator_ref: str | None = None
    embedding_model_key: str
    embedding_dim: int
    chunk_size: int
    active_document_version_id: UUID | None = None


class ScoredChunk(BaseModel):
    """相似度检索的一行。

    `distance` 是**余弦距离**，不是相似度：0 表示方向完全一致，2 表示完全相反。
    故意不叫 `score`——叫 score 就会有人当成"越大越相关"去排序或加阈值。
    要转成相似度用 `1 - distance`，那个减法请写在调用方，别藏在这一层。
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: UUID
    workspace_id: UUID
    kb_id: UUID
    document_id: UUID
    build_id: UUID
    index_version: int
    attempt_id: UUID
    chunk_ordinal: int
    content: str
    page_no: int | None = None
    heading_path: str | None = None
    locator_ref: str | None = None
    embedding_model_key: str
    embedding_dim: int
    active_document_version_id: UUID | None = None
    distance: float


class DocumentVersionInfo(BaseModel):
    """不可变版本快照的元数据，供成员5 的引用追溯使用。

    刻意**不含正文**：正文在 `document_blobs`（真实部署在对象存储），要正文请按
    `document_version_id` 走成员2 的读取路径。`object_key` 原样带出，是为了让引用
    能指回同一个存储对象，而不是在引用里复制一份内容。
    """

    model_config = ConfigDict(frozen=True)

    document_version_id: UUID
    workspace_id: UUID
    document_id: UUID
    content_hash: str
    byte_size: int
    object_key: str
    parse_status: str
    created_by_principal_id: UUID
    ingestion_source: str
    created_at: datetime
