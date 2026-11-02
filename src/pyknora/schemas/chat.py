"""成员3：流式问答请求与引用数据形状。"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pyknora.schemas.locator import Locator


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
    locator: Locator | None = None
    """结构化位置（`chunks.locator`，迁移 0014），让前端能精确跳到
    页 / 工作表单元格 / 幻灯片 / 坐标框，而不是只能给一句 snippet。

    与上面的 `page_no` 不重复：页码只是 `page`/`region` 这两种 kind 的投影，
    电子表格要的是 `B2:C4`、PPTX 要的是幻灯片序号。`None` 表示来源没有位置信息
    （老数据、或解析器没读到），此时前端退回只展示 `snippet`。
    """
    snippet: str
    active_document_version_id: UUID | None = None
    """该引用所属文档的**现行**版本 ID，用来机械判定"引用是否来自现行版"。

    只记 `document_id` 时，旧版与新版的引用长得一模一样：链接能打开、片段也在，
    但它可能来自已退休的版本。有了这个字段，判定就是一次等式比较（与
    `GET /documents/{id}` 的同名字段），不需要人眼去核内容。缺省 `None` 只出现在
    文档尚无激活版本时，不是"没查"。
    """


class RetrievalEvent(BaseModel):
    citations: list[Citation]
    degraded: dict[str, str] = Field(default_factory=dict)
    """检索降级情况：键=分支名（`vector`/`keyword`），值=错误码。

    非空意味着这次回答**只用到了一路召回**（向量或关键词之一失败）。`/query`
    接口早就把这个信息放在 `QueryResponse.degraded` 里，但流式接口先前只把它
    写进服务端日志——同一次检索，非流式调用方看得见降级、流式调用方看不见。
    流一旦开始就不能改状态码，所以它必须随第一个 `retrieval` 事件一起发出去。
    """

    retrieval_ms: float | None = None
    """检索这一段（请求进来到检索返回）的耗时，毫秒。

    只有这一段能在 `retrieval` 事件时算出来；另外三段要到 `done` 才成立，所以
    这个事件里只带这一段——给四项都填 `null` 会让"没到那一步"看起来像"没测"。
    `null` 表示未采到，**不是 0**：0 的含义是"测到了，且确实是 0 毫秒"。
    """


class TokenEvent(BaseModel):
    text: str


class RunStatus(StrEnum):
    """一次问答（一个 `run_id`）的终态，随 `done.status` 发给客户端。

    取值是计划 §7.3 七态里**本轮真能产出**的五种。`CANCELLED`（客户端断连）与
    `RUNNING`（开始态）本轮**不做**，所以它们不在这里：

    - `RUNNING` 会诱导调用方写 `if status == "RUNNING"`——`done` 是收尾事件，这个分支
      永远为假；真要表达"进行中"，正确位置是 `retrieval` 事件或新增一个 start 事件，
      而不是给终态枚举塞一个非终态。
    - `CANCELLED` 需要感知客户端断连（`is_disconnected`）并在断连时补一个 `done`，
      可流已经没人读了，补出去的事件没有接收方；这条要等 T5.2 的断连处理一起做，
      现在加进枚举只会让"能不能被产出"和"枚举里有没有"对不上。

    用 `StrEnum` 而不是裸字符串：它可以直接进 `done.status` / 日志而不再做一次映射
    （`str` 子类会被 JSON 序列化成字符串本身），与 `services/rag/evidence.Verdict`
    同一套写法。
    """

    COMPLETED = "COMPLETED"
    """正常答完（门控放行、上游没报错）。"""

    ABSTAINED = "ABSTAINED"
    """门控拒答（`INSUFFICIENT` / `CONFLICTING`）：有 `token` 正文，但那是一句说明，
    不是答案。"""

    TIMEOUT = "TIMEOUT"
    """超出运行预算的时长上限（`chat_budget_timeout_s`）。"""

    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    """检索次数超出运行预算（`chat_max_retrieval_calls`）。"""

    FAILED = "FAILED"
    """上游模型/网关失败：流里同时有一个 `error` 事件。"""


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

    status: RunStatus
    """本次问答的终态（见 `RunStatus`）。**必填**：缺省成 `COMPLETED` 等于让"没判过"
    伪装成"答完了"，这是这里唯一不能有默认值的字段。"""

    run_id: str
    """本次问答的标识（`uuid4().hex`）。

    日志、`done.status`、评测报告靠它对上是同一次请求。流式接口没有 request_id
    之外的关联键：一次问答可能发几十个 `token` 事件，出问题时只能凭时间戳猜是哪一轮。
    """

    prompt_version: str | None = None
    """实际使用的提示词版本（取自 `usage.prompt_version`）。

    没有它，"同一个问题两次答案不一样"就分不清是模型抖动还是提示词被换过；缺省
    `None` 表示这次没走到模型（预算拒绝、空检索、门控拒答）。
    """

    verdict: str | None = None
    """门控结论（`Verdict` 的字符串值），没跑门控时为 `None`。

    `status=ABSTAINED` 只说"拒答了"，`verdict` 才说清是**没依据**
    （`INSUFFICIENT`）还是**依据互相矛盾**（`CONFLICTING`）——两者的处置完全不同
    （补资料 vs 摆出两处引用）。
    """

    retrieval_ms: float | None = None
    """检索这一段（请求进来到检索返回）的耗时，毫秒。

    与 `retrieval` 事件里的同名字段是**同一个采样点**，两处都发是为了让只收 `done`
    的消费方（离线评测脚本按事件名过滤）也能拿到分阶段耗时，不必回头找前面的帧。
    """

    evidence_check_ms: float | None = None
    """证据门控 + 引用组装这一段（检索返回到引用列表定稿）的耗时，毫秒。"""

    model_ttft_ms: float | None = None
    """首字延迟：开始消费答案流 → 第一条正文 token，毫秒。

    超时分支**不采**这一段（`None`）：把"等超时的那几十秒"记成 TTFT 会把最该看的
    指标污染成看起来正常的数字。
    """

    generation_ms: float | None = None
    """首字之后到收尾的生成耗时，毫秒。没走到模型时为 `None`。"""

    total_ms: float | None = None
    """从请求进来到发 `done` 的总耗时，毫秒。

    四段之和**小于**它是正常的：段与段之间还有调度、序列化、SSE 写出的开销，实测
    量级在 1–2 毫秒。判据写成 `abs(total - sum) <= max(0.05 * total, 2ms)`，别用
    固定百分比卡极小请求。

    五个耗时字段一律 `null` 表示**没采到**，**不是 0**：0 的意思是"测到了，确实是
    0 毫秒"。把没采到写成 0，会让报表里出现一批"快得不讲道理"的样本。
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
