"""成员3：SSE 流式 RAG 问答接口。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
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
    invalid_cited_indices,
    select_context_chunks,
    stream_answer,
)
from pyknora.services.rag.budget import RunBudget
from pyknora.services.rag.evidence import Evidence, assess
from pyknora.services.retrieval.search import search_hybrid

router = APIRouter(tags=["chat"], dependencies=[Depends(require_permission(CHAT_RUN))])

logger = logging.getLogger(__name__)


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

    # 一次问答一份运行预算（plan §7.3 / T5.1），`run_id` 就是报告的关联键。
    run_id = uuid4().hex
    budget = RunBudget.from_settings(settings)
    budget_status = budget.blocked()
    chunks: list[RetrievedChunk] = []
    degraded: dict[str, str] = {}
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
            snippet=chunk.content[:500],
        ).model_dump(mode="json")
        for chunk in context_chunks
    ]

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
        event = DoneEvent(
            **(usage or {}),
            invalid_citations=len(invalid),
            status=status,
            run_id=run_id,
            # verdict 只在本轮真跑过门控时才有意义；预算中止时留 None。
            verdict=str(assessment.verdict) if assessment is not None else None,
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
        # `degraded` 必须搭第一个事件出去：流开始后状态码就固定了，之后没有任何
        # 地方能表达"这次只召回了一路"。只写日志等于让流式调用方永远看不到降级。
        yield _sse(
            "retrieval",
            RetrievalEvent(citations=citations, degraded=degraded).model_dump(mode="json"),
        )

        if budget_status is not None:
            # 预算在检索前就失效：一次检索都没发，citations 必然是空的。但**不能空回**：
            # 用户需要知道"不是没资料，是这次运行被预算中止了"，否则一条空答案看起来
            # 和"知识库里没有相关内容"一模一样。
            message = budget.stop_message(budget_status)
            yield _sse("token", TokenEvent(text=message).model_dump())
            yield _sse("done", _done(message, status=budget_status).model_dump())
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
                f"本次运行的时间预算（{budget.timeout_seconds:g} 秒）在开始生成前就已用尽："
                f"检索与模型预热合计耗时 {elapsed:.1f} 秒，因此没有调用语言模型。"
                "首次请求可能包含模型预热，稍后重试通常更快；也可以把问题缩小到更具体的条款后再试。"
            )
            yield _sse("token", TokenEvent(text=message).model_dump())
            yield _sse("done", _done(message, status=RunStatus.TIMEOUT).model_dump())
            return

        answer: list[str] = []
        usage: dict[str, Any] | None = None
        status = RunStatus.COMPLETED
        stream = stream_answer(body.query, context_chunks, settings=settings, assessment=assessment)
        try:
            while True:
                # 超时只罩住"等下一块"这一步，且必须罩在 `anext` 上。换成
                # `asyncio.timeout` 包住整个循环的话，计时器可能在生成器让出控制权
                # （正在发 SSE 字节）时触发，取消落到 Starlette 的 ASGI send 上，
                # 表现就是"答案发到一半连接断了"。罩住 `anext` 时取消点一定在上游的
                # await 上，抛 TimeoutError 之后我们还有机会补一只中文 token 和 done。
                try:
                    kind, value = await asyncio.wait_for(anext(stream), timeout=budget.remaining())
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    status = RunStatus.TIMEOUT
                    message = budget.stop_message(status)
                    answer.append(message)
                    yield _sse("token", TokenEvent(text=message).model_dump())
                    break
                if kind == "token":
                    answer.append(value)
                    yield _sse("token", TokenEvent(text=value).model_dump())
                elif kind == "refusal":
                    # 门控拒答：`message` 是给用户看的中文正文（"资料不足以支撑……" /
                    # "资料互相矛盾……"），照常作为一只 token 发出——前端不必为"拒答"
                    # 写第二套渲染。正文进 answer 是为了让越界计数看到**真发出去过的**
                    # 文本，而不是"模型说了什么"。
                    message = str(value.get("message") or "")
                    answer.append(message)
                    yield _sse("token", TokenEvent(text=message).model_dump())
                    status = RunStatus.ABSTAINED
                elif kind == "error":
                    # 流已开始，状态码改不了：错误作为事件发出去，再补一个 done 收尾，
                    # 前端的解析器才不会一直挂着等下一块。
                    yield _sse("error", StreamErrorEvent(**value).model_dump())
                    yield _sse("done", _done("".join(answer), status=RunStatus.FAILED).model_dump())
                    return
                elif kind == "usage":
                    usage = value
                else:  # pragma: no cover - answer.py 只产出上面四类
                    logger.warning("chat ignores unknown answer event: %s", kind)
        finally:
            # 超时/提前返回时把上游生成器收掉：httpx 的连接要等 GC 才还回去，
            # 长连接场景下这会把连接池拖死。
            with suppress(RuntimeError):
                await stream.aclose()

        if status is RunStatus.COMPLETED:
            # 整体时长也要判一次：上游可能断续回到超过预算，正文已经发出去了，
            # done 是唯一还能改口的地方（见 RunBudget.finish）。
            status = budget.finish()
        yield _sse("done", _done("".join(answer), usage, status=status).model_dump())

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
