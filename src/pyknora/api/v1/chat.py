"""成员3：SSE 流式 RAG 问答接口。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from pyknora.api.deps import (
    get_chunk_search_repo,
    get_keyword_search_repo,
    get_scope,
    require_permission,
)
from pyknora.api.main import get_app_state
from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.core.settings import settings
from pyknora.db.repos.reads import SqlChunkSearchRepo
from pyknora.schemas.chat import (
    ChatRequest,
    Citation,
    DoneEvent,
    RetrievalEvent,
    StreamErrorEvent,
    TokenEvent,
)
from pyknora.services.auth.permissions import CHAT_RUN
from pyknora.services.rag.answer import stream_answer
from pyknora.services.retrieval.search import search_hybrid

router = APIRouter(tags=["chat"], dependencies=[Depends(require_permission(CHAT_RUN))])

logger = logging.getLogger(__name__)


def _sse(event: str, payload: object) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post(
    "/knowledge-bases/{kb_id}/chat",
    response_class=StreamingResponse,
    summary="流式 RAG 问答",
)
async def chat(
    body: ChatRequest,
    scope: Annotated[Scope, Depends(get_scope)],
    kb_id: UUID,
    repo: Annotated[SqlChunkSearchRepo, Depends(get_chunk_search_repo)],
    keyword_repo: Annotated[SqlChunkSearchRepo, Depends(get_keyword_search_repo)],
) -> StreamingResponse:
    """SSE 顺序固定为 retrieval、token*、done；检索沿用可信 Scope。"""
    state = get_app_state()
    if state.embedder is None:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "embedding provider not configured")
    # 必须在构造 StreamingResponse **之前**校验：流一旦开始，状态码已经发出去，
    # 再抛错只能表现为连接被掐断，客户端会把"没配模型"误读成网络故障。
    # 真正的检查在 services/rag/answer.py 的生成器里（那里是最后一道），
    # 这里是把错误提前成可读的 503。
    if not settings.chat_api_key:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "chat model is not configured")
    outcome = await search_hybrid(
        scope, kb_id, body.query, embedder=state.embedder, repo=repo, top_k=body.top_k
    )
    chunks = outcome.hits
    if outcome.degraded:
        # 检索降级后仍会作答，但这必须让客户端知道：只靠一路召回时答案的
        # 覆盖面明显更窄，用户据此下结论前应当看得见（见 schemas/query.py）。
        logger.warning("chat retrieval degraded: %s", outcome.degraded)
    citations = [
        Citation(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            page_no=chunk.page_no,
            snippet=chunk.content[:500],
        ).model_dump(mode="json")
        for chunk in chunks
    ]

    async def events() -> AsyncIterator[str]:
        yield _sse("retrieval", RetrievalEvent(citations=citations).model_dump(mode="json"))
        async for kind, value in stream_answer(body.query, chunks, settings=settings):
            if kind == "token":
                yield _sse("token", TokenEvent(text=value).model_dump())
            elif kind == "error":
                # 流已开始，状态码改不了：错误作为事件发出去，再补一个 done 收尾，
                # 前端的解析器才不会一直挂着等下一块。
                yield _sse("error", StreamErrorEvent(**value).model_dump())
                yield _sse("done", DoneEvent().model_dump())
                return
            else:
                yield _sse("done", DoneEvent(**value).model_dump())

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
