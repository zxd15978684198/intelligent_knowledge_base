"""检索增强的流式答案生成（OpenAI 兼容 Chat Completions）。"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.settings import Settings
from pyknora.schemas.query import RetrievedChunk

# 模型被要求用 `[资料 N]` 标注依据（见 `_messages` 的 system prompt）。N 是**上下文
# 里的序号**而不是 chunk_id：模型看不到 UUID，也不可能准确复述它。所以校验也只能
# 校验序号是否落在本次实际投喂的资料条数内。
_CITATION_MARKER = re.compile(r"\[资料\s*(\d+)\]")


def select_context_chunks(
    chunks: Sequence[RetrievedChunk], *, settings: Settings
) -> list[RetrievedChunk]:
    """模型**真正看到**的那几条资料。

    上限只在 `chat_max_context_chunks` 一处定义，调用方必须拿同一个返回值去构建
    `retrieval` 事件的 citations。否则会给客户端列出一批模型从没见过的 chunk——
    `ChatRequest.top_k` 上限是 20、而上下文只取 6 时，多出来的 14 条就是**幻影引用**：
    客户端拿它们当"答案的依据"展示，实际模型根本没读到。
    """
    return list(chunks[: settings.chat_max_context_chunks])


def cited_indices(answer_text: str) -> set[int]:
    """回答里出现的 `[资料 N]` 序号集合。"""
    return {int(match.group(1)) for match in _CITATION_MARKER.finditer(answer_text)}


def invalid_cited_indices(answer_text: str, *, context_size: int) -> list[int]:
    """序号越界的引用：模型编了不存在的资料，或引用了被上下文截断掉的那几条。

    **只报告，不改正文**：token 是流式发出去的，回头改会让客户端看到的内容和
    "模型实际说了什么"不一致，排查时无法复原。越界条数是幻觉的硬信号，交给
    调用方去记日志并写进 `done` 事件。
    """
    return sorted(i for i in cited_indices(answer_text) if i < 1 or i > context_size)


def _messages(question: str, chunks: Sequence[RetrievedChunk]) -> list[dict[str, str]]:
    context = "\n\n".join(
        f"[资料 {index}] document_id={chunk.document_id}; page={chunk.page_no}\n{chunk.content}"
        for index, chunk in enumerate(chunks, start=1)
    )
    return [
        {
            "role": "system",
            "content": (
                "你是知识库问答助手。只依据提供的资料回答；资料不足时明确说不知道。"
                "在相关陈述后用 [资料 N] 标注依据，不得编造资料或引用。\n\n资料：\n" + context
            ),
        },
        {"role": "user", "content": question},
    ]


async def stream_answer(
    question: str,
    chunks: Sequence[RetrievedChunk],
    *,
    settings: Settings,
) -> AsyncIterator[tuple[str, Any]]:
    """产出 token 文本，最后产出 usage；网络调用按 OpenAI SSE 逐块读取。"""
    if not settings.chat_api_key:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "chat model is not configured")
    if not chunks:
        yield "token", "知识库中没有检索到相关资料，暂时无法回答。"
        yield "usage", {"prompt_tokens": 0, "completion_tokens": 0}
        return

    url = f"{settings.chat_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.chat_model,
        "messages": _messages(question, select_context_chunks(chunks, settings=settings)),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    try:
        async with (
            httpx.AsyncClient(timeout=settings.chat_timeout_s) as client,
            client.stream(
                "POST",
                url,
                headers={"Authorization": f"Bearer {settings.chat_api_key}"},
                json=payload,
            ) as response,
        ):
            response.raise_for_status()
            usage: dict[str, int] = {}
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if event.get("usage"):
                    usage = {
                        "prompt_tokens": event["usage"].get("prompt_tokens", 0),
                        "completion_tokens": event["usage"].get("completion_tokens", 0),
                    }
                for choice in event.get("choices", []):
                    text = choice.get("delta", {}).get("content")
                    if text:
                        yield "token", text
            yield "usage", usage
    except httpx.HTTPError:
        # 这里**不能抛**：生成器是在响应已经开始之后才跑的，抛出 AppError 会让
        # Starlette 撞上 "response already started"，客户端只看到连接被掐断
        # （httpx.RemoteProtocolError），拿不到任何可读的错误。
        # 所以把失败降级成一个 error 事件，由调用方发完再收尾。
        yield (
            "error",
            {
                "code": ErrorCode.DEPENDENCY_UNAVAILABLE,
                "message": "chat model request failed",
                "retryable": True,
            },
        )
        yield "usage", {}
