"""成员3检索接口的数据形状。"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    """知识库检索请求。"""

    model_config = ConfigDict(frozen=True)

    query: str = Field(
        min_length=1,
        max_length=1000,
        description=(
            "用户问题。上限 1000 字符是安全边界，不是产品限制："
            "关键词路会把汉字切成 bigram 送进 to_tsquery，"
            "而 to_tsquery 是递归下降解析，词元越多递归越深——"
            "实测 20000 字直接 stack depth limit exceeded（500）。"
            "仓库层另有 256 字符的二次封顶（src/pyknora/db/repos/reads.py），"
            "这里放宽到 1000 是为了不砍掉正常的长问句。"
            "空串同样拒绝（min_length=1）：空问题会走到 embedding 但语义为空，"
            "返回一堆无关片段比返回 422 更糟。"
        ),
    )
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
