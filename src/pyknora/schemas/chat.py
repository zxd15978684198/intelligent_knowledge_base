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


class StreamErrorEvent(BaseModel):
    """流建立**之后**才发生的失败。

    这时 HTTP 状态码已经发出去了（通常是 200），改不了；再抛异常只会把连接掐断，
    客户端看到的是"网络错误"而不是"上游模型调用失败"。所以错误必须以事件形式
    送出去，且后面必须紧跟一个 done 事件收尾，否则前端解析器会一直等。
    """

    code: str
    message: str
    retryable: bool = False
