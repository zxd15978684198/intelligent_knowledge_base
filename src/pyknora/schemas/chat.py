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
    page_end_no: int | None = None
    """末字符所在页（P2-D2）。跨页片段 `page_no < page_end_no`。

    只给起始页时，前端跳到"第 3 页"却找不到引用的后半段——那段在第 4 页。
    两个字段都可能为 None：txt/md 类文档没有"页"这个概念，编一个 1 出来更糟。
    """
    snippet: str


class RetrievalEvent(BaseModel):
    citations: list[Citation]
    degraded: dict[str, str] = Field(default_factory=dict)
    """检索降级情况：键=分支名（`vector`/`keyword`），值=错误码。

    非空意味着这次回答**只用到了一路召回**（向量或关键词之一失败）。`/query`
    接口早就把这个信息放在 `QueryResponse.degraded` 里，但流式接口先前只把它
    写进服务端日志——同一次检索，非流式调用方看得见降级、流式调用方看不见。
    流一旦开始就不能改状态码，所以它必须随第一个 `retrieval` 事件一起发出去。
    """


class TokenEvent(BaseModel):
    text: str


class DoneEvent(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    invalid_citations: int = 0
    """回答里 `[资料 N]` 序号越界的条数。

    模型被要求在正文用 `[资料 N]` 标注依据，N 是**投喂给它的资料序号**。越界意味着
    它编了一份不存在的资料，或引用了被 `chat_max_context_chunks` 截掉的那几条——
    两者都是幻觉的硬信号。正文不改（流已经发出去了），但这件事不能只留在日志里，
    否则调用方会把一段带假引用的答案当成有据可查。
    """


class StreamErrorEvent(BaseModel):
    """流建立**之后**才发生的失败。

    这时 HTTP 状态码已经发出去了（通常是 200），改不了；再抛异常只会把连接掐断，
    客户端看到的是"网络错误"而不是"上游模型调用失败"。所以错误必须以事件形式
    送出去，且后面必须紧跟一个 done 事件收尾，否则前端解析器会一直等。
    """

    code: str
    message: str
    retryable: bool = False
