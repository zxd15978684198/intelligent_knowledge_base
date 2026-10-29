"""检索增强的流式答案生成（OpenAI 兼容 Chat Completions）。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.settings import Settings
from pyknora.schemas.query import RetrievedChunk


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
        "messages": _messages(question, chunks[: settings.chat_max_context_chunks]),
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
