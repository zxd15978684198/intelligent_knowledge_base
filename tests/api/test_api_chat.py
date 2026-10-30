"""成员3 chat 端点：SSE 事件顺序、门控三态终态，以及"流已开始后失败"的降级。

这个文件存在的原因：成员3 的三条路由里，只有 chat 一直没有 API 测试，而它最容易
悄悄坏掉的地方不是检索，而是**流已经开始之后又出错**——那时状态码已经发出去了，
服务端再抛异常只会把连接掐断（客户端看到 `httpx.RemoteProtocolError`，拿不到任何
错误体）。所以这里断言的是事件序列，不是"HTTP 200"。

D15 之后契约变了三处，本文件跟着改：

1. `done` 带回 `status`（`RunStatus`）与 `run_id`。断言从"整字典相等"改成**字段级**：
   新增一个字段不该让每条用例都红一次，但每个字段的语义必须各自被锁住。
2. `retrieval.citations` 不再等于"召回到了什么"，而是"门控放行且真被投喂给模型的
   那几条"（`select_context_chunks` 先按 `supporting_chunk_ids` 过滤再截断）。所以
   造数据要么真能过门控，要么显式断言子集关系——**不能**假设"召回 8 条就有 8 条引用"。
3. `chat.py` 现在会先调 `assess(...)`：`stream_answer` 的替身也必须收下 `assessment`
   这个关键字参数，否则请求会以 `TypeError` 变成 500，而不是以断言失败的形式报出来。

`assess` 默认**不打桩**：门控的三种结论（放行 / 不充分 / 冲突）都用真实片段造出来，
断言才能证明"实现真的按门控分流"，而不是"按我写的桩分流"。只有需要精确控制"放行
哪几条"的那一条用例才打桩，并在注释里说明理由。

不真的联网：`stream_answer` 要么被打桩，要么走**门控拒答**分支（该分支在 `httpx`
之前 return）。`chat_base_url` 仍被指向回环 discard 端口，只做防御——万一哪条用例
走漏了，也是立刻 `ECONNREFUSED` 降级成 `error` 事件，不会打公网、不会挂到
`chat_timeout_s`（60s）。
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from pyknora.api.v1 import chat as chat_routes
from pyknora.core.scope import WORKSPACE_A
from pyknora.core.settings import settings
from pyknora.schemas.chat import RunStatus
from pyknora.schemas.query import RetrievedChunk
from pyknora.services.rag.answer import PROMPT_VERSION
from pyknora.services.rag.evidence import EvidenceAssessment, Verdict
from pyknora.services.retrieval.search import HybridSearchResult
from tests.api.tokens import KB_ID, bearer

CHAT_URL = f"/api/v1/knowledge-bases/{KB_ID}/chat"

_VERSION_ID = UUID("77777777-7777-7777-7777-777777777777")
"""`Citation.active_document_version_id` 的值：由 `v_active_chunks` 一路原样透出。

门控会**丢掉**没有现行版本的片段（`chat._evidence` 里 `None` 直接跳过），所以测试
数据必须带上它；漏掉的话 citations 恒为空，用例会以"门控判不充分"的形式变绿或变红，
但都不是它本来要守的东西。
"""

_ANCHORED_QUESTION = "一线城市住宿报销上限是 500 元吗？"
"""带数值锚点（`500 元`）的问题：片段里写得有同一个数，真实门控才判 `SUPPORTED`。

用"问题"这种无锚点问句会走 `SUPPORTED` 的退化分支（要覆盖 ≥2 个主题片段且片段里
真有数字），结果偏向 `INSUFFICIENT`——那些用例断言的 citations 就全空了。
"""

_ANCHORED_CONTENT = "一线城市住宿报销上限为 500 元。"

CHAT_BODY: dict[str, Any] = {"query": _ANCHORED_QUESTION}

_UNREACHABLE_BASE_URL = "http://127.0.0.1:9/v1"
"""回环 discard 端口：真走到 httpx 会立刻失败，而不是连公网挂满 60 秒超时。"""


def _parse_sse(text: str) -> list[tuple[str, str]]:
    """把 SSE 正文拆成 (event, data) 序列。"""
    events: list[tuple[str, str]] = []
    event = "message"
    datas: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line:
            if datas:
                events.append((event, "\n".join(datas)))
            event, datas = "message", []
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            datas.append(line[len("data:") :].lstrip())
    if datas:
        events.append((event, "\n".join(datas)))
    return events


def _payloads(events: list[tuple[str, str]], name: str) -> list[dict[str, Any]]:
    """指定名字的事件的 data 体（已反序列化）。"""
    return [json.loads(data) for event, data in events if event == name]


def _single(events: list[tuple[str, str]], name: str) -> dict[str, Any]:
    """取唯一一只该名字的事件；0 只或多只都直接失败并打印真实序列。"""
    found = _payloads(events, name)
    assert len(found) == 1, f"expected exactly one {name!r} event, got {[n for n, _ in events]}"
    return found[0]


def _answer_text(events: list[tuple[str, str]]) -> str:
    """把所有 token 事件拼回正文——客户端看到的就是这个。"""
    return "".join(payload["text"] for payload in _payloads(events, "token"))


def _chunk(index: int = 1, *, content: str = _ANCHORED_CONTENT) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=UUID(f"{index:08d}-1111-1111-1111-111111111111"),
        workspace_id=WORKSPACE_A,
        kb_id=KB_ID,
        document_id=UUID(f"{index:08d}-2222-2222-2222-222222222222"),
        index_version=1,
        chunk_ordinal=0,
        content=content,
        rrf_score=0.5,
        vector_rank=1,
        keyword_rank=1,
        active_document_version_id=_VERSION_ID,
    )


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """等价于"配好了模型"，但不指向任何真实模型。

    `chat_api_key` 非空是建流前那道 503 判定要看的；`chat_base_url` 只做防御（见模块
    docstring）。这两项每个用例都要设，所以收进夹具——比在 8 个用例里各抄两行可靠。
    """
    monkeypatch.setattr(settings, "chat_api_key", "test-key")
    monkeypatch.setattr(settings, "chat_base_url", _UNREACHABLE_BASE_URL)


@pytest.fixture
def fake_retrieval(monkeypatch: pytest.MonkeyPatch) -> list[RetrievedChunk]:
    """把检索替换成固定结果：本文件要测的是 SSE 组帧，不是检索算法。"""
    chunks = [_chunk()]

    async def _search(*args: Any, **kwargs: Any) -> HybridSearchResult:
        return HybridSearchResult(hits=chunks)

    monkeypatch.setattr(chat_routes, "search_hybrid", _search)
    return chunks


def _patch_answer(
    monkeypatch: pytest.MonkeyPatch,
    script: Sequence[tuple[str, Any]],
    *,
    seen: list[int] | None = None,
) -> None:
    """用一段固定脚本替换模型调用，逐项回放。

    `assessment` 必须收下：`chat.py` 现在按
    `stream_answer(query, context_chunks, settings=..., assessment=...)` 调用，替身
    少一个关键字参数就是 `TypeError` → 500，看起来像路由坏了。`settings` 同名遮蔽
    模块级导入是无害的（这里根本不用它）。
    """

    async def _answer(
        question: str,
        chunks: Sequence[RetrievedChunk],
        *,
        settings: Any,
        assessment: Any = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        if seen is not None:
            seen.append(len(chunks))
        for kind, value in script:
            yield kind, value

    monkeypatch.setattr(chat_routes, "stream_answer", _answer)


async def test_chat_requires_chat_model(
    client: Any, fake_retrieval: list[RetrievedChunk], monkeypatch: pytest.MonkeyPatch
) -> None:
    """没配 key 必须在建流之前失败：否则客户端只看到断流，误判成网络问题。

    `chat_base_url` 也指向不可达地址：这条 503 与 URL 无关（缺 key 的判定在发起任何
    网络请求之前）。如果实现把它挪进生成器，客户端拿到的会是 200 + `error` 事件，
    这条用例就会红——这正是用例要守住的位置。
    """
    monkeypatch.setattr(settings, "chat_api_key", "")
    monkeypatch.setattr(settings, "chat_base_url", _UNREACHABLE_BASE_URL)

    response = await client.post(CHAT_URL, headers=bearer(), json=CHAT_BODY)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "event-stream" not in response.headers.get("content-type", "")


async def test_chat_streams_retrieval_then_tokens_then_done(
    client: Any, fake_retrieval: list[RetrievedChunk], wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """顺序固定 retrieval → token* → done；done 的字段逐个断言（不整字典比较）。

    `usage` 的脚本体照着 `services/rag/answer.py` 真实产出写：上游发的是
    `{"prompt_tokens", "completion_tokens", "prompt_version"}`，`prompt_version` 由
    answer.py 自己补（`PROMPT_VERSION`）。这里断言它原样进了 done——"同一问题两次
    答案不一样"时，才分得清是模型抖动还是提示词被换过。
    """
    _patch_answer(
        monkeypatch,
        [
            ("token", "蓝鲸[资料 1]"),
            ("token", "锚点"),
            (
                "usage",
                {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "prompt_version": PROMPT_VERSION,
                },
            ),
        ],
    )

    response = await client.post(CHAT_URL, headers=bearer(), json=CHAT_BODY)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert [name for name, _ in events] == ["retrieval", "token", "token", "done"]

    retrieval = _single(events, "retrieval")
    citations = retrieval["citations"]
    assert [citation["chunk_id"] for citation in citations] == [str(fake_retrieval[0].chunk_id)]
    # 新增字段：版本 ID 从 v_active_chunks 一路带到出参（CIT-03 的机械判定靠它）。
    assert citations[0]["active_document_version_id"] == str(_VERSION_ID)
    assert retrieval["degraded"] == {}

    assert [payload["text"] for payload in _payloads(events, "token")] == [
        "蓝鲸[资料 1]",
        "锚点",
    ]

    done = _single(events, "done")
    assert done["status"] == RunStatus.COMPLETED
    assert done["verdict"] == Verdict.SUPPORTED
    assert done["prompt_tokens"] == 3
    assert done["completion_tokens"] == 2
    assert done["prompt_version"] == PROMPT_VERSION
    assert done["invalid_citations"] == 0
    assert re.fullmatch(r"[0-9a-f]{32}", done["run_id"]), done["run_id"]


async def test_chat_degrades_upstream_failure_into_error_event(
    client: Any, fake_retrieval: list[RetrievedChunk], wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上游挂了也要把错误送出去：error 事件 + done 收尾，连接不许断。

    终点是 `done.status=FAILED`：`status` 是必填字段，就是为了让"失败"不能靠缺省值
    伪装成 `COMPLETED`。脚本里 error 之后仍跟一条 usage，是照着真实 answer.py 的产出
    顺序写的——但 `chat.py` 在 error 处就 return 了，所以 usage 读不到，`prompt_tokens`
    必须还是 `None`。
    """
    _patch_answer(
        monkeypatch,
        [
            (
                "error",
                {
                    "code": "DEPENDENCY_UNAVAILABLE",
                    "message": "chat model request failed",
                    "retryable": True,
                },
            ),
            ("usage", {}),
        ],
    )

    response = await client.post(CHAT_URL, headers=bearer(), json=CHAT_BODY)

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [name for name, _ in events] == ["retrieval", "error", "done"]

    assert _single(events, "error") == {
        "code": "DEPENDENCY_UNAVAILABLE",
        "message": "chat model request failed",
        "retryable": True,
    }
    assert _answer_text(events) == ""
    done = _single(events, "done")
    assert done["status"] == RunStatus.FAILED
    assert done["prompt_tokens"] is None


async def test_chat_citations_match_what_the_model_actually_read(
    client: Any, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """检索 8 条、上下文只取 6 条时，citations 必须也只发 6 条。

    否则客户端会拿模型从没读过的 chunk 当"答案的依据"展示——这是幻影引用，
    比少列几条严重得多。8 条命中都写了问句里的 `500 元`，所以真实门控全放行，
    这里的差异只来自 `chat_max_context_chunks` 的截断。
    """
    chunks = [_chunk(i) for i in range(1, 9)]
    seen: list[int] = []

    async def _search(*args: Any, **kwargs: Any) -> HybridSearchResult:
        return HybridSearchResult(hits=chunks)

    monkeypatch.setattr(chat_routes, "search_hybrid", _search)
    _patch_answer(monkeypatch, [("token", "答案"), ("usage", {})], seen=seen)
    # 显式写死 6：默认值来自 .env 可覆盖的配置，用例不该随部署环境变。
    monkeypatch.setattr(settings, "chat_max_context_chunks", 6)

    response = await client.post(CHAT_URL, headers=bearer(), json={**CHAT_BODY, "top_k": 8})

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert len(_single(events, "retrieval")["citations"]) == 6
    assert seen == [6]


async def test_chat_citations_are_only_the_gated_supporting_chunks(
    client: Any, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """引用只含门控放行的片段，不是全部召回（D15 语义变化的落点）。

    这里显式打桩 `assess` 是刻意的：要精确控制"只放行第 2 条"，用真实门控得凑语料，
    还会把本用例的失败原因混进门控本身。桩**必须**返回真的 `EvidenceAssessment`——
    `chat.py` 会读 `.verdict` 与 `.supporting_chunk_ids`，返回 Mock 会在
    `select_context_chunks` 里以很怪的方式炸掉。
    """
    hits = [
        _chunk(1, content="与问题无关的片段。"),
        _chunk(2),
        _chunk(3, content="另一段无关文字。"),
    ]
    captured: list[Any] = []

    async def _search(*args: Any, **kwargs: Any) -> HybridSearchResult:
        return HybridSearchResult(hits=hits)

    def fake_assess(question: str, evidence: Sequence[Any]) -> EvidenceAssessment:
        captured.append((question, list(evidence)))
        return EvidenceAssessment(
            verdict=Verdict.SUPPORTED,
            supporting_chunk_ids=(hits[1].chunk_id,),
            reason="用例固定只放行第 2 段",
        )

    monkeypatch.setattr(chat_routes, "search_hybrid", _search)
    monkeypatch.setattr(chat_routes, "assess", fake_assess)
    _patch_answer(monkeypatch, [("token", "答案"), ("usage", {})])

    response = await client.post(CHAT_URL, headers=bearer(), json=CHAT_BODY)

    events = _parse_sse(response.text)
    citations = _single(events, "retrieval")["citations"]
    # 先断子集关系（引用绝不能超出召回集），再断"恰好是放行的那一条"：
    # 只断子集会让"三条全列出来"也通过，那正是旧语义。
    assert {citation["chunk_id"] for citation in citations} <= {str(hit.chunk_id) for hit in hits}
    assert [citation["chunk_id"] for citation in citations] == [str(hits[1].chunk_id)]

    question, evidence = captured[0]
    assert question == _ANCHORED_QUESTION
    # 门控拿到的是 `Evidence`（字段从 RetrievedChunk 摊平），版本号必须已经带上。
    assert [item.chunk_id for item in evidence] == [hit.chunk_id for hit in hits]
    assert all(item.active_document_version_id == _VERSION_ID for item in evidence)


async def test_chat_gate_refusal_when_evidence_is_insufficient(
    client: Any, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """门控判 INSUFFICIENT：不调模型，发中文拒答，终态 ABSTAINED，citations 为空。

    问句带数值锚点（`500 元`）而片段没写这个数：真实 `assess` 判不够，
    `select_context_chunks` 于是返回空。"召回到一段主题相关的文字"不等于"有依据"，
    所以 citations 必须空——发出去就会变成"召回冒充引用"。

    这条**不打桩** `stream_answer`：拒答分支在 `httpx` 之前 return，既证明没联网，
    也证明"没调模型"（真调了会在 `_UNREACHABLE_BASE_URL` 上失败成 error 事件）。
    """
    chunks = [_chunk(1, content="本文与差旅报销无关。")]

    async def _search(*args: Any, **kwargs: Any) -> HybridSearchResult:
        return HybridSearchResult(hits=chunks)

    monkeypatch.setattr(chat_routes, "search_hybrid", _search)

    response = await client.post(CHAT_URL, headers=bearer(), json=CHAT_BODY)

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [name for name, _ in events] == ["retrieval", "token", "done"]
    assert _single(events, "retrieval")["citations"] == []

    text = _answer_text(events)
    assert re.search(r"[\u4e00-\u9fff]", text), text
    assert "不足" in text

    done = _single(events, "done")
    assert done["status"] == RunStatus.ABSTAINED
    # `status` 只说"拒答了"，`verdict` 才说清是没依据还是依据矛盾。
    assert done["verdict"] == Verdict.INSUFFICIENT
    # 拒答分支 yield 的是空 usage，说明没走到模型。
    assert done["prompt_tokens"] is None
    assert done["prompt_version"] is None


async def test_chat_gate_refusal_when_evidence_conflicts(
    client: Any, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """门控判 CONFLICTING：正文指出冲突、两处一起列出，不替用户裁决。

    两条片段对同一事实（住宿报销上限）给出不同取值：真实 `assess` 判冲突，终态
    `ABSTAINED`。`citations` 此时是"必须一起摆出来"的两处，不是"可信依据"。
    """
    left = _chunk(1, content="住宿报销上限为 500 元。")
    right = _chunk(2, content="住宿报销上限为 800 元。")
    chunks = [left, right]

    async def _search(*args: Any, **kwargs: Any) -> HybridSearchResult:
        return HybridSearchResult(hits=chunks)

    monkeypatch.setattr(chat_routes, "search_hybrid", _search)

    response = await client.post(CHAT_URL, headers=bearer(), json={"query": "住宿报销上限是多少？"})

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [name for name, _ in events] == ["retrieval", "token", "done"]

    text = _answer_text(events)
    assert "冲突" in text, text

    assert [citation["chunk_id"] for citation in _single(events, "retrieval")["citations"]] == [
        str(left.chunk_id),
        str(right.chunk_id),
    ]
    done = _single(events, "done")
    assert done["status"] == RunStatus.ABSTAINED
    assert done["verdict"] == Verdict.CONFLICTING


async def test_chat_reports_citations_outside_context(
    client: Any, fake_retrieval: list[RetrievedChunk], wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回答引用了不存在的资料时，done 事件要带上越界条数。

    正文改不了（token 已经发出去了），但"这段答案带了假引用"必须能被调用方看见。
    越界判据是模型**实际看到的**条数（`context_size`），不是召回条数。
    """
    _patch_answer(
        monkeypatch,
        [("token", "结论甲[资料 1]，结论乙[资料 9]"), ("usage", {"completion_tokens": 4})],
    )

    response = await client.post(CHAT_URL, headers=bearer(), json=CHAT_BODY)

    events = _parse_sse(response.text)
    assert [name for name, _ in events] == ["retrieval", "token", "done"]
    assert _single(events, "done")["invalid_citations"] == 1
    assert "[资料 9]" in _answer_text(events)


async def test_chat_run_id_is_per_request_and_only_in_done(
    client: Any, fake_retrieval: list[RetrievedChunk], wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_id` 是**本次请求**的关联键，按真实实现只出现在 `done`。

    `chat.py` 的 `_done()` 是唯一填它的地方：`retrieval` 与 `token` 的事件体里没有
    这个字段（不要断"三个事件都有同一个 run_id"）。两次请求必须拿到不同的值，
    否则"按 run_id 对齐日志/评测报告"就是假的。
    """
    _patch_answer(monkeypatch, [("token", "答案"), ("usage", {})])

    first = await client.post(CHAT_URL, headers=bearer(), json=CHAT_BODY)
    second = await client.post(CHAT_URL, headers=bearer(), json=CHAT_BODY)

    first_events = _parse_sse(first.text)
    first_done = _single(first_events, "done")
    assert re.fullmatch(r"[0-9a-f]{32}", first_done["run_id"]), first_done["run_id"]
    assert first_done["run_id"] != _single(_parse_sse(second.text), "done")["run_id"]

    # 同一请求内它只落在 done：其余事件的载荷里没有这个键。
    assert "run_id" not in _single(first_events, "retrieval")
    for token in _payloads(first_events, "token"):
        assert "run_id" not in token


async def test_chat_requires_embedder(
    client: Any, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没注入 embedder：建流**之前**就 503。

    只替掉 chat 路由看到的那一份 app state，不动进程级 `configure()`：
    `require_kb_access` 读的是 `api/deps` 里自己的引用，KB 校验照常发生，503 才确实
    来自 embedder 缺失（否则用例会先撞上"KB 仓库没配"的 503，以绿的形式失去它该守
    的东西，还会把全局状态留给后面的用例）。
    """
    monkeypatch.setattr(chat_routes, "get_app_state", lambda: SimpleNamespace(embedder=None))

    response = await client.post(CHAT_URL, headers=bearer(), json=CHAT_BODY)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "embedding" in error["message"]
    assert "event-stream" not in response.headers.get("content-type", "")


async def test_chat_on_foreign_kb_is_404(client: Any) -> None:
    """跨租户/不存在的 kb 在**建流之前**回 404：流一旦开始就改不了状态码。"""
    response = await client.post(
        f"/api/v1/knowledge-bases/{UUID('99999999-9999-9999-9999-999999999999')}/chat",
        headers=bearer(),
        json=CHAT_BODY,
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert "event-stream" not in response.headers.get("content-type", "")


async def test_chat_surfaces_retrieval_degradation(
    client: Any, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """检索降级必须随第一个事件出去，不能只留服务端日志。

    同一次检索，`/query` 的调用方拿得到 `QueryResponse.degraded`，流式调用方
    此前拿不到——它只能从"答案比平时窄"里猜。降级是关键信息（只召回了一路），
    SSE 一旦开始就改不了状态码，所以唯一的出口就是 `retrieval` 事件。
    """

    async def _degraded_search(*args: Any, **kwargs: Any) -> HybridSearchResult:
        return HybridSearchResult(hits=[_chunk()], degraded={"vector": "DEPENDENCY_UNAVAILABLE"})

    monkeypatch.setattr(chat_routes, "search_hybrid", _degraded_search)
    _patch_answer(monkeypatch, [("usage", {})])

    response = await client.post(CHAT_URL, headers=bearer(), json=CHAT_BODY)

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[0][0] == "retrieval"
    assert _single(events, "retrieval")["degraded"] == {"vector": "DEPENDENCY_UNAVAILABLE"}


async def test_chat_retrieval_event_is_not_degraded_by_default(
    client: Any, fake_retrieval: list[RetrievedChunk], wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正常检索时 `degraded` 是空对象——调用方按 `degraded == {}` 判断"这次是全的"。"""
    _patch_answer(monkeypatch, [("usage", {})])

    response = await client.post(CHAT_URL, headers=bearer(), json=CHAT_BODY)

    events = _parse_sse(response.text)
    assert _single(events, "retrieval")["degraded"] == {}
    assert _single(events, "done")["status"] == RunStatus.COMPLETED
