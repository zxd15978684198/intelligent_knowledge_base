"""成员3：流式问答请求与引用数据形状。"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=6, gt=0, le=20)


class Citation(BaseModel):
    chunk_id: UUID
    document_id: UUID
    page_no: int | None = None
    snippet: str


class RetrievalEvent(BaseModel):
    citations: list[Citation]


class TokenEvent(BaseModel):
    text: str


class DoneEvent(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
