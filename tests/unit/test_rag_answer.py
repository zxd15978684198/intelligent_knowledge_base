"""生成器阶段的失败必须降级成事件，不能抛。

`stream_answer` 是**响应开始之后**才被消费的异步生成器。它一旦抛异常，Starlette
会撞上 "Caught handled exception, but response already started"，客户端只看到连接
被掐断（`httpx.RemoteProtocolError`），既拿不到状态码也拿不到错误体。这三条用例
锁的就是这个约束：

1. 没配 key → 在**首个 token 之前**抛（此时还没进流式阶段，可以变成 503）；
2. 没检索到资料 → 不调用模型，直接给兜底话术；
3. 模型连不上 → 产出 `error` 事件再收尾，**不抛**。
"""

from __future__ import annotations

from uuid import UUID

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.settings import settings
from pyknora.schemas.query import RetrievedChunk
from pyknora.services.rag.answer import stream_answer

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


async def test_upstream_failure_becomes_error_event_not_exception() -> None:
    """连不上模型：降级成 error 事件。抛出去就是把连接掐断，客户端无从判断。"""
    stream = stream_answer("问题", [_chunk()], settings=_settings(chat_base_url=DEAD_URL))

    events = [event async for event in stream]

    assert [kind for kind, _ in events] == ["error", "usage"]
    payload = events[0][1]
    assert payload["code"] == ErrorCode.DEPENDENCY_UNAVAILABLE
    assert payload["retryable"] is True
    assert events[1][1] == {}
