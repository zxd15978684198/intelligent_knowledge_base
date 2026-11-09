"""检索编排：向量路、关键词路，以及两路的 RRF 融合。

一条贯穿全模块的原则：**降级必须可见**。本项目已经吃过一次"静默失效"的亏
（`tsvector` 把整段汉字当一个 token，FTS 那一路恒空且不报错，融合结果比纯向量
还差却无任何信号）。所以两路里任何一路失败时，另一路仍要出结果，但**失败的
事实必须随结果一起返回**（`HybridSearchResult.degraded`），不许当成正常结果。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.logging import log_event
from pyknora.core.scope import Scope
from pyknora.core.settings import (
    RETRIEVAL_STRATEGY_BASELINE,
    RETRIEVAL_STRATEGY_CANDIDATE,
    settings,
)
from pyknora.schemas.document import ActiveChunk, ScoredChunk
from pyknora.schemas.query import MAX_TOP_K, RetrievedChunk
from pyknora.services.contracts.reads import ChunkSearchRepo
from pyknora.services.ingest.embedder import EmbeddingProvider
from pyknora.services.retrieval.rrf import reciprocal_rank_fusion

logger = logging.getLogger(__name__)

#: `candidate` 策略把候选池放大到 `top_k` 的几倍。取 4 而不是 2，是为了让它与
#: baseline 的 `top_k * 2` 有可辨认的差别，同时仍被 `MAX_TOP_K` 压住；倍数本身是
#: 待标定值（要靠评测集定），当前没有评测集，所以先给一个保守的放大。
#:
#: 上限会吃掉这个差别：`top_k >= MAX_TOP_K // 2`（25）时 baseline 已经封到 50，
#: candidate 也是 50，两个策略结算出同一个池子——所以 A/B 必须用 `top_k < 25`
#: 才有可比性。这条边界由 `tests/unit/test_retrieval_strategy.py` 钉住。
_CANDIDATE_POOL_FACTOR = 4


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    """融合结果 + 降级实情 + 本次实际走的策略。

    `degraded` 为空表示两路都成功。非空时键是失败的分支名（`"vector"` /
    `"keyword"`），值是该分支的错误码（`AppError` 取 `code`，其他异常取类名）。
    调用方——尤其是 HTTP 层——有义务把它透出去，否则就是又一次静默降级。

    `retrieval_strategy` 是**实际生效**的策略（参数 > `PYKNORA_RETRIEVAL_STRATEGY`
    > 默认 `baseline`），不是"配置里写的那个"；`candidate_k` 是算完之后真正进两路
    `LIMIT` 的候选数。`candidate_k=None` 只表示构造方没填（旧调用点），不是"取了
    0 条"。
    """

    hits: list[RetrievedChunk]
    degraded: dict[str, str] = field(default_factory=dict)
    retrieval_strategy: str = RETRIEVAL_STRATEGY_BASELINE
    candidate_k: int | None = None


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


def _candidate_limit(*, top_k: int, candidate_k: int | None) -> int:
    """算出两路各自该取多少候选。

    默认取 `top_k * 2`（封顶 `MAX_TOP_K`）而不是 `top_k`：**RRF 只能重排它看到的候选**。
    两路各只取 `top_k` 条时，融合池一共就 `2 * top_k` 条，而某一路排在第 `top_k + 1`、
    另一路完全没命中的片段永远浮不上来——那正是混合检索相对单路唯一的价值所在。
    也就是说 `candidate_k == top_k` 时融合不是"更保守"，而是**基本白做**：它只是把
    两份长度相同的列表交错一下。

    这是结构约束（候选池必须宽于返回数），不是调参：倍数取 2 是为了让两条排序各
    多出一倍余量，同时不让 SQL 扫描翻倍再多。真正的取值要靠评测集定，本轮没有评测集。
    """
    if candidate_k is None:
        return min(top_k * 2, MAX_TOP_K)
    _require_bounded_limit(candidate_k, name="candidate_k")
    if candidate_k < top_k:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "candidate_k must not be less than top_k",
            details={"candidate_k": candidate_k, "top_k": top_k},
        )
    return candidate_k


def _resolve_candidate_k(*, top_k: int, strategy: str, explicit: int | None) -> int:
    """按策略结算候选池大小；显式传参优先。返回值直接进两路 `LIMIT`。

    `baseline` 刻意复用 `_candidate_limit(top_k=top_k, candidate_k=None)`，而不是
    在这里再写一个 `top_k * 2`：改造前只有那一条推导，"默认策略不改线上行为"要
    靠**结构上走同一条代码**成立，而不是靠两处算式恰好相等——后者在有人调了
    `_candidate_limit` 的倍数时就会静默分叉，而分叉的后果是对照实验的 baseline
    已经不是线上那条路。

    `candidate` 只放大候选池，其余参数（rrf_k、切片、embedding、top_k）一概不动：
    一次只改一个主因素，A/B 的差值才能归因到候选池大小。

    非法策略在调用模型与数据库之前拒绝：拼错的策略若被忽略，跑完一轮对照实验
    才发现两组走的是同一条路，代价是整轮结果作废。
    """
    if explicit is not None:
        return _candidate_limit(top_k=top_k, candidate_k=explicit)
    if strategy == RETRIEVAL_STRATEGY_BASELINE:
        return _candidate_limit(top_k=top_k, candidate_k=None)
    if strategy == RETRIEVAL_STRATEGY_CANDIDATE:
        return min(top_k * _CANDIDATE_POOL_FACTOR, MAX_TOP_K)
    raise AppError(
        ErrorCode.INVALID_REQUEST,
        f"unknown retrieval strategy: {strategy!r}",
        details={"allowed": [RETRIEVAL_STRATEGY_BASELINE, RETRIEVAL_STRATEGY_CANDIDATE]},
    )


#: `error_type` 的四类取值（计划 §6 / Day4 §8.3）。它是**给排障的人**做粗分类的，
#: 不是错误码的翻版：错误码有 17 个，观测只关心四件事——要不要补权限、要不要重建
#: 索引、是不是被过滤掉了、还是库里确实没有。映射不上的错误填 None，不猜。
_ERROR_TYPE_BY_CODE: dict[ErrorCode, str] = {
    ErrorCode.UNAUTHENTICATED: "no_permission",
    ErrorCode.FORBIDDEN: "no_permission",
    ErrorCode.RESOURCE_NOT_FOUND: "index_missing",
    ErrorCode.DEPENDENCY_UNAVAILABLE: "index_missing",
}


def _elapsed_ms(since: float) -> float:
    """从 `since` 到此刻的毫秒数。换算只写这一处，免得哪处漏乘 1000。"""
    return (time.perf_counter() - since) * 1000.0


def _error_type(error: BaseException) -> str | None:
    """把一次检索失败折成 `error_type` 的一个取值；认不出就 None（不猜）。"""
    if isinstance(error, AppError):
        return _ERROR_TYPE_BY_CODE.get(error.code)
    return None


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
    retrieval_strategy: str | None = None,
) -> HybridSearchResult:
    """两路候选检索后用 RRF 融合；**单路失败时降级到另一路并在 `degraded` 里报告**。

    `keyword_repo` 传进来时两路**真并行**（`asyncio.gather`）：SQLAlchemy 的
    `AsyncSession` 不支持并发语句，所以并行版必须由调用方给两个绑定不同
    session 的仓库（见 `api/deps.py` 的 `get_keyword_search_repo`）。不传时退回
    顺序执行，结果与并行版一致——单元测试和脚本走这条，不用为了测一个函数的
    融合逻辑去开真连接。

    `candidate_k` 是**两路各自**取多少候选；不传时由策略决定（`baseline` → 改造前
    的 `top_k * 2`，`candidate` → 放大候选池，见 `_resolve_candidate_k`）。它必须
    不小于 `top_k`，否则候选池比返回值还窄，融合没有意义。

    `retrieval_strategy` 缺省读 `settings.retrieval_strategy`（环境变量
    `PYKNORA_RETRIEVAL_STRATEGY`）：HTTP 层不传参，切换只靠换配置；脚本/评测要
    逐次对照时按次传参。

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

    # 策略与候选池都在碰模型/数据库之前结算：参数错了要立刻 422，不能先跑一遍
    # 检索再发现"策略名打错、这一轮其实白跑"。
    strategy = retrieval_strategy if retrieval_strategy is not None else settings.retrieval_strategy
    candidate_limit = _resolve_candidate_k(top_k=top_k, strategy=strategy, explicit=candidate_k)
    if rrf_k < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, "rrf_k must be greater than zero")

    # 计时从"真要碰模型/数据库"这一刻起：上面几行都是参数校验，422 的耗时不属于检索，
    # 混进来会把一次参数错误记成一次"很慢的检索"。
    started = time.perf_counter()

    async def _vector_branch() -> Sequence[ScoredChunk]:
        # 复用 search_vector 而不是在这里再写一遍"embed → 校验维度 → 打库"：
        # 两条路必须对 embedding 的失败方式给出完全一致的处理。
        return await search_vector(
            scope, kb_id, normalized_query, embedder=embedder, repo=repo, top_k=candidate_limit
        )

    async def _keyword_branch() -> Sequence[ActiveChunk]:
        target = keyword_repo if keyword_repo is not None else repo
        return await target.search_keyword(scope, kb_id, normalized_query, limit=candidate_limit)

    try:
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
            logger.warning(
                "retrieval branch degraded: branch=%s code=%s error=%s", branch, code, error
            )

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
    except Exception as exc:
        # 失败分支也要发事件：`outcome=error` 是"N 次检索有几成直接失败"的唯一来源，
        # 只在成功路径发等于把最该看见的那一半藏起来。计数写 0 是事实——这次调用一条
        # 结果都没返回；时间是真测的，不写 0。
        log_event(
            "retrieval_completed",
            retrieval_count=0,
            duration_ms=_elapsed_ms(started),
            stage="retrieve",
            outcome="error",
            error_type=_error_type(exc),
        )
        raise

    if hits:
        outcome = "ok"
        error_type: str | None = None
    else:
        outcome = "empty"
        # 两路都成功却一条不出，是"库里确实没有"；有候选却没活到融合结果里，才算
        # 被过滤掉。后者当前不可达（RRF 不会把非空候选池清空），但这个分支必须留着：
        # 以后谁在 search_hybrid 里加了打分阈值，标签会自动变成对的，而不是悄悄退化成
        # `no_evidence` 去骗人。
        error_type = "filtered_out" if (vector_value or keyword_value) else "no_evidence"

    log_event(
        "retrieval_completed",
        retrieval_count=len(hits),
        duration_ms=_elapsed_ms(started),
        stage="retrieve",
        outcome=outcome,
        error_type=error_type,
    )
    return HybridSearchResult(
        hits=hits,
        degraded=degraded,
        retrieval_strategy=strategy,
        candidate_k=candidate_limit,
    )
