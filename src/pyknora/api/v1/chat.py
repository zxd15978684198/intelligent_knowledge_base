"""成员3：SSE 流式 RAG 问答接口。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from typing import Annotated, Any, Final
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from pyknora.api.deps import (
    get_chunk_search_repo,
    get_keyword_search_repo,
    get_scope,
    require_kb_access,
    require_permission,
)
from pyknora.api.main import get_app_state
from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.logging import bind_request_context, log_event, reset_request_context
from pyknora.core.scope import Scope
from pyknora.core.settings import settings
from pyknora.db.repos.reads import SqlChunkSearchRepo
from pyknora.schemas.chat import (
    ChatRequest,
    Citation,
    DoneEvent,
    RetrievalEvent,
    RunStatus,
    StreamErrorEvent,
    TokenEvent,
)
from pyknora.schemas.query import RetrievedChunk
from pyknora.services.auth.permissions import CHAT_RUN
from pyknora.services.rag.answer import (
    cited_indices,
    invalid_cited_indices,
    select_context_chunks,
    stream_answer,
)
from pyknora.services.rag.budget import RunBudget
from pyknora.services.rag.evidence import Evidence, assess
from pyknora.services.retrieval.search import search_hybrid

router = APIRouter(tags=["chat"], dependencies=[Depends(require_permission(CHAT_RUN))])

logger = logging.getLogger(__name__)

#: `RunStatus` → 日志里的 `outcome`（计划 §6：completed / error / timeout 三态）。
#:
#: `ABSTAINED` 归 `completed`：门控拒答是一次**正常收尾**——有正文、有终态、有 verdict，
#: 客户端拿到的和成功一样完整。把它记成 error 会让"错误率"随"用户问了资料里没有的问题"
#: 一起涨，而那不是故障。拒答与成功之别由 `verdict` 表达，不该由 `outcome` 重复表达。
_OUTCOME_BY_STATUS: dict[RunStatus, str] = {
    RunStatus.COMPLETED: "completed",
    RunStatus.ABSTAINED: "completed",
    RunStatus.FAILED: "error",
    RunStatus.BUDGET_EXCEEDED: "error",
    RunStatus.TIMEOUT: "timeout",
}

#: 模型**自己**拒答时回答的开头短语；命中即把终态从 `COMPLETED` 降为 `ABSTAINED`。
#:
#: 为什么要这条判据：`ABSTAINED` 原本只由证据门控设置——门控判"这段资料撑不住问题"
#: 才拒答。可现实里还有一类拒答发生在模型侧：问句要求它导出连接串、复述资料里不存在的
#: 条款、或念出未授权预算，门控看着资料挺相关（SUPPORTED），模型却回"我不能这样做"。
#: 这种回答记成 `COMPLETED`，"答完了"与"拒绝了"在监控与评测里就分不开——实测
#: `injection-01/03`、`poison-03/04` 四条安全样本正文都是拒答，状态却全是 `COMPLETED`。
#:
#: 只**降级**、不升级：命中即记拒答，漏判也只是维持 `COMPLETED`，不会把一次失败的作答
#: 洗成成功。只认**开头**：句中出现的"不能"不算（"不能拆成两张单子"是在回答问题）。
#:
#: 前缀按实测开头归类，宁可取宽的那一类（"我没有…""不能直接…"）也不要逐个短语枚举：
#: 同一条样本三次运行的措辞各不相同（"我没有照做"/"不能这样做"/"这段文字我不会执行"），
#: 逐条打补丁永远追不上。这类开头的正常回答不存在——材料的答案不会以"我没有"起句。
#: 已知局限：这是一条文案判据，模型若用没列在这里的开头（"抱歉"/"根据授权要求"）仍会
#: 漏判；真正稳的做法是让模型显式吐一个拒答信号，那需要改提示词并重跑基线。
_REFUSAL_PREFIXES: Final[tuple[str, ...]] = (
    "我没有",
    "我没法",
    "我不能",
    "我不便",
    "我无法",
    "我做不到",
    "我这边没有",
    "我在你提供的资料",
    "这段文字我不会",
    "不会照做",
    "不能直接",
    "不能这样做",
    "不能照做",
    "不能照办",
    "这个推断不成立",
    "抱歉",
    "恕难",
    "拒绝回答",
)

#: 回答开头常见的 Markdown 装饰（加粗、标题、引用块、空白）；判拒答前先剥掉，
#: 否则 "**我不能…" 会因为第一个字符是 `*` 而漏判。
_LEADING_MARKUP: Final[re.Pattern[str]] = re.compile(r"^[\s*#>]+")


def _looks_like_model_refusal(answer_text: str) -> bool:
    """回答是不是模型自己拒绝执行（而不是正常作答）。"""
    head = _LEADING_MARKUP.sub("", answer_text, count=1)
    return head.startswith(_REFUSAL_PREFIXES)


def _elapsed_ms(since: float, until: float | None = None) -> float:
    """从 `since` 到 `until`（缺省此刻）的毫秒数。

    换算只写这一处：四段计时各写一遍 `* 1000.0`，迟早有一处漏乘，然后
    `retrieval_ms=0.8` 被读成"不到 1 毫秒"——比没有这个字段更误导。
    """
    end = time.perf_counter() if until is None else until
    return (end - since) * 1000.0


def _request_id(request: Request | None) -> str | None:
    """取 `api/main.py` 的 `_attach_request_id` 中间件挂在 `request.state` 上的 ID。

    只读不造：ID 的生成与回写响应头（`X-Request-ID`）都在那个中间件里，这里再造一个
    uuid 会让日志里的 request_id 和客户端手里的响应头对不上——而它们必须能互相对上，
    否则"把响应头贴给我我来查"就是一句空话。
    """
    if request is None:
        return None
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def _sse(event: str, payload: object) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _evidence(chunks: Sequence[RetrievedChunk]) -> list[Evidence]:
    """把检索命中摊平成门控输入（计划 §6.2）；没有现行版本的片段直接剔除。

    为什么要剔：`Evidence.active_document_version_id` 是非空 UUID（CIT-03 要求引用
    必须来自现行版），而 `RetrievedChunk` 上这个字段可以是 `None`（文档正在写入，或
    已撤下）。把 `None` 塞成一个哨兵 UUID 等于伪造版本号——后续"引用是否来自现行版"
    的等式判定会静默通过，而这正是这条字段存在的唯一理由。没有现行版本的内容撑不起
    一条可核验的引用，所以宁可不当依据。
    """
    evidence: list[Evidence] = []
    without_version = 0
    for chunk in chunks:
        version = chunk.active_document_version_id
        if version is None:
            without_version += 1
            continue
        evidence.append(
            Evidence(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                active_document_version_id=version,
                index_version=chunk.index_version,
                chunk_ordinal=chunk.chunk_ordinal,
                content=chunk.content,
                score=chunk.rrf_score,
                page_no=chunk.page_no,
                page_end_no=chunk.page_end_no,
                locator=chunk.locator,
            )
        )
    if without_version:
        logger.warning("chat drops chunks without active document version: %d", without_version)
    return evidence


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
    _kb: Annotated[Any, Depends(require_kb_access)],
    request: Request = None,  # type: ignore[assignment]
) -> StreamingResponse:
    """SSE 顺序固定为 retrieval、token*、done；检索沿用可信 Scope。

    `request` 的类型是 `Request` 而默认值是 `None`，这不是笔误：单测直接调用本函数
    （`tests/unit/test_rag_chat_events.py`）时没有 ASGI 请求可注入，而 FastAPI 对
    `Request` 注解的参数**不看默认值**、一律注入真实请求（`add_non_field_param_to_dependency`），
    所以这个缺省值只影响"直接调用"，不影响线上，也不必为了单测去伪造一个请求对象。
    """
    started = time.perf_counter()
    request_id = _request_id(request)
    # 一次请求一次绑定，`finally` 里必须复位。不绑：`retrieval_completed` 是在
    # `search_hybrid` 库内部发的，它的 request_id 会是 null，和 `chat_finished` 串不
    # 到一起（计划 §6 要的就是"按同一个 request_id 捞出四个事件"）。不复位：同一个
    # 连接上的下一次请求会继承上一轮的 request_id——比没有更糟，日志会指向别人的请求。
    ctx_token = bind_request_context(request_id) if request_id is not None else None
    try:
        state = get_app_state()
        if state.embedder is None:
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "embedding provider not configured")
        # 必须在构造 StreamingResponse **之前**校验：流一旦开始，状态码已经发出去，
        # 再抛错只能表现为连接被掐断，客户端会把"没配模型"误读成网络故障。
        # 真正的检查在 services/rag/answer.py 的生成器里（那里是最后一道），
        # 这里是把错误提前成可读的 503。
        if not settings.chat_api_key:
            raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "chat model is not configured")

        # 一次问答一份运行预算（plan §7.3 / T5.1），`run_id` 就是报告的关联键。
        run_id = uuid4().hex
        budget = RunBudget.from_settings(settings)
        budget_status = budget.blocked()
        chunks: list[RetrievedChunk] = []
        degraded: dict[str, str] = {}

        # 四段计时（计划 §6 / Day4 §8.3）：全部本进程 `perf_counter` 采，采不到的段留
        # `None`（序列化后是 null），**绝不补 0**——0 的含义是"测到了，且确实是 0"，
        # 与"没采到"是两件事；把后者写成 0 会让 p50 里混进一堆假样本。
        stage: dict[str, float | None] = {
            "retrieval_ms": None,
            "evidence_check_ms": None,
            "model_ttft_ms": None,
            "generation_ms": None,
        }
        retrieval_done_at: float | None = None
        answer_started_at: float | None = None
        first_token_at: float | None = None
        error_code: str | None = None
        finished_logged = False

        if budget_status is None:
            # 建流之前已判过一次预算，正常配置下这里不会抛；真抛出只可能是"判定通过到
            # 真正开始检索之间刚好跨过超时线"这种微秒级竞态，闸门里只有预算会抛。
            try:
                budget.begin_retrieval()
            except AppError:
                budget_status = budget.blocked()
                if budget_status is None:
                    # 只剩 cancelled（本轮没有任何地方会设置它）。既不是"超时"也不是
                    # "次数超限"，没有对应终态可写，按原样抛——此时流还没建，抛是合法的。
                    raise
                logger.warning("chat budget refused the first retrieval: %s", budget_status)
            else:
                outcome = await search_hybrid(
                    scope, kb_id, body.query, embedder=state.embedder, repo=repo, top_k=body.top_k
                )
                chunks = list(outcome.hits)
                degraded = dict(outcome.degraded)
                retrieval_done_at = time.perf_counter()
                stage["retrieval_ms"] = _elapsed_ms(started, retrieval_done_at)
                if degraded:
                    # 检索降级后仍会作答，但这必须让客户端知道：只靠一路召回时答案的
                    # 覆盖面明显更窄，用户据此下结论前应当看得见（见 schemas/query.py）。
                    logger.warning("chat retrieval degraded: %s", degraded)

        # 门控：先判"这批片段撑不撑得住这个问题"，再决定投喂什么。`assessment` 为 None
        # 时**不跑**门控，而不是跑一个空判——预算中止的那一轮根本没检索，把
        # `no evidence` 写进 done.verdict 会把"预算中止"误报成"知识库没资料"。
        assessment = assess(body.query, _evidence(chunks)) if budget_status is None else None
        # 客户端看到的 citations 必须和模型真正读到的资料是**同一批**：
        # `select_context_chunks` 传了 assessment 时先按 `supporting_chunk_ids` 过滤、
        # 再按 `chat_max_context_chunks` 截断。门控没放行的片段留在这儿就变成了
        # "召回到了"冒充"依据"——而 citations 的定义是后者（"引用必须是系统记录"）。
        context_chunks = (
            select_context_chunks(chunks, settings=settings, assessment=assessment)
            if assessment is not None
            else []
        )
        citations = [
            Citation(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                # 版本 ID 一路从 `v_active_chunks` 带到这里，引用才能证明自己来自
                # 现行版；中间任何一跳漏传，出参就永远是 null，判定退化成"人工比对"。
                active_document_version_id=chunk.active_document_version_id,
                page_no=chunk.page_no,
                page_end_no=chunk.page_end_no,
                # 结构化位置（第 3 页 / Sheet1!B2:C4 / 幻灯片 5 / 坐标框）：前端靠它把
                # 引用跳回原文的具体位置；缺失时只能退到 page_no。
                locator=chunk.locator,
                snippet=chunk.content[:500],
            ).model_dump(mode="json")
            for chunk in context_chunks
        ]
        if retrieval_done_at is not None:
            # 证据检查这一段到此为止：门控判定 + 上下文截断 + 引用组装。它的下界是
            # 检索返回，上界是"资料确定成型"，中间不含任何模型调用。
            stage["evidence_check_ms"] = _elapsed_ms(retrieval_done_at)

        def _mark_first_token() -> None:
            """记下第一条用户可见文本的时刻；只记第一次，后续调用直接返回。

            锚点是 `answer_started_at`（开始消费答案流），不是请求入口：`model_ttft_ms`
            要与客户端量到的 `chat_ttft_ms` 互校，两边都必须只含"模型思考"这一段。
            """
            nonlocal first_token_at
            if first_token_at is not None or answer_started_at is None:
                return
            first_token_at = time.perf_counter()
            stage["model_ttft_ms"] = _elapsed_ms(answer_started_at, first_token_at)

        def _finish_error_type(finish_status: RunStatus) -> str | None:
            """`chat_finished.error_type`：只回答"为什么没答成"，正常收尾一律 None。"""
            if finish_status is RunStatus.TIMEOUT:
                return "timeout"
            if finish_status is RunStatus.BUDGET_EXCEEDED:
                return "budget_exceeded"
            if finish_status is RunStatus.FAILED:
                # 有上游错误码就用它（answer.py 的 error 事件里带着 `ErrorCode`），
                # 没有说明是未预期异常，退到 `upstream_error`。
                return error_code or "upstream_error"
            return None

        def _log_chat_finished(
            *, finish_status: RunStatus, usage: dict[str, Any] | None, error: str | None
        ) -> None:
            """发一条 `chat_finished`，一次运行**恰好一条**。

            终态在 `_done` 里判，生成器里的未预期异常由 `except` 兜底；两条路都走这里，
            靠 `finished_logged` 保证不重复——重复的终态事件会让"每轮恰好一个
            chat_finished"这条验收彻底失效。
            """
            nonlocal finished_logged
            if finished_logged:
                return
            finished_logged = True
            if first_token_at is not None:
                stage["generation_ms"] = _elapsed_ms(first_token_at)
            log_event(
                "chat_finished",
                stage_timings=dict(stage),
                total_ms=_elapsed_ms(started),
                # token_usage 取不到就是 None（null），不写 0：0 个 token 是一次真实
                # 的采样结果，而"上游没回 usage"是没采到，两者在报表里不能合并。
                token_usage={
                    "prompt": (usage or {}).get("prompt_tokens"),
                    "completion": (usage or {}).get("completion_tokens"),
                },
                outcome=_OUTCOME_BY_STATUS.get(finish_status, "error"),
                error_type=error,
            )

        def _done(
            answer_text: str,
            usage: dict[str, Any] | None = None,
            *,
            status: RunStatus,
        ) -> DoneEvent:
            """收尾事件，顺带把"引用越界"与终态报出去。

            越界 = 模型引用了不存在的资料，或引用了被上下文上限截掉的资料。正文不能改
            （token 已经逐块发出去了），但计数必须让调用方拿到：否则一段带假 `[资料 9]`
            的答案会被当成有据可查。

            `status` 必填，且没有默认值：让"忘了判终态"直接变成 TypeError，而不是悄悄
            发一个 `COMPLETED` 出去（那等于把失败伪装成成功）。
            """
            invalid = invalid_cited_indices(answer_text, context_size=len(context_chunks))
            if invalid:
                logger.warning(
                    "chat answer cites sources outside context: indices=%s context_size=%d",
                    invalid,
                    len(context_chunks),
                )
            # 先落日志再建事件：`_log_chat_finished` 补算 `generation_ms`（它的锚点是
            # 首字时刻），必须在那之后再快照 `stage`，否则 `done` 里的 `generation_ms`
            # 恒为 None——而日志里却有值，同一个量两处对不上。
            _log_chat_finished(finish_status=status, usage=usage, error=_finish_error_type(status))
            # 引用分两份：`citations` 是模型读到的全部（`retrieval` 帧已经发过，当时答案
            # 还没生成），这里补的是正文自己用 `[资料 N]` 认领过的那几条。没有序号时
            # 退回全量——拒答、上游失败、模型漏标注这三种情况"用了几条"不可知，当成
            # "一条没用"会把有据可查的答案显示成无依据。
            cited = sorted(i for i in cited_indices(answer_text) if 1 <= i <= len(citations))
            used_citations = [citations[i - 1] for i in cited] or citations
            event = DoneEvent(
                **(usage or {}),
                invalid_citations=len(invalid),
                used_citations=used_citations,
                status=status,
                run_id=run_id,
                # verdict 只在本轮真跑过门控时才有意义；预算中止时留 None。
                verdict=str(assessment.verdict) if assessment is not None else None,
                # 五个耗时字段原样透出（含 None）：详情见 `DoneEvent` 的字段注释。
                retrieval_ms=stage["retrieval_ms"],
                evidence_check_ms=stage["evidence_check_ms"],
                model_ttft_ms=stage["model_ttft_ms"],
                generation_ms=stage["generation_ms"],
                total_ms=_elapsed_ms(started),
            )
            # run_id 只在这里落一行日志：一次问答几十个 token 事件，出问题时靠它对上
            # "哪一轮、什么终态、门控怎么判的"。
            logger.info(
                "chat run finished: run_id=%s status=%s verdict=%s invalid_citations=%d",
                run_id,
                status,
                event.verdict,
                len(invalid),
            )
            return event

        async def events() -> AsyncIterator[str]:
            nonlocal answer_started_at, error_code
            # 生成器在**端点返回之后**才被 Starlette 迭代，上面那次 bind 已经在
            # `finally` 里复位过了，所以这里要自己再绑一次、自己在 `finally` 里复位。
            # SSE 是长连接：漏掉复位就等于把这一轮的 request_id 留给下一个请求
            # （计划 §6 点名的串号风险），两段各自 bind/reset，谁也不依赖谁的执行时机。
            stream_token = bind_request_context(request_id) if request_id is not None else None
            try:
                # `degraded` 必须搭第一个事件出去：流开始后状态码就固定了，之后没有任何
                # 地方能表达"这次只召回了一路"。只写日志等于让流式调用方永远看不到降级。
                yield _sse(
                    "retrieval",
                    RetrievalEvent(
                        citations=citations,
                        degraded=degraded,
                        # 这个时刻只有第一段能算出来；后三段要到 done 才成立，所以这里
                        # 只带 retrieval_ms，不补 null 占位（详见 `RetrievalEvent`）。
                        retrieval_ms=stage["retrieval_ms"],
                    ).model_dump(mode="json"),
                )

                if budget_status is not None:
                    # 预算在检索前就失效：一次检索都没发，citations 必然是空的。但**不能空回**：
                    # 用户需要知道"不是没资料，是这次运行被预算中止了"，否则一条空答案看起来
                    # 和"知识库里没有相关内容"一模一样。
                    message = budget.stop_message(budget_status)
                    yield _sse("token", TokenEvent(text=message).model_dump())
                    yield _sse("done", _done(message, status=budget_status).model_dump(mode="json"))
                    return

                if budget.remaining() <= 0.0:
                    # 预算在**调用模型之前**就已经用光，最常见的原因是首次请求的冷启动：本地
                    # embedding 首次加载与首次编码合计要几十秒（2026-10-30 实测：冷进程 26.4s，
                    # 允许 HF 联网探测时 121.5s），这段时间全部记进本次运行的预算，于是模型
                    # 一次都没被调用过。
                    #
                    # 为什么不能沿用 `budget.stop_message(TIMEOUT)`：那句话写的是"已中止生成"，
                    # 而这里根本没有开始生成——把原因扣给模型会把排查方向带偏（当天正是这句
                    # 话让排查先怀疑 HTTP 层）。这里如实说明"检索阶段就用完了预算"并给出实测
                    # 耗时；日志与正文用同一个 elapsed，两边对得上。
                    elapsed = budget.elapsed()
                    logger.warning(
                        "chat budget exhausted before generation: "
                        "run_id=%s elapsed=%.3fs budget=%.3fs hits=%d",
                        run_id,
                        elapsed,
                        budget.timeout_seconds,
                        len(context_chunks),
                    )
                    message = (
                        f"本次运行的时间预算（{budget.timeout_seconds:g} 秒）"
                        f"在开始生成前就已用尽："
                        f"检索与模型预热合计耗时 {elapsed:.1f} 秒，因此没有调用语言模型。"
                        "首次请求可能包含模型预热，稍后重试通常更快；"
                        "也可以把问题缩小到更具体的条款后再试。"
                    )
                    yield _sse("token", TokenEvent(text=message).model_dump())
                    yield _sse(
                        "done",
                        _done(message, status=RunStatus.TIMEOUT).model_dump(mode="json"),
                    )
                    return

                answer: list[str] = []
                usage: dict[str, Any] | None = None
                status = RunStatus.COMPLETED
                stream = stream_answer(
                    body.query, context_chunks, settings=settings, assessment=assessment
                )
                # 模型段的起点：开始消费答案流。放在这之前会把 `stream_answer` 的
                # 参数组装算进去，虽然只有微秒，但没有理由让它污染 TTFT。
                answer_started_at = time.perf_counter()
                try:
                    while True:
                        # 超时只罩住"等下一块"这一步，且必须罩在 `anext` 上。换成
                        # `asyncio.timeout` 包住整个循环的话，计时器可能在生成器让出控制权
                        # （正在发 SSE 字节）时触发，取消落到 Starlette 的 ASGI send 上，
                        # 表现就是"答案发到一半连接断了"。罩住 `anext` 时取消点一定在上游的
                        # await 上，抛 TimeoutError 之后我们还有机会补一只中文 token 和 done。
                        try:
                            kind, value = await asyncio.wait_for(
                                anext(stream), timeout=budget.remaining()
                            )
                        except StopAsyncIteration:
                            break
                        except TimeoutError:
                            # 不在这里记首 token：这一段没有产出任何生成内容，把超时补的
                            # 那句提示当成 TTFT 会让"超时率高的那些请求看起来 TTFT 很好"。
                            status = RunStatus.TIMEOUT
                            message = budget.stop_message(status)
                            answer.append(message)
                            yield _sse("token", TokenEvent(text=message).model_dump())
                            break
                        if kind == "token":
                            _mark_first_token()
                            answer.append(value)
                            yield _sse("token", TokenEvent(text=value).model_dump())
                        elif kind == "refusal":
                            # 门控拒答：`message` 是给用户看的中文正文（"资料不足以支撑……" /
                            # "资料互相矛盾……"），照常作为一只 token 发出——前端不必为"拒答"
                            # 写第二套渲染。正文进 answer 是为了让越界计数看到**真发出去过的**
                            # 文本，而不是"模型说了什么"。
                            _mark_first_token()
                            message = str(value.get("message") or "")
                            answer.append(message)
                            yield _sse("token", TokenEvent(text=message).model_dump())
                            status = RunStatus.ABSTAINED
                        elif kind == "error":
                            # 流已开始，状态码改不了：错误作为事件发出去，再补一个 done 收尾，
                            # 前端的解析器才不会一直挂着等下一块。
                            error_code = str(value.get("code") or "") or None
                            yield _sse("error", StreamErrorEvent(**value).model_dump())
                            yield _sse(
                                "done",
                                _done("".join(answer), status=RunStatus.FAILED).model_dump(
                                    mode="json"
                                ),
                            )
                            return
                        elif kind == "usage":
                            usage = value
                        else:  # pragma: no cover - answer.py 只产出上面四类
                            logger.warning("chat ignores unknown answer event: %s", kind)
                except Exception as exc:
                    # 未预期异常（不是 answer.py 降级出来的 error 事件）。流已经开始了，
                    # 抛出去客户端只会看到断连；这里补一条终态事件再上抛，至少日志里留下
                    # "这一轮以什么异常结束"。done 无法再发（外层 finally 会收流），不伪造。
                    _log_chat_finished(
                        finish_status=RunStatus.FAILED,
                        usage=usage,
                        error=type(exc).__name__,
                    )
                    raise
                finally:
                    # 超时/提前返回时把上游生成器收掉：httpx 的连接要等 GC 才还回去，
                    # 长连接场景下这会把连接池拖死。
                    with suppress(RuntimeError):
                        await stream.aclose()

                if status is RunStatus.COMPLETED:
                    # 整体时长也要判一次：上游可能断续回到超过预算，正文已经发出去了，
                    # done 是唯一还能改口的地方（见 RunBudget.finish）。
                    status = budget.finish()
                if status is RunStatus.COMPLETED and _looks_like_model_refusal("".join(answer)):
                    # 模型自己拒答（"我不能导出连接串"/"我没有这样做"）：正文是拒答，
                    # 状态就不能是"答完了"。只降级不升级，理由见 `_REFUSAL_PREFIXES`。
                    #
                    # 已知局限（实测踩到，暂不修）：模型会写「我不能替你判断权限……
                    # 按资料原文，研发中心 2026 年度预算总额 1200 万元 [资料 1]」，
                    # 开头像拒答、正文却把数字念出来了。按前缀判成 ABSTAINED 会把泄漏
                    # 记成合格。试过「带 [资料 N] 引用就不算拒答」，但真拒答在解释理由时
                    # 也会引用资料编号，误伤后 abstain 从 0.909 掉到 0.636。
                    # 真正稳的解法是让模型显式吐拒答信号，而不是继续加文案判据。
                    status = RunStatus.ABSTAINED
                yield _sse(
                    "done",
                    _done("".join(answer), usage, status=status).model_dump(mode="json"),
                )
            finally:
                if stream_token is not None:
                    reset_request_context(stream_token)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    finally:
        # 与开头那次 bind 配对：无论 503 早退、异常上抛还是正常返回（SSE 尚未开始发），
        # 都必须把 contextvar 复位，不留给同一连接上的下一个请求。
        if ctx_token is not None:
            reset_request_context(ctx_token)
