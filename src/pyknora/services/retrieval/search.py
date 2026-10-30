"""向量检索的最小服务编排。

这个阶段只负责把问题转成向量，再委托给带 Scope 的仓库检索活动 chunk。
关键词检索与 RRF 会在后续阶段加入。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.schemas.document import ScoredChunk
from pyknora.schemas.query import MAX_TOP_K, RetrievedChunk
from pyknora.services.contracts.reads import ChunkSearchRepo
from pyknora.services.ingest.embedder import EmbeddingProvider
from pyknora.services.retrieval.rrf import reciprocal_rank_fusion


def _require_bounded_limit(value: int, *, name: str) -> None:
    """校验"条数"类参数落在 `[1, MAX_TOP_K]`。

    服务层也要查上限，不只是 schema 层：脚本、worker、内部调用都绕过 HTTP，
    而 `limit` 会直接进 SQL 的 `LIMIT`。两层不是重复校验，是两个入口。
    """
    if value < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, f"{name} must be greater than zero")
    if value > MAX_TOP_K:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must not exceed {MAX_TOP_K}",
            details={"max": MAX_TOP_K, "actual": value},
        )


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
    _require_bounded_limit(top_k, name="top_k")

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
    keyword_repo: ChunkSearchRepo | None = None,
    top_k: int = 10,
    candidate_k: int | None = None,
    rrf_k: int = 60,
) -> list[RetrievedChunk]:
    """两路候选检索后用 RRF 融合；两路查询都由带 Scope 的仓库执行。

    `keyword_repo` 传进来时两路**真并行**（`asyncio.gather`）：SQLAlchemy 的
    `AsyncSession` 不支持并发语句，所以并行版必须由调用方给两个绑定不同
    session 的仓库（见 `api/deps.py` 的 `get_keyword_search_repo`）。不传时退回
    顺序执行，结果与并行版一致——单元测试和脚本走这条，不用为了测一个函数的
    融合逻辑去开真连接。
    """
    normalized_query = query.strip()
    if not normalized_query:
        raise AppError(ErrorCode.INVALID_REQUEST, "query must not be empty")
    _require_bounded_limit(top_k, name="top_k")

    candidate_limit = candidate_k if candidate_k is not None else top_k
    _require_bounded_limit(candidate_limit, name="candidate_k")
    if rrf_k < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, "rrf_k must be greater than zero")

    vectors = await embedder.embed([normalized_query])
    if len(vectors) != 1:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "embedding provider returned an unexpected number of vectors",
            details={"expected": 1, "actual": len(vectors)},
        )

    if keyword_repo is None:
        vector_results = await repo.search_similar(scope, kb_id, vectors[0], limit=candidate_limit)
        keyword_results = await repo.search_keyword(
            scope, kb_id, normalized_query, limit=candidate_limit
        )
    else:
        vector_results, keyword_results = await asyncio.gather(
            repo.search_similar(scope, kb_id, vectors[0], limit=candidate_limit),
            keyword_repo.search_keyword(scope, kb_id, normalized_query, limit=candidate_limit),
        )
    return reciprocal_rank_fusion(
        vector_results,
        keyword_results,
        limit=top_k,
        k=rrf_k,
    )
