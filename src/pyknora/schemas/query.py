"""成员3检索接口的数据形状。"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    """知识库检索请求。"""

    model_config = ConfigDict(frozen=True)

    query: str
    top_k: int = Field(default=10, gt=0)


class RetrievedChunk(BaseModel):
    """融合后的命中片段；保留引用所需字段，不暴露某一路的原始分数。"""

    model_config = ConfigDict(frozen=True)

    chunk_id: UUID
    workspace_id: UUID
    kb_id: UUID
    document_id: UUID
    index_version: int
    chunk_ordinal: int
    content: str
    page_no: int | None = None
    heading_path: str | None = None
    locator_ref: str | None = None
    rrf_score: float
    vector_rank: int | None = None
    keyword_rank: int | None = None


class QueryResponse(BaseModel):
    """检索结果及其来源片段。"""

    model_config = ConfigDict(frozen=True)

    query: str
    results: list[RetrievedChunk]
