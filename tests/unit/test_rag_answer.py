"""生成器阶段的失败必须降级成事件，不能抛。

`stream_answer` 是**响应开始之后**才被消费的异步生成器。它一旦抛异常，Starlette
会撞上 "Caught handled exception, but response already started"，客户端只看到连接
被掐断（`httpx.RemoteProtocolError`），既拿不到状态码也拿不到错误体。这四条用例
锁的就是这个约束：

1. 没配 key → 在**首个 token 之前**抛（此时还没进流式阶段，可以变成 503）；
2. 没检索到资料 → 不调用模型，直接给兜底话术；
3. 模型连不上 → 产出 `error` 事件再收尾，**不抛**；
4. 异常不是 `HTTPError`（如 `InvalidURL`）→ 同样产出 `error` 事件，**不抛**；
   这条有两个用例：一个直接构造该异常，一个复现真实触发源（`NO_PROXY` 含 `[::1]`）。
"""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.settings import settings
from pyknora.schemas.query import RetrievedChunk
from pyknora.services.rag.answer import (
    cited_indices,
    invalid_cited_indices,
    select_context_chunks,
    stream_answer,
)

# 9/tcp 是 discard 服务，本机不会有进程监听：连上去立刻被拒，
# 不依赖外网，也不会因为环境不同变成"超时 60 秒"。
DEAD_URL = "http://127.0.0.1:9/v1"


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=UUID("11111111-1111-1111-1111-111111111111"),
        workspace_id=UUID("11111111-1111-1111-1111-111111111111"),
        kb_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        document_id=UUID("22222222-2222-2222-2222-222222222222"),
        index_version=1,
        chunk_ordinal=0,
        content="蓝鲸锚点：本段用于测试。",
        page_no=1,
        rrf_score=0.5,
        vector_rank=1,
        keyword_rank=1,
    )


def _settings(**overrides: object):
    return settings.model_copy(
        update={"chat_api_key": "test-key", "chat_timeout_s": 3.0, **overrides}
    )


async def test_missing_key_raises_before_any_token() -> None:
    """没配模型属于"请求就该被拒"，不是"流里的错误"——所以这里必须抛。"""
    stream = stream_answer("问题", [_chunk()], settings=_settings(chat_api_key=""))

    with pytest.raises(AppError) as excinfo:
        await anext(stream)

    assert excinfo.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE


async def test_no_chunks_answers_without_calling_model() -> None:
    """空检索结果不该产生一次网络调用，也不该让前端拿到空流。"""
    stream = stream_answer("问题", [], settings=_settings(chat_base_url=DEAD_URL))

    events = [event async for event in stream]

    assert [kind for kind, _ in events] == ["token", "usage"]
    assert "没有检索到相关资料" in events[0][1]
    assert events[1][1] == {"prompt_tokens": 0, "completion_tokens": 0}


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """让"连不上上游"这条用例与本机代理设置无关。

    httpx 建 client 时会读 *_PROXY：本机 `NO_PROXY` 含 `[::1]` 会让它在建 client
    阶段就抛 `InvalidURL`，根本走不到"连接被拒"，测的就不是原本想测的东西了。
    """
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        for key in (name, name.lower()):
            monkeypatch.delenv(key, raising=False)


async def test_upstream_failure_becomes_error_event_not_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连不上模型：降级成 error 事件。抛出去就是把连接掐断，客户端无从判断。"""
    _clear_proxy_env(monkeypatch)
    stream = stream_answer("问题", [_chunk()], settings=_settings(chat_base_url=DEAD_URL))

    events = [event async for event in stream]

    assert [kind for kind, _ in events] == ["error", "usage"]
    payload = events[0][1]
    assert payload["code"] == ErrorCode.DEPENDENCY_UNAVAILABLE
    assert payload["retryable"] is True
    assert events[1][1] == {}


async def test_non_http_error_still_becomes_error_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """建 client 时的异常不是 `HTTPError`，同样不许穿出流。

    真实触发路径：本机 `NO_PROXY` 含 `[::1]`，httpx 0.28 解析它抛 `InvalidURL`，
    而 `InvalidURL` 直接继承 `Exception`——只接 `HTTPError` 的兜底漏掉它，
    客户端于是只看到连接被掐断（AGENTS §8 第 9 条）。
    """

    def _boom(*args: object, **kwargs: object) -> None:
        raise httpx.InvalidURL("Invalid port: ':1]'")

    monkeypatch.setattr("httpx.AsyncClient", _boom)
    stream = stream_answer("问题", [_chunk()], settings=_settings(chat_base_url=DEAD_URL))

    events = [event async for event in stream]

    assert [kind for kind, _ in events] == ["error", "usage"]
    assert events[0][1]["code"] == ErrorCode.INTERNAL_ERROR
    assert events[0][1]["retryable"] is True
    assert events[1][1] == {}


async def test_broken_no_proxy_does_not_break_the_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """复现真实触发源：`NO_PROXY` 含 `[::1]` 时流必须照常收尾，不许断连接。

    只断言"有 error 事件、没抛异常"，不断言错误码：httpx 若哪天修好 NO_PROXY 解析，
    这条会退化成"连接被拒"（DEPENDENCY_UNAVAILABLE），那同样是正确行为。
    """
    monkeypatch.setenv("NO_PROXY", "localhost,.localhost,127.0.0.1,127.0.0.0/8,[::1],::1")
    stream = stream_answer("问题", [_chunk()], settings=_settings(chat_base_url=DEAD_URL))

    events = [event async for event in stream]

    assert [kind for kind, _ in events] == ["error", "usage"]
    assert events[1][1] == {}


def test_select_context_chunks_caps_at_setting() -> None:
    """上下文条数只有 `chat_max_context_chunks` 一处定义。

    检索可以返回 20 条，模型只读 6 条；`retrieval` 事件的 citations 必须跟着只发 6 条，
    否则客户端会拿到一批模型从没见过的 chunk 并当成答案依据。
    """
    chunks = [_chunk() for _ in range(8)]

    selected = select_context_chunks(chunks, settings=_settings(chat_max_context_chunks=3))

    assert len(selected) == 3
    assert selected == chunks[:3]


def test_cited_indices_parses_markers() -> None:
    """`[资料 N]` 的序号要能取出来，容忍中间有空格。"""
    assert cited_indices("[资料 1] 与 [资料 2]、[资料  3]") == {1, 2, 3}
    assert cited_indices("没有任何标注") == set()


def test_invalid_cited_indices_flags_out_of_range() -> None:
    """越界引用必须被抓出来：N 超出本次投喂条数、或干脆是 0。"""
    text = "结论甲[资料 1]，结论乙[资料 7]，结论丙[资料 0]"

    assert invalid_cited_indices(text, context_size=3) == [0, 7]
    assert invalid_cited_indices(text, context_size=10) == [0]
    assert invalid_cited_indices("[资料 2]", context_size=2) == []
