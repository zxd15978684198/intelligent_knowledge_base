"""`locator` 必须真的出现在 HTTP 出参里，落库了不等于贯通。

pydantic v2 默认 `extra="ignore"`：模型上少一个字段，传进去的值被**静默丢弃**，
既不报错也不出现在响应里。`services/retrieval/rrf.py` 就留过这样一行注释——
`RetrievedChunk` 没有 `locator` 时，`locator=chunk.locator` 只是被 ignore 掉。
所以断言必须落在出参 JSON 上：`/query` 的 `results[].locator` 与 chat 的 SSE
`retrieval` 事件 `citations[].locator` 都要读到 `{"kind":"page","page":3}`；库里为
NULL 时出参是显式 `null`（字段缺失会让前端没法省掉 `?.`）。

本文件只走 HTTP，不跑 OCR：`chunks.locator` 直接用 SQL 写成 jsonb 字面量，
数据形状与 `splitter`/`ingest_worker` 真实产出的那一列一致（迁移 0014 第 23 列）。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.api.v1 import chat as chat_routes
from pyknora.core.scope import WORKSPACE_A, Scope
from pyknora.core.settings import settings
from pyknora.schemas.query import RetrievedChunk
from tests.api.tokens import KB_ID, bearer
from tests.integration.test_search_repo import _seed_one

QUERY_URL = f"/api/v1/knowledge-bases/{KB_ID}/query"
CHAT_URL = f"/api/v1/knowledge-bases/{KB_ID}/chat"

#: 与 `usage` 里真实产出同形（`answer.py` 会自己补 `prompt_version`，这里不需要）。
_PAGE_LOCATOR: dict[str, Any] = {"kind": "page", "page": 3, "inferred": False}

_SCOPE = Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A)


async def _seed_chunk(
    session: AsyncSession, *, content: str, locator: dict[str, Any] | None
) -> UUID:
    """插一条最小可用 active chunk，并按需把 `locator` 写成 jsonb。

    复用 `tests/integration/test_search_repo.py` 的造数手法（`documents` +
    `index_builds` + `chunks` 各一行），这里只补 0014 那一列：`_seed_one` 不写
    `locator`，新行落库就是 NULL——正好是同文件「出参是 null」那条要的形状。
    """
    document_id = await _seed_one(scope=_SCOPE, kb_id=KB_ID, session=session, text=content)
    if locator is not None:
        await session.execute(
            text(
                """
                UPDATE chunks
                   SET locator = CAST(:locator AS jsonb)
                 WHERE workspace_id = :ws AND document_id = :doc
                """
            ),
            {"locator": json.dumps(locator), "ws": WORKSPACE_A, "doc": document_id},
        )
        await session.commit()
    return document_id


def _parse_sse(body: str) -> list[tuple[str, str]]:
    """把 SSE 正文拆成 (event, data) 序列。

    故意本地再写一份（`test_api_chat.py` 里有同名函数）：该文件是另一个会话的
    交付物，从测试模块 import 私有工具会让两边的失败互相污染。
    """
    events: list[tuple[str, str]] = []
    event, datas = "message", []
    for raw in body.splitlines():
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


def _single(events: list[tuple[str, str]], name: str) -> dict[str, Any]:
    payloads = [json.loads(data) for event, data in events if event == name]
    assert len(payloads) == 1, f"expected exactly one {name!r}, got {[n for n, _ in events]}"
    return payloads[0]


async def test_query_returns_locator_as_structured_json(
    client: httpx.AsyncClient, clean_db: None, db_session: AsyncSession
) -> None:
    """`/query` 的 `results[0].locator` 必须是 0014 那一列原样的结构。

    先断库里的值（证明出参不是模型默认值），再断 HTTP 出参：少任何一跳，
    `{"kind":"page","page":3}` 都不会出现在响应体里。
    """
    document_id = await _seed_chunk(db_session, content="银狐锚点 定位出参", locator=_PAGE_LOCATOR)

    stored = (
        await db_session.execute(
            text("SELECT locator FROM v_active_chunks WHERE document_id = :doc"),
            {"doc": document_id},
        )
    ).scalar_one()
    assert stored == _PAGE_LOCATOR, f"库里的 locator 不是预期形状：{stored!r}"

    response = await client.post(
        QUERY_URL, headers=bearer(), json={"query": "银狐锚点", "top_k": 5}
    )

    assert response.status_code == 200, response.text
    hits = response.json()["results"]
    assert len(hits) == 1, hits
    hit = hits[0]
    assert hit["document_id"] == str(document_id)
    # 精确到值：只断 `"locator" in hit` 的话，字段落地但恒为默认值也会通过。
    assert hit["locator"] is not None
    assert hit["locator"]["kind"] == "page"
    assert hit["locator"]["page"] == 3
    assert hit["locator"]["inferred"] is False


async def test_query_locator_null_is_explicit_null_not_missing_key(
    client: httpx.AsyncClient, clean_db: None, db_session: AsyncSession
) -> None:
    """库里 `locator` 为 NULL 时，出参必须是 `"locator": null` 而不是没有这个键。

    这条决定前端能不能写 `hit.locator?.page`：字段缺失时可选链是必需的，
    显式 null 时它就只是噪音。`_seed_one` 不写 `locator`，新行天然是 NULL。
    """
    document_id = await _seed_chunk(db_session, content="银狐锚点 无位置", locator=None)

    response = await client.post(
        QUERY_URL, headers=bearer(), json={"query": "银狐锚点", "top_k": 5}
    )

    assert response.status_code == 200, response.text
    hits = response.json()["results"]
    assert len(hits) == 1, hits
    hit = hits[0]
    assert hit["document_id"] == str(document_id)
    assert "locator" in hit, "字段缺失（而不是 null）：前端无法省掉可选链"
    assert hit["locator"] is None


async def _fake_answer(
    question: str,
    chunks: Sequence[RetrievedChunk],
    *,
    settings: Any,
    assessment: Any = None,
) -> AsyncIterator[tuple[str, Any]]:
    """替掉模型调用：本用例要测的是引用里的 `locator`，不是上游生成。

    `chat.py` 按 `stream_answer(query, chunks, settings=..., assessment=...)` 调用，
    替身少收一个关键字就是 `TypeError` → 500，看起来像路由坏了。
    """
    yield "token", "答案[资料 1]"
    yield "usage", {"prompt_tokens": 1, "completion_tokens": 1}


async def test_chat_citations_carry_locator(
    client: httpx.AsyncClient,
    clean_db: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat 的引用同样带 `locator`，且来自真实检索 + 真实门控。

    `citations` 实际搭的是第一个 SSE 事件 `retrieval`，不是 `done`——
    `DoneEvent` 根本没有 citations 字段（见 `schemas/chat.py`）。这条用例按真实
    出参断言，顺带把这个位置差异钉在注释里，免得下次又去 `done` 里找。

    门控是真的：问句与片段都写同一个数值锚点 `500 元`，`assess` 判 `SUPPORTED`，
    citations 才非空；不这么做就要打桩 `assess`，而那样就证明不了链路。
    """
    document_id = await _seed_chunk(
        db_session, content="一线城市住宿报销上限为 500 元。", locator=_PAGE_LOCATOR
    )
    # `chat_api_key` 非空是建流前那道 503 判定要看的；模型调用被下面的替身接住，
    # 所以不需要可达的 base_url。
    monkeypatch.setattr(settings, "chat_api_key", "test-key")
    monkeypatch.setattr(chat_routes, "stream_answer", _fake_answer)

    response = await client.post(
        CHAT_URL,
        headers=bearer(),
        json={"query": "一线城市住宿报销上限是 500 元吗？"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert [name for name, _ in events] == ["retrieval", "token", "done"]

    citations = _single(events, "retrieval")["citations"]
    assert len(citations) == 1, citations
    citation = citations[0]
    assert citation["document_id"] == str(document_id)
    assert citation["locator"] is not None
    assert citation["locator"]["kind"] == "page"
    assert citation["locator"]["page"] == 3
    assert _single(events, "done")["status"] == "COMPLETED"
