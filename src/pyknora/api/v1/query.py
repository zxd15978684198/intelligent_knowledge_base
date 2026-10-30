"""成员3：知识库检索 API。"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from pyknora.api.deps import (
    get_chunk_search_repo,
    get_keyword_search_repo,
    get_scope,
    require_kb_access,
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
    _kb: Annotated[Any, Depends(require_kb_access)],
) -> QueryResponse:
    """使用可信 Scope 检索并返回融合排名后的片段。

    `require_kb_access` 是"先判可见性、再谈检索结果"的那一步：别人的 kb_id
    一律 404（与文档、血缘路径同码），而不是 200 + 空 results——后者会让客户端
    以为"库是空的"，而真相是"你根本没权限，连它存不存在都不该知道"。
    """
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
    # `retrieval_strategy` 同理走实际值而不是回读配置：A/B 要按次归因，报告里
    # 每条样本都得带着它自己走的那条路。
    return QueryResponse(
        query=body.query,
        results=outcome.hits,
        degraded=outcome.degraded,
        retrieval_strategy=outcome.retrieval_strategy,
    )
