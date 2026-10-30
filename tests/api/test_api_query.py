"""成员3的 /query API：鉴权、依赖缺失与检索结果响应。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import text

from pyknora.api.deps import get_chunk_search_repo, get_kb_repo, get_keyword_search_repo
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
from pyknora.schemas.query import MAX_TOP_K, QueryRequest
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
        # 请求 top_k=3，但两路各取 `top_k * 2 = 6` 条候选再融合（候选池必须比返回宽，
        # 否则 RRF 只能重排它看到的 top_k 条，混合检索等于白做）。
        assert limit == 6
        assert document_id is None
        self.scope = scope
        self.query_vector = list(query_vector)
        return [_scored_chunk()]

    async def search_keyword(
        self, scope: Scope, kb_id: UUID, query_text: str, *, limit: int = 10
    ) -> Sequence[ActiveChunk]:
        assert kb_id == KB_ID
        assert limit == 6
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


class _KbRepoGrantingA:
    """只回答"KB_ID 属于 A 区且未删"，其余一律 None。

    本文件测的是检索编排，不是可见性规则；但 `/query` 现在会先做一次
    `require_kb_access`。若这里不 override，依赖会拿到进程级状态里的 `None`
    并回 503，用例就会因为"没配仓库"而不是"检索结果不对"失败。
    """

    async def get(self, scope: Scope, kb_id: UUID) -> object | None:
        if kb_id == KB_ID and scope.workspace_id == WORKSPACE_A:
            return object()
        return None


def _client(repo: FakeSearchRepo) -> tuple[FastAPI, httpx.AsyncClient]:
    app = create_app()
    # 两路检索现在各有一个 session 依赖：只 override 第一条的话，关键词那一路
    # 会真的去连库并返回空集，融合结果就和用例声明的 fake 数据对不上。
    app.dependency_overrides[get_chunk_search_repo] = lambda: repo
    app.dependency_overrides[get_keyword_search_repo] = lambda: repo
    app.dependency_overrides[get_kb_repo] = _KbRepoGrantingA
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


async def test_query_on_foreign_or_missing_kb_is_404() -> None:
    """别人的 / 不存在的 kb_id 一律 404，不能回 200 + 空 results。

    回归：此前 `/query` 不做可见性判定，直接带着 Scope 去检索，跨租户 kb_id 命中
    0 行 → 200 `{"results": []}`。同一件事在文档路径是 404，两处口径相反时，
    客户端会把"无权"误读成"库是空的"。
    """
    repo = FakeSearchRepo()
    configure(embedder=HashingEmbedder(dim=512))
    _app, client = _client(repo)
    foreign_kb = UUID("99999999-9999-9999-9999-999999999999")

    async with client:
        response = await client.post(
            f"/api/v1/knowledge-bases/{foreign_kb}/query",
            headers=bearer(),
            json={"query": "问题"},
        )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert repo.query_vector is None, "可见性判定必须在打检索之前"


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


async def test_query_rejects_top_k_above_max() -> None:
    """`top_k` 超上限是 422，不是被静默截断。

    `top_k` 会直接进向量路与关键词路两条 SQL 的 `LIMIT`，还决定响应体大小；
    静默截断会让调用方以为"库里只有 50 条"，拿错结论比报错代价高。
    """
    repo = FakeSearchRepo()
    configure(embedder=HashingEmbedder(dim=512))
    _app, client = _client(repo)

    async with client:
        response = await client.post(
            f"/api/v1/knowledge-bases/{KB_ID}/query",
            headers=bearer(),
            json={"query": "问题", "top_k": MAX_TOP_K + 1},
        )

    assert response.status_code == 422
    assert repo.query_vector is None, "校验必须在打库之前"


def test_query_accepts_top_k_at_exactly_max() -> None:
    """边界值本身要放行（不放行就等于上限只有 49）。

    这里只验 schema：用 `QueryRequest` 直接构造，不经过检索链路——
    本文件的 `FakeSearchRepo` 把候选条数写死成 6（`top_k * 2`），走 HTTP 时
    `top_k=50` 会变成候选 50（封顶）并让"上限放行"这件事被夹具的硬期望干扰成假失败。
    """
    assert QueryRequest(query="问题", top_k=MAX_TOP_K).top_k == MAX_TOP_K

    with pytest.raises(ValidationError):
        QueryRequest(query="问题", top_k=MAX_TOP_K + 1)


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
