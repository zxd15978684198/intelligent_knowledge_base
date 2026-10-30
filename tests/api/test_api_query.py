"""成员3的 /query API：鉴权、依赖缺失与检索结果响应。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import httpx
from fastapi import FastAPI
from sqlalchemy import text

from pyknora.api.deps import get_chunk_search_repo
from pyknora.api.main import (
    _lifespan,
    configure,
    create_app,
    get_app_state,
    reset_state,
)
from pyknora.core.scope import WORKSPACE_A, Scope
from pyknora.core.settings import settings
from pyknora.schemas.document import ActiveChunk, ScoredChunk
from pyknora.services.ingest.embedder import HashingEmbedder
from tests.api.tokens import KB_ID, bearer
from tests.integration.test_search_repo import _seed_one

DOCUMENT_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
BUILD_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
ATTEMPT_ID = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
CHUNK_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


class FakeSearchRepo:
    def __init__(self) -> None:
        self.scope: Scope | None = None
        self.query_vector: list[float] | None = None
        self.query_text: str | None = None

    async def search_similar(
        self,
        scope: Scope,
        kb_id: UUID,
        query_vector: Sequence[float],
        *,
        limit: int = 10,
        document_id: UUID | None = None,
    ) -> Sequence[ScoredChunk]:
        assert kb_id == KB_ID
        assert limit == 3
        assert document_id is None
        self.scope = scope
        self.query_vector = list(query_vector)
        return [_scored_chunk()]

    async def search_keyword(
        self, scope: Scope, kb_id: UUID, query_text: str, *, limit: int = 10
    ) -> Sequence[ActiveChunk]:
        assert kb_id == KB_ID
        assert limit == 3
        self.scope = scope
        self.query_text = query_text
        return []


def _scored_chunk() -> ScoredChunk:
    return ScoredChunk(
        chunk_id=CHUNK_ID,
        workspace_id=WORKSPACE_A,
        kb_id=KB_ID,
        document_id=DOCUMENT_ID,
        build_id=BUILD_ID,
        index_version=1,
        attempt_id=ATTEMPT_ID,
        chunk_ordinal=0,
        content="图书馆周末 9:00–18:00 开放。",
        page_no=2,
        locator_ref="docs/library.md#p2",
        embedding_model_key="test/hashing",
        embedding_dim=512,
        distance=0.1,
    )


def _client(repo: FakeSearchRepo) -> tuple[FastAPI, httpx.AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_chunk_search_repo] = lambda: repo
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    return app, httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_query_returns_fused_hits_and_uses_trusted_scope() -> None:
    repo = FakeSearchRepo()
    configure(embedder=HashingEmbedder(dim=512))
    _app, client = _client(repo)

    async with client:
        response = await client.post(
            f"/api/v1/knowledge-bases/{KB_ID}/query",
            headers=bearer(),
            json={"query": "  周末开放时间？  ", "top_k": 3},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query"] == "  周末开放时间？  "
    assert len(body["results"]) == 1
    assert body["results"][0]["chunk_id"] == str(CHUNK_ID)
    assert body["results"][0]["page_no"] == 2
    assert repo.scope == Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A)
    assert repo.query_text == "周末开放时间？"
    assert len(repo.query_vector or []) == 512


async def test_query_requires_authentication() -> None:
    repo = FakeSearchRepo()
    configure(embedder=HashingEmbedder(dim=512))
    _app, client = _client(repo)

    async with client:
        response = await client.post(
            f"/api/v1/knowledge-bases/{KB_ID}/query",
            json={"query": "问题"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert repo.query_vector is None


async def test_query_reports_missing_embedder_as_dependency_unavailable() -> None:
    repo = FakeSearchRepo()
    configure(embedder=None)
    _app, client = _client(repo)

    async with client:
        response = await client.post(
            f"/api/v1/knowledge-bases/{KB_ID}/query",
            headers=bearer(),
            json={"query": "问题"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert repo.query_vector is None


async def test_query_rejects_nonpositive_top_k() -> None:
    repo = FakeSearchRepo()
    configure(embedder=HashingEmbedder(dim=512))
    _app, client = _client(repo)

    async with client:
        response = await client.post(
            f"/api/v1/knowledge-bases/{KB_ID}/query",
            headers=bearer(),
            json={"query": "问题", "top_k": 0},
        )

    assert response.status_code == 422
    assert repo.query_vector is None


async def test_query_rejects_overlong_and_empty_query() -> None:
    """超长/空查询在 schema 层就被挡住，不落到 SQL（防 to_tsquery 递归爆栈）。"""
    repo = FakeSearchRepo()
    configure(embedder=HashingEmbedder(dim=512))
    _app, client = _client(repo)

    async with client:
        too_long = await client.post(
            f"/api/v1/knowledge-bases/{KB_ID}/query",
            headers=bearer(),
            json={"query": "报" * 1001},
        )
        empty = await client.post(
            f"/api/v1/knowledge-bases/{KB_ID}/query",
            headers=bearer(),
            json={"query": ""},
        )

    assert too_long.status_code == 422
    assert empty.status_code == 422
    # 两次都没有进到检索：向量路一次都没被调用。
    assert repo.query_vector is None


async def test_api_lifespan_configures_default_embedder_lazily() -> None:
    reset_state()

    async with _lifespan(FastAPI()):
        embedder = get_app_state().embedder
        assert embedder is not None
        assert embedder.model_key == settings.embedding_model_key


async def test_query_returns_hits_from_real_postgres(
    client: httpx.AsyncClient, clean_db: None, db_session
) -> None:
    scope = Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A)
    document_id = await _seed_one(
        scope=scope,
        kb_id=KB_ID,
        session=db_session,
        text="银狐锚点 检索测试",
    )
    await db_session.execute(
        text(
            "UPDATE chunks SET tsvector = to_tsvector('simple', pyknora_cjk_bigram(content)) "
            "WHERE workspace_id = :ws AND document_id = :doc"
        ),
        {"ws": WORKSPACE_A, "doc": document_id},
    )
    await db_session.commit()

    response = await client.post(
        f"/api/v1/knowledge-bases/{KB_ID}/query",
        headers=bearer(),
        json={"query": "银狐锚点", "top_k": 5},
    )

    assert response.status_code == 200, response.text
    hits = response.json()["results"]
    assert len(hits) == 1
    assert hits[0]["document_id"] == str(document_id)
    assert hits[0]["content"] == "银狐锚点 检索测试"
    assert hits[0]["vector_rank"] == 1
    assert hits[0]["keyword_rank"] == 1
