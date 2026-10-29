"""成员3 chat 端点：SSE 事件顺序，以及"流已开始后失败"的降级。

这个文件存在的原因：成员3 的三条路由里，只有 chat 一直没有 API 测试，而它最容易
悄悄坏掉的地方不是检索，而是**流已经开始之后又出错**——那时状态码已经发出去了，
服务端再抛异常只会把连接掐断（客户端看到 `httpx.RemoteProtocolError`，拿不到任何
错误体）。所以这里断言的是事件序列，不是"HTTP 200"。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID

import pytest

from pyknora.api.v1 import chat as chat_routes
from pyknora.core.settings import settings
from pyknora.schemas.query import RetrievedChunk
from tests.api.tokens import KB_ID, bearer

CHAT_URL = f"/api/v1/knowledge-bases/{KB_ID}/chat"


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


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=UUID("11111111-1111-1111-1111-111111111111"),
        workspace_id=UUID("11111111-1111-1111-1111-111111111111"),
        kb_id=KB_ID,
        document_id=UUID("22222222-2222-2222-2222-222222222222"),
        index_version=1,
        chunk_ordinal=0,
        content="蓝鲸锚点：本段用于测试。",
        rrf_score=0.5,
        vector_rank=1,
        keyword_rank=1,
    )


@pytest.fixture
def fake_retrieval(monkeypatch: pytest.MonkeyPatch) -> list[RetrievedChunk]:
    """把检索替换成固定结果：本文件要测的是 SSE 组帧，不是检索算法。"""
    chunks = [_chunk()]

    async def _search(*args: Any, **kwargs: Any) -> list[RetrievedChunk]:
        return chunks

    monkeypatch.setattr(chat_routes, "search_hybrid", _search)
    return chunks


def _patch_answer(monkeypatch: pytest.MonkeyPatch, script: Sequence[tuple[str, Any]]) -> None:
    """用一段固定脚本替换模型调用，逐项回放。"""

    async def _answer(
        question: str, chunks: Sequence[RetrievedChunk], *, settings: Any
    ) -> AsyncIterator[tuple[str, Any]]:
        for kind, value in script:
            yield kind, value

    monkeypatch.setattr(chat_routes, "stream_answer", _answer)


async def test_chat_requires_chat_model(
    client: Any, fake_retrieval: list[RetrievedChunk], monkeypatch: pytest.MonkeyPatch
) -> None:
    """没配 key 必须在建流之前失败：否则客户端只看到断流，误判成网络问题。"""
    monkeypatch.setattr(settings, "chat_api_key", "")

    response = await client.post(CHAT_URL, headers=bearer(), json={"query": "问题"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "event-stream" not in response.headers.get("content-type", "")


async def test_chat_streams_retrieval_then_tokens_then_done(
    client: Any, fake_retrieval: list[RetrievedChunk], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chat_api_key", "test-key")
    _patch_answer(
        monkeypatch,
        [
            ("token", "蓝鲸"),
            ("token", "锚点"),
            ("usage", {"prompt_tokens": 3, "completion_tokens": 2}),
        ],
    )

    response = await client.post(CHAT_URL, headers=bearer(), json={"query": "问题"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert [name for name, _ in events] == ["retrieval", "token", "token", "done"]

    citations = json.loads(events[0][1])["citations"]
    assert citations[0]["chunk_id"] == str(_chunk().chunk_id)
    assert [json.loads(data)["text"] for name, data in events if name == "token"] == [
        "蓝鲸",
        "锚点",
    ]
    assert json.loads(events[-1][1]) == {"prompt_tokens": 3, "completion_tokens": 2}


async def test_chat_degrades_upstream_failure_into_error_event(
    client: Any, fake_retrieval: list[RetrievedChunk], monkeypatch: pytest.MonkeyPatch
) -> None:
    """上游挂了也要把错误送出去：error 事件 + done 收尾，连接不许断。"""
    monkeypatch.setattr(settings, "chat_api_key", "test-key")
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

    response = await client.post(CHAT_URL, headers=bearer(), json={"query": "问题"})

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [name for name, _ in events] == ["retrieval", "error", "done"]
    error = json.loads(events[1][1])
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert error["retryable"] is True


async def test_chat_requires_embedder(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """embedding 没注入时同样在建流前失败，而不是发出一个空的 retrieval。"""
    from pyknora.api.main import configure

    configure(kb_repo=None, embedder=None)
    try:
        response = await client.post(CHAT_URL, headers=bearer(), json={"query": "问题"})
    finally:
        from pyknora.db.repos.reads import FakeKbRepo
        from pyknora.services.ingest.embedder import HashingEmbedder

        configure(kb_repo=FakeKbRepo(), embedder=HashingEmbedder(dim=512))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
