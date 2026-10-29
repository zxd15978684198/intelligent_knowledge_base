"""向量检索的最小服务编排。

这个阶段只负责把问题转成向量，再委托给带 Scope 的仓库检索活动 chunk。
关键词检索与 RRF 会在后续阶段加入。
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.schemas.document import ScoredChunk
from pyknora.schemas.query import RetrievedChunk
from pyknora.services.contracts.reads import ChunkSearchRepo
from pyknora.services.ingest.embedder import EmbeddingProvider
from pyknora.services.retrieval.rrf import reciprocal_rank_fusion


async def search_vector(
    scope: Scope,
    kb_id: UUID,
    query: str,
    *,
    embedder: EmbeddingProvider,
    repo: ChunkSearchRepo,
    top_k: int = 10,
) -> Sequence[ScoredChunk]:
    """将问题向量化，并检索当前 scope 与知识库可见的 top-k 片段。

    空问题或无效 top_k 在调用模型和数据库前拒绝。无命中是正常结果，返回空序列。
    """
    normalized_query = query.strip()
    if not normalized_query:
        raise AppError(ErrorCode.INVALID_REQUEST, "query must not be empty")
    if top_k < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, "top_k must be greater than zero")

    vectors = await embedder.embed([normalized_query])
    if len(vectors) != 1:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "embedding provider returned an unexpected number of vectors",
            details={"expected": 1, "actual": len(vectors)},
        )

    return await repo.search_similar(scope, kb_id, vectors[0], limit=top_k)


async def search_hybrid(
    scope: Scope,
    kb_id: UUID,
    query: str,
    *,
    embedder: EmbeddingProvider,
    repo: ChunkSearchRepo,
    top_k: int = 10,
    candidate_k: int | None = None,
    rrf_k: int = 60,
) -> list[RetrievedChunk]:
    """并行候选检索后用 RRF 融合；两路查询都由带 Scope 的仓库执行。"""
    normalized_query = query.strip()
    if not normalized_query:
        raise AppError(ErrorCode.INVALID_REQUEST, "query must not be empty")
    if top_k < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, "top_k must be greater than zero")

    candidate_limit = candidate_k if candidate_k is not None else top_k
    if candidate_limit < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, "candidate_k must be greater than zero")
    if rrf_k < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, "rrf_k must be greater than zero")

    vectors = await embedder.embed([normalized_query])
    if len(vectors) != 1:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "embedding provider returned an unexpected number of vectors",
            details={"expected": 1, "actual": len(vectors)},
        )

    vector_results = await repo.search_similar(scope, kb_id, vectors[0], limit=candidate_limit)
    keyword_results = await repo.search_keyword(
        scope, kb_id, normalized_query, limit=candidate_limit
    )
    return reciprocal_rank_fusion(
        vector_results,
        keyword_results,
        limit=top_k,
        k=rrf_k,
    )
