"""成员3：知识库检索 API。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from pyknora.api.deps import (
    get_chunk_search_repo,
    get_keyword_search_repo,
    get_scope,
    require_permission,
)
from pyknora.api.main import get_app_state
from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.db.repos.reads import SqlChunkSearchRepo
from pyknora.schemas.query import QueryRequest, QueryResponse
from pyknora.services.auth.permissions import QUERY_RUN
from pyknora.services.retrieval.search import search_hybrid

router = APIRouter(tags=["query"], dependencies=[Depends(require_permission(QUERY_RUN))])


@router.post(
    "/knowledge-bases/{kb_id}/query",
    response_model=QueryResponse,
    summary="检索知识库片段",
)
async def query_knowledge_base(
    kb_id: UUID,
    body: QueryRequest,
    scope: Annotated[Scope, Depends(get_scope)],
    repo: Annotated[SqlChunkSearchRepo, Depends(get_chunk_search_repo)],
    keyword_repo: Annotated[SqlChunkSearchRepo, Depends(get_keyword_search_repo)],
) -> QueryResponse:
    """使用可信 Scope 检索并返回融合排名后的片段。"""
    embedder = get_app_state().embedder
    if embedder is None:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "embedding provider not configured")

    outcome = await search_hybrid(
        scope,
        kb_id,
        body.query,
        embedder=embedder,
        repo=repo,
        keyword_repo=keyword_repo,
        top_k=body.top_k,
    )
    # `degraded` 必须原样透出：单路失败时结果依然返回，但客户端要能分辨
    # 「库里只有这些」和「一路挂了只剩一路」（见 schemas/query.py 的字段说明）。
    return QueryResponse(query=body.query, results=outcome.hits, degraded=outcome.degraded)
