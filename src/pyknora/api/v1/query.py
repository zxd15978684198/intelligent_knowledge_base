"""成员3：知识库检索 API。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from pyknora.api.deps import get_chunk_search_repo, get_scope
from pyknora.api.main import get_app_state
from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.db.repos.reads import SqlChunkSearchRepo
from pyknora.schemas.query import QueryRequest, QueryResponse
from pyknora.services.retrieval.search import search_hybrid

router = APIRouter(tags=["query"])


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
) -> QueryResponse:
    """使用可信 Scope 检索并返回融合排名后的片段。"""
    embedder = get_app_state().embedder
    if embedder is None:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "embedding provider not configured")

    results = await search_hybrid(
        scope,
        kb_id,
        body.query,
        embedder=embedder,
        repo=repo,
        top_k=body.top_k,
    )
    return QueryResponse(query=body.query, results=list(results))
