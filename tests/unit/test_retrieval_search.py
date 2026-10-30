"""成员3阶段1：问题向量化后委托给带 Scope 的向量检索仓库。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import WORKSPACE_A, Scope
from pyknora.schemas.document import ActiveChunk, ScoredChunk
from pyknora.schemas.query import MAX_TOP_K
from pyknora.services.retrieval.rrf import reciprocal_rank_fusion
from pyknora.services.retrieval.search import search_hybrid, search_vector

KB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class FakeEmbedder:
    model_key = "test/model"
    dim = 3

    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.received_texts: list[str] | None = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.received_texts = list(texts)
        return self.vectors


class FakeSearchRepo:
    def __init__(
        self,
        results: Sequence[ScoredChunk] = (),
        keyword_results: Sequence[ActiveChunk] = (),
        *,
        vector_error: Exception | None = None,
        keyword_error: Exception | None = None,
    ) -> None:
        self.results = results
        self.keyword_results = keyword_results
        self.vector_error = vector_error
        self.keyword_error = keyword_error
        self.received: tuple[Scope, UUID, list[float], int] | None = None
        self.keyword_received: tuple[Scope, UUID, str, int] | None = None

    async def search_similar(
        self,
        scope: Scope,
        kb_id: UUID,
        query_vector: Sequence[float],
        *,
        limit: int = 10,
        document_id: UUID | None = None,
    ) -> Sequence[ScoredChunk]:
        assert document_id is None
        if self.vector_error is not None:
            raise self.vector_error
        self.received = (scope, kb_id, list(query_vector), limit)
        return self.results

    async def search_keyword(
        self, scope: Scope, kb_id: UUID, query_text: str, *, limit: int = 10
    ) -> Sequence[ActiveChunk]:
        if self.keyword_error is not None:
            raise self.keyword_error
        self.keyword_received = (scope, kb_id, query_text, limit)
        return self.keyword_results


@pytest.fixture
def scope() -> Scope:
    return Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A)


async def test_search_vector_embeds_question_and_passes_scope_and_limit(scope: Scope) -> None:
    embedder = FakeEmbedder([[0.1, 0.2, 0.3]])
    repo = FakeSearchRepo()

    result = await search_vector(
        scope,
        KB_ID,
        "  如何申请借阅证？  ",
        embedder=embedder,
        repo=repo,
        top_k=5,
    )

    assert not result
    assert embedder.received_texts == ["如何申请借阅证？"]
    assert repo.received == (scope, KB_ID, [0.1, 0.2, 0.3], 5)


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
async def test_search_vector_rejects_empty_query_before_dependencies(
    query: str, scope: Scope
) -> None:
    embedder = FakeEmbedder([[0.1, 0.2, 0.3]])
    repo = FakeSearchRepo()

    with pytest.raises(AppError) as exc:
        await search_vector(scope, KB_ID, query, embedder=embedder, repo=repo)

    assert exc.value.code == ErrorCode.INVALID_REQUEST
    assert embedder.received_texts is None
    assert repo.received is None


async def test_search_vector_rejects_nonpositive_top_k(scope: Scope) -> None:
    embedder = FakeEmbedder([[0.1, 0.2, 0.3]])
    repo = FakeSearchRepo()

    with pytest.raises(AppError) as exc:
        await search_vector(scope, KB_ID, "问题", embedder=embedder, repo=repo, top_k=0)

    assert exc.value.code == ErrorCode.INVALID_REQUEST
    assert embedder.received_texts is None
    assert repo.received is None


async def test_search_vector_rejects_unexpected_embedding_count(scope: Scope) -> None:
    embedder = FakeEmbedder([])
    repo = FakeSearchRepo()

    with pytest.raises(AppError) as exc:
        await search_vector(scope, KB_ID, "问题", embedder=embedder, repo=repo)

    assert exc.value.code == ErrorCode.INTERNAL_ERROR
    assert exc.value.details == {"expected": 1, "actual": 0}
    assert repo.received is None


async def test_search_hybrid_queries_both_channels_with_same_scope(scope: Scope) -> None:
    embedder = FakeEmbedder([[0.1, 0.2, 0.3]])
    repo = FakeSearchRepo()

    outcome = await search_hybrid(
        scope,
        KB_ID,
        "  关键词查询  ",
        embedder=embedder,
        repo=repo,
        top_k=3,
        candidate_k=8,
    )

    assert outcome.hits == []
    assert outcome.degraded == {}
    assert embedder.received_texts == ["关键词查询"]
    assert repo.received == (scope, KB_ID, [0.1, 0.2, 0.3], 8)
    assert repo.keyword_received == (scope, KB_ID, "关键词查询", 8)


def test_rrf_fuses_ranks_and_keeps_one_copy_of_each_chunk() -> None:
    vector = [
        _scored_chunk("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "向量第一"),
        _scored_chunk("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "向量第二"),
    ]
    keyword = [
        _active_chunk("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "关键词第一"),
        _active_chunk("cccccccc-cccc-cccc-cccc-cccccccccccc", "关键词第二"),
    ]

    hits = reciprocal_rank_fusion(vector, keyword, limit=3, k=60)

    assert [hit.chunk_id for hit in hits] == [
        UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
    ]
    assert hits[0].rrf_score == pytest.approx(1 / 62 + 1 / 61)
    assert hits[0].vector_rank == 2
    assert hits[0].keyword_rank == 1
    # 两路都命中的片段使用向量结果中的公共元数据，不重复返回。
    assert hits[0].content == "向量第二"


def test_rrf_applies_final_limit_and_rejects_invalid_options() -> None:
    vector = [_scored_chunk("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "片段")]

    assert len(reciprocal_rank_fusion(vector, [], limit=1)) == 1
    with pytest.raises(ValueError, match="limit"):
        reciprocal_rank_fusion(vector, [], limit=0)
    with pytest.raises(ValueError, match="k"):
        reciprocal_rank_fusion(vector, [], k=0)


def _scored_chunk(chunk_id: str, content: str) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=UUID(chunk_id),
        workspace_id=WORKSPACE_A,
        kb_id=KB_ID,
        document_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        build_id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        index_version=1,
        attempt_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        chunk_ordinal=0,
        content=content,
        embedding_model_key="test/model",
        embedding_dim=3,
        distance=0.1,
    )


def _active_chunk(chunk_id: str, content: str) -> ActiveChunk:
    return ActiveChunk(
        chunk_id=UUID(chunk_id),
        workspace_id=WORKSPACE_A,
        kb_id=KB_ID,
        document_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        build_id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        index_version=1,
        attempt_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        chunk_ordinal=0,
        content=content,
        embedding_model_key="test/model",
        embedding_dim=3,
        chunk_size=512,
    )


async def test_search_hybrid_routes_keyword_to_second_repo(scope: Scope) -> None:
    """并行分支：关键词那一路必须走传入的第二个仓库。

    两个 session 是并行的前提（`AsyncSession` 不支持并发语句），所以这里断言
    "第二路没落到第一个仓库上"——写错的话两路会挤在同一个 session 上，生产里
    表现为随机 `InvalidRequestError`，而在 fake 上看起来一切正常。
    """
    embedder = FakeEmbedder([[0.1, 0.2, 0.3]])
    vector_repo = FakeSearchRepo(
        results=[_scored_chunk("11111111-1111-1111-1111-111111111111", "向量命中")]
    )
    keyword_repo = FakeSearchRepo(
        keyword_results=[_active_chunk("22222222-2222-2222-2222-222222222222", "关键词命中")]
    )

    outcome = await search_hybrid(
        scope,
        KB_ID,
        "开放时间",
        embedder=embedder,
        repo=vector_repo,
        keyword_repo=keyword_repo,
        top_k=2,
    )

    assert vector_repo.received is not None
    assert vector_repo.keyword_received is None, "关键词查询不该落到向量仓库上"
    assert keyword_repo.keyword_received is not None
    assert outcome.degraded == {}
    assert {hit.chunk_id for hit in outcome.hits} == {
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
    }


async def test_limits_above_max_top_k_are_rejected(scope: Scope) -> None:
    """上限是资源边界：超了必须报错，而不是静默截断成 50 条。

    服务层单独校验的依据：脚本/worker 不走 HTTP schema，但它同样会把这个数
    直接塞进 SQL 的 `LIMIT`。
    """
    embedder = FakeEmbedder([[0.1, 0.2, 0.3]])
    repo = FakeSearchRepo()

    with pytest.raises(AppError) as exc:
        await search_vector(scope, KB_ID, "问题", embedder=embedder, repo=repo, top_k=MAX_TOP_K + 1)

    assert exc.value.code == ErrorCode.INVALID_REQUEST
    assert exc.value.details == {"max": MAX_TOP_K, "actual": MAX_TOP_K + 1}
    assert embedder.received_texts is None, "校验要在调模型之前"
    assert repo.received is None


async def test_hybrid_rejects_over_max_candidate_k(scope: Scope) -> None:
    """candidate_k 决定两路各取多少候选，同样不能无限放大。"""
    embedder = FakeEmbedder([[0.1, 0.2, 0.3]])
    repo = FakeSearchRepo()

    with pytest.raises(AppError) as exc:
        await search_hybrid(
            scope,
            KB_ID,
            "问题",
            embedder=embedder,
            repo=repo,
            candidate_k=MAX_TOP_K + 1,
        )

    assert exc.value.code == ErrorCode.INVALID_REQUEST
    assert "candidate_k" in str(exc.value.message)
    assert embedder.received_texts is None


async def test_hybrid_degrades_to_keyword_when_vector_branch_fails(scope: Scope) -> None:
    """向量路挂了（如 embedding 服务不可用）不该整请求 500：关键词路照样能答。

    但降级必须**可见**——`degraded` 里要写明哪一路挂了、错误码是什么。
    """
    embedder = FakeEmbedder([[0.1, 0.2, 0.3]])
    repo = FakeSearchRepo(
        keyword_results=[_active_chunk("22222222-2222-2222-2222-222222222222", "关键词命中")],
        vector_error=AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "embedding down"),
    )

    outcome = await search_hybrid(scope, KB_ID, "开放时间", embedder=embedder, repo=repo, top_k=2)

    assert [hit.chunk_id for hit in outcome.hits] == [UUID("22222222-2222-2222-2222-222222222222")]
    assert outcome.degraded == {"vector": "DEPENDENCY_UNAVAILABLE"}


async def test_hybrid_degrades_to_vector_when_keyword_branch_fails(scope: Scope) -> None:
    """关键词路挂了（如迁移没跑、函数不存在）同样只降级，并报出分支与错误类型。"""
    embedder = FakeEmbedder([[0.1, 0.2, 0.3]])
    repo = FakeSearchRepo(
        results=[_scored_chunk("11111111-1111-1111-1111-111111111111", "向量命中")],
        keyword_error=RuntimeError("pyknora_cjk_tsquery does not exist"),
    )

    outcome = await search_hybrid(scope, KB_ID, "开放时间", embedder=embedder, repo=repo, top_k=2)

    assert [hit.chunk_id for hit in outcome.hits] == [UUID("11111111-1111-1111-1111-111111111111")]
    assert outcome.degraded == {"keyword": "RuntimeError"}


async def test_hybrid_raises_when_both_branches_fail(scope: Scope) -> None:
    """两路都挂时不能返回空列表：那等于谎称「库里没有匹配」。"""
    embedder = FakeEmbedder([[0.1, 0.2, 0.3]])
    repo = FakeSearchRepo(
        vector_error=AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "embedding down"),
        keyword_error=AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "keyword down"),
    )

    with pytest.raises(AppError) as exc:
        await search_hybrid(scope, KB_ID, "开放时间", embedder=embedder, repo=repo, top_k=2)

    assert exc.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    assert exc.value.message == "embedding down"


async def test_hybrid_does_not_degrade_on_invalid_request(scope: Scope) -> None:
    """`INVALID_REQUEST` 不降级：请求本身不合法时，另一路返回什么都是错的。

    降级会把它伪装成「搜得少」，调用方永远修不好参数。
    """
    embedder = FakeEmbedder([[0.1, 0.2, 0.3]])
    repo = FakeSearchRepo(
        keyword_results=[_active_chunk("22222222-2222-2222-2222-222222222222", "关键词命中")],
        vector_error=AppError(ErrorCode.INVALID_REQUEST, "bad request"),
    )

    with pytest.raises(AppError) as exc:
        await search_hybrid(scope, KB_ID, "开放时间", embedder=embedder, repo=repo, top_k=2)

    assert exc.value.code == ErrorCode.INVALID_REQUEST
