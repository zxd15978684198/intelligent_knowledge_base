"""SAFE-01/02/03/06/09：拒答、伪造引用与运行预算的可执行 API 用例。

覆盖讲义 SAFE 矩阵里"路由 + 门控 + 收尾事件"这一层：

- SAFE-01 无证据 → ``ABSTAINED``、``citations`` 为空、正文是明确拒答且不含任何金额；
- SAFE-02 只有主题没有金额 → ``ABSTAINED``，正文点名"缺少数值"而不是泛泛说不知道；
- SAFE-03 条款只适用于上层角色 → 不得把经理条款套到普通员工头上（``ABSTAINED``）；
- SAFE-06 模型伪造 ``[资料 99]`` → ``done.invalid_citations >= 1``，``citations`` 不含伪造 id；
- SAFE-09 超时 / 检索次数上限 → ``TIMEOUT`` / ``BUDGET_EXCEEDED``，正文给出预算解释。

打桩方式与 ``tests/api/test_api_chat.py`` 一致：只替换路由模块里的 ``search_hybrid``
与 ``stream_answer``，不连模型、不连检索；``assess`` 与 ``select_context_chunks`` 用
真实实现，这样断言的是"门控真的把片段挡在 citations 之外"，而不是自证打桩。

作者未在本地执行本文件（``tests/api`` 需要真 PG，由 Lead 串行跑）；只保证
``ruff check`` / ``ruff format --check`` 与 ``pytest --collect-only`` 通过。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID

import httpx
import pytest

from pyknora.api.v1 import chat as chat_routes
from pyknora.core.scope import WORKSPACE_A
from pyknora.core.settings import settings
from pyknora.schemas.chat import RunStatus
from pyknora.schemas.query import RetrievedChunk
from pyknora.services.rag.evidence import EvidenceAssessment, Verdict
from pyknora.services.retrieval.search import HybridSearchResult
from tests.api.tokens import KB_ID, bearer

CHAT_URL = f"/api/v1/knowledge-bases/{KB_ID}/chat"

# 带数值锚点的问题 + 覆盖该锚点的片段：门控判 SUPPORTED，用来验证伪造引用的计数。
_ANCHORED_QUESTION = "一线城市住宿报销上限是 500 元吗？"
_ANCHORED_CONTENT = "一线城市住宿报销上限为 500 元。"

# 只命中主题、没有任何数字：门控判 INSUFFICIENT（"该片段没有任何数字"）。
_TOPIC_ONLY_QUESTION = "一线城市住宿报销上限是多少？"
_TOPIC_ONLY_CONTENT = "一线城市住宿报销规定：员工出差应选择合标酒店，超标部分自理。"

# 条款只适用于经理，问题问的是普通员工：门控按"适用对象不匹配"剔除该片段。
_SCOPE_QUESTION = "普通员工年假有几天？"
_SCOPE_CONTENT = "年假制度仅适用于经理及以上人员，年假为 10 天。"

_VERSION_ID = UUID("77777777-7777-7777-7777-777777777777")


def _parse_sse(text: str) -> list[tuple[str, str]]:
    """把 SSE 响应体拆成 ``(event, data)`` 列表。"""
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


def _payloads(events: Sequence[tuple[str, str]], name: str) -> list[Any]:
    return [json.loads(data) for event, data in events if event == name]


def _single(events: Sequence[tuple[str, str]], name: str) -> Any:
    found = _payloads(events, name)
    assert len(found) == 1, f"expected exactly one {name!r} event, got {[n for n, _ in events]}"
    return found[0]


def _answer_text(events: Sequence[tuple[str, str]]) -> str:
    return "".join(payload["text"] for payload in _payloads(events, "token"))


def _chunk(index: int = 1, *, content: str = _ANCHORED_CONTENT) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=UUID(f"{index:08d}-1111-1111-1111-111111111111"),
        workspace_id=UUID(str(WORKSPACE_A)),
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


def _patch_retrieval(monkeypatch: pytest.MonkeyPatch, chunks: Sequence[RetrievedChunk]) -> None:
    """替换路由模块里的检索入口，注入固定命中。"""

    async def fake_search(
        scope: Any,
        kb_id: UUID,
        query: str,
        *,
        embedder: Any,
        repo: Any,
        top_k: int,
    ) -> HybridSearchResult:
        return HybridSearchResult(hits=list(chunks))

    monkeypatch.setattr(chat_routes, "search_hybrid", fake_search)


def _patch_answer(monkeypatch: pytest.MonkeyPatch, script: Sequence[tuple[str, Any]]) -> None:
    """替换模型流，按 ``(kind, value)`` 逐项吐出脚本内容。"""

    async def fake_answer(
        question: str,
        chunks: Sequence[RetrievedChunk],
        *,
        settings: Any,
        assessment: EvidenceAssessment | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        for item in script:
            yield item

    monkeypatch.setattr(chat_routes, "stream_answer", fake_answer)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """建流前的两道闸放行：有模型 key，且指向一个必然拒绝连接的回环端口。"""
    monkeypatch.setattr(settings, "chat_api_key", "test-key")
    monkeypatch.setattr(settings, "chat_base_url", "http://127.0.0.1:9/v1")


# --------------------------------------------------------------------- SAFE-01 无证据
async def test_safe_01_no_evidence_abstains_without_citations(
    client: httpx.AsyncClient, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有召回片段时：明确拒答、citations 为空、正文不出现任何金额。"""
    _patch_retrieval(monkeypatch, [])

    response = await client.post(CHAT_URL, json={"query": _TOPIC_ONLY_QUESTION}, headers=bearer())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    assert _single(events, "retrieval")["citations"] == []

    text = _answer_text(events)
    assert text, "拒答必须有可读正文，不能空回"
    assert "没有检索到" in text
    # 拒答不是"编一个数糊弄"：整段正文连阿拉伯数字都不该出现。
    assert re.search(r"\d", text) is None, text
    assert "元" not in text
    assert "500" not in text

    done = _single(events, "done")
    assert done["status"] == RunStatus.ABSTAINED
    assert done["verdict"] == Verdict.INSUFFICIENT
    assert done["invalid_citations"] == 0
    assert done["prompt_version"] is None
    assert _payloads(events, "error") == []


# ------------------------------------------------------- SAFE-02 部分相关（缺少数值）
async def test_safe_02_topic_only_evidence_abstains_and_names_the_gap(
    client: httpx.AsyncClient, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只命中主题、没有金额：拒答正文要说出缺什么，且不得把片段当依据。"""
    _patch_retrieval(monkeypatch, [_chunk(content=_TOPIC_ONLY_CONTENT)])

    response = await client.post(CHAT_URL, json={"query": _TOPIC_ONLY_QUESTION}, headers=bearer())
    assert response.status_code == 200, response.text

    events = _parse_sse(response.text)
    # 门控没放行的片段不能出现在 citations 里（"召回到了"不等于"有依据"）。
    assert _single(events, "retrieval")["citations"] == []

    text = _answer_text(events)
    assert "不足以支撑" in text
    assert "没有任何数字" in text, text
    assert "500" not in text
    assert "元" not in text

    done = _single(events, "done")
    assert done["status"] == RunStatus.ABSTAINED
    assert done["verdict"] == Verdict.INSUFFICIENT


# ------------------------------------------------- SAFE-03 适用对象不匹配（经理条款）
async def test_safe_03_scope_mismatch_does_not_apply_manager_clause(
    client: httpx.AsyncClient, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """经理条款不能套到普通员工身上：拒答并点名"适用对象不匹配"，数值不外泄。"""
    _patch_retrieval(monkeypatch, [_chunk(content=_SCOPE_CONTENT)])

    response = await client.post(CHAT_URL, json={"query": _SCOPE_QUESTION}, headers=bearer())
    assert response.status_code == 200, response.text

    events = _parse_sse(response.text)
    assert _single(events, "retrieval")["citations"] == []

    text = _answer_text(events)
    assert "适用对象不匹配" in text, text
    assert "10 天" not in text
    assert "年假为 10" not in text

    done = _single(events, "done")
    assert done["status"] == RunStatus.ABSTAINED
    assert done["verdict"] == Verdict.INSUFFICIENT


# ------------------------------------------------------------ SAFE-06 模型伪造引用
async def test_safe_06_forged_citation_marker_is_counted_not_accepted(
    client: httpx.AsyncClient, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[资料 99]`` 不存在：正文照发，但必须计数，且 citations 只含真实片段。"""
    chunk = _chunk(content=_ANCHORED_CONTENT)
    _patch_retrieval(monkeypatch, [chunk])
    _patch_answer(
        monkeypatch,
        [
            ("token", "一线城市住宿报销上限为 500 元[资料 1]，另见[资料 99]。"),
            ("usage", {"prompt_tokens": 12, "completion_tokens": 9}),
        ],
    )

    response = await client.post(CHAT_URL, json={"query": _ANCHORED_QUESTION}, headers=bearer())
    assert response.status_code == 200, response.text

    events = _parse_sse(response.text)
    citations = _single(events, "retrieval")["citations"]
    assert len(citations) == 1
    assert citations[0]["chunk_id"] == str(chunk.chunk_id)

    forged_id = str(UUID(f"{99:08d}-1111-1111-1111-111111111111"))
    assert all(citation["chunk_id"] != forged_id for citation in citations)

    text = _answer_text(events)
    # 正文不改写（token 已逐块发出），但伪造标记必须被看见。
    assert "[资料 99]" in text

    done = _single(events, "done")
    assert done["invalid_citations"] >= 1
    assert done["status"] == RunStatus.COMPLETED
    assert done["verdict"] == Verdict.SUPPORTED
    assert done["prompt_tokens"] == 12
    assert done["completion_tokens"] == 9


# ---------------------------------------------------- SAFE-09 预算在检索前就失效
async def test_safe_09_timeout_before_retrieval_explains_instead_of_answering(
    client: httpx.AsyncClient, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超时线在检索前已跨过：不发检索、正文给出预算解释、终态 TIMEOUT。"""
    monkeypatch.setattr(settings, "chat_budget_timeout_s", 0.0)

    async def must_not_search(*args: Any, **kwargs: Any) -> HybridSearchResult:
        raise AssertionError("budget already exhausted; retrieval must not run")

    monkeypatch.setattr(chat_routes, "search_hybrid", must_not_search)

    response = await client.post(CHAT_URL, json={"query": _TOPIC_ONLY_QUESTION}, headers=bearer())
    assert response.status_code == 200, response.text

    events = _parse_sse(response.text)
    assert _single(events, "retrieval")["citations"] == []

    text = _answer_text(events)
    assert text, "预算中止也要解释原因，不能空回"
    assert "预算" in text
    assert "秒" in text

    done = _single(events, "done")
    assert done["status"] == RunStatus.TIMEOUT
    # 这一轮根本没检索，verdict 留空，不能误报成"知识库没资料"。
    assert done["verdict"] is None
    assert done["prompt_version"] is None
    assert _payloads(events, "error") == []


async def test_safe_09_retrieval_call_cap_reports_budget_exceeded(
    client: httpx.AsyncClient, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """检索次数上限为 0：同样是检索前中止，但终态必须是 BUDGET_EXCEEDED。"""
    monkeypatch.setattr(settings, "chat_max_retrieval_calls", 0)

    async def must_not_search(*args: Any, **kwargs: Any) -> HybridSearchResult:
        raise AssertionError("retrieval call cap already hit; retrieval must not run")

    monkeypatch.setattr(chat_routes, "search_hybrid", must_not_search)

    response = await client.post(CHAT_URL, json={"query": _TOPIC_ONLY_QUESTION}, headers=bearer())
    assert response.status_code == 200, response.text

    events = _parse_sse(response.text)
    text = _answer_text(events)
    assert "检索次数上限" in text
    assert "次" in text

    done = _single(events, "done")
    assert done["status"] == RunStatus.BUDGET_EXCEEDED
    assert done["verdict"] is None
    assert _single(events, "retrieval")["citations"] == []


async def test_safe_09_mid_stream_timeout_closes_with_explanation(
    client: httpx.AsyncClient, wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """流已开始后超时：补一只解释 token 和 done(TIMEOUT)，不把连接掐断。"""
    monkeypatch.setattr(settings, "chat_budget_timeout_s", 0.2)
    _patch_retrieval(monkeypatch, [_chunk(content=_ANCHORED_CONTENT)])

    async def slow_answer(
        question: str,
        chunks: Sequence[RetrievedChunk],
        *,
        settings: Any,
        assessment: EvidenceAssessment | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        await asyncio.sleep(1.0)
        yield "token", "这句不该出现在正文里"

    monkeypatch.setattr(chat_routes, "stream_answer", slow_answer)

    response = await client.post(CHAT_URL, json={"query": _ANCHORED_QUESTION}, headers=bearer())
    assert response.status_code == 200, response.text

    events = _parse_sse(response.text)
    text = _answer_text(events)
    assert "预算" in text
    assert "秒" in text
    assert "这句不该出现在正文里" not in text

    done = _single(events, "done")
    assert done["status"] == RunStatus.TIMEOUT
    assert _payloads(events, "error") == []
