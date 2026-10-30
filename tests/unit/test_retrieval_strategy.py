"""T3.1 尾款：`retrieval_strategy` 开关必须**真的**改变行为，且 baseline 与改造前一致。

只钉开关本身，其余（融合算法、降级、限值校验）在 `tests/unit/test_retrieval_search.py`
里已经钉过。三条主线：

1. `baseline` 的候选池与改造前逐值相等（`min(top_k * 2, MAX_TOP_K)`），且不传策略时
   走的就是它——A/B 的对照臂不能偷偷变。
2. `candidate` 的候选池确有差异（`min(top_k * 4, MAX_TOP_K)`），并且这个差异能穿过
   RRF 落进返回集合；只断言"数字变大"不足以证明开关有效。
3. 换策略不动权限路径：两路仓库拿到的仍是同一个 `Scope` 对象与同一个 `kb_id`，
   走同一对 `ChunkSearchRepo` 方法（生产实现读 `v_active_chunks`）。
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import pytest
from pydantic import ValidationError

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import WORKSPACE_A, Scope
from pyknora.core.settings import (
    RETRIEVAL_STRATEGY_BASELINE,
    RETRIEVAL_STRATEGY_CANDIDATE,
    Settings,
    settings,
)
from pyknora.schemas.document import ActiveChunk, ScoredChunk
from pyknora.schemas.query import MAX_TOP_K
from pyknora.services.retrieval.search import HybridSearchResult, search_hybrid

KB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

ID_A = UUID("11111111-1111-1111-1111-111111111111")
ID_B = UUID("22222222-2222-2222-2222-222222222222")
ID_PAD3 = UUID("33333333-3333-3333-3333-333333333333")
ID_PAD4 = UUID("44444444-4444-4444-4444-444444444444")
ID_C = UUID("55555555-5555-5555-5555-555555555555")
ID_K1 = UUID("66666666-6666-6666-6666-666666666666")

#: 改造前 `_candidate_limit` 的默认倍数（`top_k * 2`），现在是 baseline 的唯一推导。
BASELINE_POOL_FACTOR = 2
#: `candidate` 的倍数。故意写死字面量而不是 import `_CANDIDATE_POOL_FACTOR`：
#: 期望值跟着实现走，就什么都钉不住了。
CANDIDATE_POOL_FACTOR = 4


class FakeEmbedder:
    model_key = "test/model"
    dim = 3

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeSearchRepo:
    """按 `limit` 截断候选池的假仓库，同时记录每次调用拿到的 scope。

    `limit` 不能忽略：策略差异只体现在这个参数上，忽略它等于把被测行为一起省掉。
    """

    def __init__(
        self,
        vector_pool: Sequence[ScoredChunk] = (),
        keyword_pool: Sequence[ActiveChunk] = (),
    ) -> None:
        self.vector_pool = list(vector_pool)
        self.keyword_pool = list(keyword_pool)
        self.vector_calls: list[tuple[Scope, UUID, int]] = []
        self.keyword_calls: list[tuple[Scope, UUID, int]] = []

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
        self.vector_calls.append((scope, kb_id, limit))
        return self.vector_pool[:limit]

    async def search_keyword(
        self, scope: Scope, kb_id: UUID, query_text: str, *, limit: int = 10
    ) -> Sequence[ActiveChunk]:
        self.keyword_calls.append((scope, kb_id, limit))
        return self.keyword_pool[:limit]


@pytest.fixture
def scope() -> Scope:
    return Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A)


def _scored(chunk_id: UUID, content: str) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
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


def _active(chunk_id: UUID, content: str) -> ActiveChunk:
    return ActiveChunk(
        chunk_id=chunk_id,
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


def _ids(outcome: HybridSearchResult) -> list[UUID]:
    return [hit.chunk_id for hit in outcome.hits]


def _signature(outcome: HybridSearchResult) -> list[tuple[UUID, float, int | None, int | None]]:
    """融合结果的完整签名：同一 `top_k` 下不变才叫"baseline 没被改动"。"""
    return [
        (hit.chunk_id, hit.rrf_score, hit.vector_rank, hit.keyword_rank) for hit in outcome.hits
    ]


def _complementary_pools() -> tuple[list[ScoredChunk], list[ActiveChunk]]:
    """两路互补的小样本：`C` 只有更宽的候选池才看得见（见差异用例）。"""
    vector = [
        _scored(ID_A, "向量第一"),
        _scored(ID_B, "向量第二"),
        _scored(ID_PAD3, "向量第三"),
        _scored(ID_PAD4, "向量第四"),
        _scored(ID_C, "互补片段"),
    ]
    keyword = [_active(ID_K1, "关键词第一"), _active(ID_C, "互补片段")]
    return vector, keyword


@pytest.mark.parametrize(
    ("top_k", "expected"),
    [(1, 2), (3, 6), (10, 20), (24, 48), (25, MAX_TOP_K), (MAX_TOP_K, MAX_TOP_K)],
)
async def test_baseline_pool_is_the_pre_change_two_times_top_k(
    top_k: int, expected: int, scope: Scope
) -> None:
    """baseline 必须逐值等于改造前的默认推导（`top_k * 2`，封顶 `MAX_TOP_K`）。

    封顶很关键：`top_k = 50` 时算出 100 会直接越过资源边界，所以 50 就是 50。
    """
    repo = FakeSearchRepo()

    outcome = await search_hybrid(
        scope,
        KB_ID,
        "问题",
        embedder=FakeEmbedder(),
        repo=repo,
        top_k=top_k,
        retrieval_strategy=RETRIEVAL_STRATEGY_BASELINE,
    )

    assert outcome.retrieval_strategy == RETRIEVAL_STRATEGY_BASELINE
    assert outcome.candidate_k == expected
    assert repo.vector_calls == [(scope, KB_ID, expected)]
    assert repo.keyword_calls == [(scope, KB_ID, expected)]


@pytest.mark.parametrize(
    ("top_k", "expected"),
    [(1, 4), (3, 12), (10, 40), (12, 48), (13, MAX_TOP_K), (25, MAX_TOP_K), (MAX_TOP_K, MAX_TOP_K)],
)
async def test_candidate_pool_is_four_times_top_k(top_k: int, expected: int, scope: Scope) -> None:
    """candidate 的候选池是 baseline 的 2 倍（4x vs 2x），同样受 `MAX_TOP_K` 封顶。

    两路必须拿到**同一个**候选数：一边 4x 一边 2x 会让融合偏向池子大的一路，
    A/B 的差值也就不再只归因于"候选池更大"。
    """
    repo = FakeSearchRepo()

    outcome = await search_hybrid(
        scope,
        KB_ID,
        "问题",
        embedder=FakeEmbedder(),
        repo=repo,
        top_k=top_k,
        retrieval_strategy=RETRIEVAL_STRATEGY_CANDIDATE,
    )

    assert outcome.retrieval_strategy == RETRIEVAL_STRATEGY_CANDIDATE
    assert outcome.candidate_k == expected
    assert repo.vector_calls == [(scope, KB_ID, expected)]
    assert repo.keyword_calls == [(scope, KB_ID, expected)]


@pytest.mark.parametrize("top_k", [3, 40])
async def test_baseline_matches_the_old_explicit_default(top_k: int, scope: Scope) -> None:
    """baseline 与"显式传改造前默认值"必须逐字段一致——这是 A/B 可比性的地基。

    改造前没有策略参数，调用方要么不传、要么显式传 `candidate_k`。两种写法现在都
    必须落到同一条推导上，否则对照实验的 baseline 不是线上那条路，两臂差值失去意义。
    """
    vector_pool, keyword_pool = _complementary_pools()
    implicit = await search_hybrid(
        scope,
        KB_ID,
        "问题",
        embedder=FakeEmbedder(),
        repo=FakeSearchRepo(vector_pool, keyword_pool),
        top_k=top_k,
        retrieval_strategy=RETRIEVAL_STRATEGY_BASELINE,
    )
    explicit = await search_hybrid(
        scope,
        KB_ID,
        "问题",
        embedder=FakeEmbedder(),
        repo=FakeSearchRepo(vector_pool, keyword_pool),
        top_k=top_k,
        candidate_k=min(top_k * BASELINE_POOL_FACTOR, MAX_TOP_K),
    )

    assert implicit.candidate_k == explicit.candidate_k
    assert _signature(implicit) == _signature(explicit)


async def test_omitting_strategy_follows_settings(
    scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP 层不传参：切换只靠 `PYKNORA_RETRIEVAL_STRATEGY` + 重启。

    这里把配置改成 candidate 后不传参，两路候选池必须拿到 4x——否则"改环境变量就能
    切策略"只是文档里的一句话。
    """
    monkeypatch.setattr(settings, "retrieval_strategy", RETRIEVAL_STRATEGY_CANDIDATE)
    repo = FakeSearchRepo()

    outcome = await search_hybrid(scope, KB_ID, "问题", embedder=FakeEmbedder(), repo=repo, top_k=5)

    assert outcome.retrieval_strategy == RETRIEVAL_STRATEGY_CANDIDATE
    assert outcome.candidate_k == 5 * CANDIDATE_POOL_FACTOR
    assert repo.vector_calls == [(scope, KB_ID, 20)]
    assert repo.keyword_calls == [(scope, KB_ID, 20)]


async def test_candidate_changes_the_fused_output_not_only_the_pool(scope: Scope) -> None:
    """开关有效的证据：候选池变宽让两路互补的片段**真的浮上来**。

    构造：`C` 在向量路排第 5（baseline 池子只到第 4 条，它进不了融合）、在关键词路
    排第 2。baseline 下 C 只剩关键词那一路的 `1/(60+2)`，挤不进前 2；candidate 下
    它拿到 `1/(60+5) + 1/(60+2)`，直接排到第一。若只断言 `candidate_k` 变大，
    "开关能改变行为"这个结论就没人钉住。
    """
    vector_pool, keyword_pool = _complementary_pools()

    baseline = await search_hybrid(
        scope,
        KB_ID,
        "问题",
        embedder=FakeEmbedder(),
        repo=FakeSearchRepo(vector_pool, keyword_pool),
        top_k=2,
        retrieval_strategy=RETRIEVAL_STRATEGY_BASELINE,
    )
    candidate = await search_hybrid(
        scope,
        KB_ID,
        "问题",
        embedder=FakeEmbedder(),
        repo=FakeSearchRepo(vector_pool, keyword_pool),
        top_k=2,
        retrieval_strategy=RETRIEVAL_STRATEGY_CANDIDATE,
    )

    assert baseline.candidate_k == 4
    assert candidate.candidate_k == 8
    # 返回条数不动：放大的是候选池，不是 top_k。
    assert len(baseline.hits) == len(candidate.hits) == 2
    assert _ids(baseline) == [ID_A, ID_K1]
    assert _ids(candidate) == [ID_C, ID_A]
    assert candidate.hits[0].vector_rank == 5
    assert candidate.hits[0].keyword_rank == 2
    assert candidate.hits[0].rrf_score == pytest.approx(1 / 65 + 1 / 62)


@pytest.mark.parametrize("top_k", [25, MAX_TOP_K])
async def test_the_switch_is_a_no_op_once_the_pool_hits_the_cap(top_k: int, scope: Scope) -> None:
    """上限压住时两个策略结算出同一个池子——这不是缺陷，是 A/B 的操作约束。

    `MAX_TOP_K = 50`：baseline 在 `top_k >= 25` 就被压到 50，candidate 最晚在
    `top_k >= 13` 也到 50，于是两臂完全同路。用 `top_k = 50` 跑 A/B 得到的"差值"
    只可能是噪声，这条用例把边界写在测试里而不是口口相传。
    """
    vector_pool, keyword_pool = _complementary_pools()

    baseline = await search_hybrid(
        scope,
        KB_ID,
        "问题",
        embedder=FakeEmbedder(),
        repo=FakeSearchRepo(vector_pool, keyword_pool),
        top_k=top_k,
        retrieval_strategy=RETRIEVAL_STRATEGY_BASELINE,
    )
    candidate = await search_hybrid(
        scope,
        KB_ID,
        "问题",
        embedder=FakeEmbedder(),
        repo=FakeSearchRepo(vector_pool, keyword_pool),
        top_k=top_k,
        retrieval_strategy=RETRIEVAL_STRATEGY_CANDIDATE,
    )

    assert baseline.candidate_k == candidate.candidate_k == MAX_TOP_K
    assert _signature(baseline) == _signature(candidate)


@pytest.mark.parametrize("strategy", ["hybrid", "Candidate", ""])
async def test_unknown_strategy_is_rejected_before_any_io(strategy: str, scope: Scope) -> None:
    """脚本/评测按次传参时的非法值：调模型与打库**之前**拒绝。

    忽略拼错的策略，A/B 会跑成"两臂都是 baseline"，而报告里两边写得清清楚楚是
    baseline vs candidate——代价是整轮评测作废。
    """
    embedder = FakeEmbedder()
    repo = FakeSearchRepo()

    with pytest.raises(AppError) as exc:
        await search_hybrid(
            scope, KB_ID, "问题", embedder=embedder, repo=repo, retrieval_strategy=strategy
        )

    assert exc.value.code == ErrorCode.INVALID_REQUEST
    assert exc.value.details == {
        "allowed": [RETRIEVAL_STRATEGY_BASELINE, RETRIEVAL_STRATEGY_CANDIDATE]
    }
    assert embedder.calls == 0
    assert repo.vector_calls == []
    assert repo.keyword_calls == []


def test_settings_rejects_unknown_strategy_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置非法值选择"启动即失败"，不静默退化——这是本项目的显式取舍。

    退化成 baseline 更"温和"，但会让运维以为 A/B 在跑 candidate，直到看报告才发现
    两臂同路；构造期失败把代价压到一次启动。`_env_file=None` 是为了让用例只受
    `monkeypatch` 的显式环境变量影响，不被本机 `.env` 干扰。
    """
    monkeypatch.delenv("PYKNORA_RETRIEVAL_STRATEGY", raising=False)
    assert Settings(_env_file=None).retrieval_strategy == RETRIEVAL_STRATEGY_BASELINE

    monkeypatch.setenv("PYKNORA_RETRIEVAL_STRATEGY", RETRIEVAL_STRATEGY_CANDIDATE)
    assert Settings(_env_file=None).retrieval_strategy == RETRIEVAL_STRATEGY_CANDIDATE

    for invalid in ("hybrid", "Candidate", ""):
        monkeypatch.setenv("PYKNORA_RETRIEVAL_STRATEGY", invalid)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


@pytest.mark.parametrize("strategy", [RETRIEVAL_STRATEGY_BASELINE, RETRIEVAL_STRATEGY_CANDIDATE])
async def test_strategy_does_not_change_the_permission_path(strategy: str, scope: Scope) -> None:
    """换策略不许换权限视图：两路仍带同一个 `Scope` 对象走同一对仓库方法。

    生产实现里 `ChunkSearchRepo.search_similar` / `search_keyword` 都读
    `v_active_chunks`，且 `scope` 是必填第一个参数。如果 candidate 为了"多取一点"
    另起一条少一层权限过滤的查询，这种漏洞在假仓库上永远看不出来——所以这里钉
    **对象身份**（`is`）而不是等值：等值会放过"重新拼一个同字段 Scope"的实现。
    """
    repo = FakeSearchRepo()

    outcome = await search_hybrid(
        scope,
        KB_ID,
        "问题",
        embedder=FakeEmbedder(),
        repo=repo,
        top_k=3,
        retrieval_strategy=strategy,
    )

    assert outcome.retrieval_strategy == strategy
    # 每条分支恰好一次调用：没有"策略专用"的第二条查询路径。
    assert len(repo.vector_calls) == 1
    assert len(repo.keyword_calls) == 1
    vector_scope, vector_kb, _ = repo.vector_calls[0]
    keyword_scope, keyword_kb, _ = repo.keyword_calls[0]
    assert vector_scope is scope
    assert keyword_scope is scope
    assert vector_kb == keyword_kb == KB_ID
