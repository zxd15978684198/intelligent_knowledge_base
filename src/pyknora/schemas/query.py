"""成员3检索接口的数据形状。"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: 单次检索最多返回多少条片段。这是**资源边界**，不是产品调参项。
#:
#: 不封顶的后果是可放大的：`LIMIT :top_k` 直接进向量路与关键词路两条 SQL，
#: 融合阶段还要为每条候选建一条 `RetrievedChunk` 并序列化进响应体，
#: 所以 `top_k=100000` 会让一次请求同时放大数据库扫描、Python 内存与响应体积。
#: 服务层 `services/retrieval/search.py` 也读这个常量做同一道校验——schema 层
#: 挡住的是 HTTP 入口，服务层挡住的是脚本/内部调用，两层不是重复而是两个入口。
#:
#: 50 的依据：本项目 `chat_max_context_chunks` 默认 6，检索 UI 一页展示远小于 50；
#: 上限取 50 留出 8 倍余量，同时让"误传大数"立刻得到 422 而不是拖慢全服务。
MAX_TOP_K: Final[int] = 50


class QueryRequest(BaseModel):
    """知识库检索请求。"""

    model_config = ConfigDict(frozen=True)

    query: str = Field(
        min_length=1,
        max_length=1000,
        description=(
            "用户问题。上限 1000 字符是安全边界，不是产品限制："
            "关键词路会把汉字切成 bigram 送进 to_tsquery，"
            "而 to_tsquery 是递归下降解析，词元越多递归越深——"
            "实测 20000 字直接 stack depth limit exceeded（500）。"
            "仓库层另有 256 字符的二次封顶（src/pyknora/db/repos/reads.py），"
            "这里放宽到 1000 是为了不砍掉正常的长问句。"
            "空串同样拒绝（min_length=1）：空问题会走到 embedding 但语义为空，"
            "返回一堆无关片段比返回 422 更糟。"
        ),
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=MAX_TOP_K,
        description=(
            f"返回片段数上限（1..{MAX_TOP_K}）。"
            "这是资源边界：它会直接进两条 SQL 的 LIMIT，并决定响应体大小；"
            "超上限返回 422，不做静默截断——静默截断会让调用方以为自己拿到了 100 条。"
        ),
    )


class RetrievedChunk(BaseModel):
    """融合后的命中片段；保留引用所需字段，不暴露某一路的原始分数。"""

    model_config = ConfigDict(frozen=True)

    chunk_id: UUID
    workspace_id: UUID
    kb_id: UUID
    document_id: UUID
    index_version: int
    chunk_ordinal: int
    content: str
    page_no: int | None = None
    page_end_no: int | None = Field(
        default=None,
        description="末字符所在页；跨页片段 page_no < page_end_no。用于让引用回指一整个范围。",
    )
    heading_path: str | None = None
    locator_ref: str | None = None
    rrf_score: float
    vector_rank: int | None = None
    keyword_rank: int | None = None
    active_document_version_id: UUID | None = None
    """该片段所属文档的**现行**版本 ID（`documents.active_document_version_id`）。

    `index_version` 只说明"第几次索引"，它随重切片/换切片参数而变，与"文档改过
    几版"是两条轴：文档改到第 3 版之后，旧版内容仍可能带着一个看起来很正常的
    `index_version` 被检出来。把现行版本 ID 透出来，引用才能被机械判定为
    "来自现行版"——拿它与 `GET /documents/{id}` 的同名字段做等式即可。

    `None` 不是"没实现"：文档此时确实没有激活版本（写入中或已撤下）。
    默认值让既有构造点（测试、脚本）不必逐个补参数。
    """


class QueryResponse(BaseModel):
    """检索结果及其来源片段。"""

    model_config = ConfigDict(frozen=True)

    query: str
    results: list[RetrievedChunk]
    degraded: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "降级实情：空对象表示两路（向量 / 关键词）都成功。"
            "非空时键是失败的分支名、值是该分支的错误码。"
            "**必须透出给调用方**——本项目踩过「FTS 一路静默恒空」的坑："
            "少了这个字段，客户端无法区分「库里只有这些」和「一路挂了只剩一路」。"
        ),
    )
    retrieval_strategy: str = Field(
        default="baseline",
        description=(
            "本次检索**实际**走的策略（`baseline` / `candidate`）。"
            "A/B 是逐个样本归因的：报告里记的必须是这次真走的路，而不是查配置"
            "反推“它应该走的路”。缺了它，两个策略的结果混在一起就再也分不开。"
            "默认 `baseline` 让老调用方不带这个字段也能构造响应（线上行为不变）。"
        ),
    )
