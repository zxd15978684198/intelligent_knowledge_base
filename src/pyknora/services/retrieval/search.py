"""检索编排：向量路、关键词路，以及两路的 RRF 融合。

一条贯穿全模块的原则：**降级必须可见**。本项目已经吃过一次"静默失效"的亏
（`tsvector` 把整段汉字当一个 token，FTS 那一路恒空且不报错，融合结果比纯向量
还差却无任何信号）。所以两路里任何一路失败时，另一路仍要出结果，但**失败的
事实必须随结果一起返回**（`HybridSearchResult.degraded`），不许当成正常结果。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.schemas.document import ActiveChunk, ScoredChunk
from pyknora.schemas.query import MAX_TOP_K, RetrievedChunk
from pyknora.services.contracts.reads import ChunkSearchRepo
from pyknora.services.ingest.embedder import EmbeddingProvider
from pyknora.services.retrieval.rrf import reciprocal_rank_fusion

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    """融合结果 + 降级实情。

    `degraded` 为空表示两路都成功。非空时键是失败的分支名（`"vector"` /
    `"keyword"`），值是该分支的错误码（`AppError` 取 `code`，其他异常取类名）。
    调用方——尤其是 HTTP 层——有义务把它透出去，否则就是又一次静默降级。
    """

    hits: list[RetrievedChunk]
    degraded: dict[str, str] = field(default_factory=dict)


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


async def _settle[T](awaitable: Awaitable[T]) -> tuple[T | None, BaseException | None]:
    """执行一个分支并把异常变成返回值，供并行 `gather` 用。

    直接 `gather(..., return_exceptions=True)` 也行，但返回值与异常要混在同一个
    列表里再按位置解包，类型和意图都不清楚；这里一次收敛成一个二元组。
    """
    try:
        return await awaitable, None
    except Exception as exc:  # noqa: BLE001 - 分支降级需要把异常转成"事实"
        return None, exc


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
) -> HybridSearchResult:
    """两路候选检索后用 RRF 融合；**单路失败时降级到另一路并在 `degraded` 里报告**。

    `keyword_repo` 传进来时两路**真并行**（`asyncio.gather`）：SQLAlchemy 的
    `AsyncSession` 不支持并发语句，所以并行版必须由调用方给两个绑定不同
    session 的仓库（见 `api/deps.py` 的 `get_keyword_search_repo`）。不传时退回
    顺序执行，结果与并行版一致——单元测试和脚本走这条，不用为了测一个函数的
    融合逻辑去开真连接。

    降级规则（本函数是唯一判定处）：

    - 单路抛出 `INVALID_REQUEST` → **不降级**，原样上抛。请求本身不合法时，
      另一路返回什么都是错的，降级会把"参数错了"伪装成"搜得少"。
    - 单路抛出其他异常 → 记 `degraded[分支] = 错误码` 并打 warning，另一路照用。
    - 两路都失败 → 抛出第一个错误（`AppError` 原样，其他折叠成 `INTERNAL_ERROR`），
      因为已经没有可用结果，返回空列表等于谎称"库里没有匹配"。
    """
    normalized_query = query.strip()
    if not normalized_query:
        raise AppError(ErrorCode.INVALID_REQUEST, "query must not be empty")
    _require_bounded_limit(top_k, name="top_k")

    candidate_limit = candidate_k if candidate_k is not None else top_k
    _require_bounded_limit(candidate_limit, name="candidate_k")
    if rrf_k < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, "rrf_k must be greater than zero")

    async def _vector_branch() -> Sequence[ScoredChunk]:
        # 复用 search_vector 而不是在这里再写一遍"embed → 校验维度 → 打库"：
        # 两条路必须对 embedding 的失败方式给出完全一致的处理。
        return await search_vector(
            scope, kb_id, normalized_query, embedder=embedder, repo=repo, top_k=candidate_limit
        )

    async def _keyword_branch() -> Sequence[ActiveChunk]:
        target = keyword_repo if keyword_repo is not None else repo
        return await target.search_keyword(scope, kb_id, normalized_query, limit=candidate_limit)

    if keyword_repo is None:
        vector_value, vector_error = await _settle(_vector_branch())
        keyword_value, keyword_error = await _settle(_keyword_branch())
    else:
        (vector_value, vector_error), (keyword_value, keyword_error) = await asyncio.gather(
            _settle(_vector_branch()),
            _settle(_keyword_branch()),
        )

    degraded: dict[str, str] = {}
    for branch, error in (("vector", vector_error), ("keyword", keyword_error)):
        if error is None:
            continue
        if isinstance(error, AppError) and error.code == ErrorCode.INVALID_REQUEST:
            raise error
        code = error.code if isinstance(error, AppError) else type(error).__name__
        degraded[branch] = code
        logger.warning("retrieval branch degraded: branch=%s code=%s error=%s", branch, code, error)

    if vector_value is None and keyword_value is None:
        first = vector_error or keyword_error
        if isinstance(first, AppError):
            raise first
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "all retrieval branches failed",
            details={"branches": degraded},
        ) from first

    hits = reciprocal_rank_fusion(
        vector_value or [],
        keyword_value or [],
        limit=top_k,
        k=rrf_k,
    )
    return HybridSearchResult(hits=hits, degraded=degraded)
