"""成员3：一次问答的运行预算（计划 §7.3 / T5.1）。

为什么要有它：流式问答最贵的两件事——检索与生成——都没有天然上限。用户可以把
`top_k` 拉满（20 条），也可以问一个需要反复检索才能拼出答案的问题；上游模型卡住时
连接会一直挂着。没有预算的运行，代价由服务端单方面承担，客户端只看到"很慢"。

预算在这里只是一个**记账对象**，不含 IO、不含等待逻辑：它回答两个问题——
"还能不能再检索一次"（次数）与"还有多少时间可用"（时长）。真正的等待与超时在
`api/v1/chat.py` 的流转发循环里实现（那里才有 async 上下文）。

与 `services/rag/evidence.py` 一样保持纯计算：时间可以注入（每个方法都有 `now`
参数），所以边界测试不需要真的睡觉。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.settings import Settings
from pyknora.core.settings import settings as app_settings
from pyknora.schemas.chat import RunStatus


@dataclass(slots=True)
class RunBudget:
    """一次问答的预算记账。

    字段与计划 §7.3 的骨架一致；`started_at` 用**单调时钟**的秒数，见 `elapsed`。
    """

    started_at: float
    timeout_seconds: float
    max_retrieval_calls: int = 2
    retrieval_calls: int = 0
    cancelled: bool = False

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> RunBudget:
        """按当前配置开一次新预算（`started_at` 取此刻）。

        `settings` 缺省用模块级单例，测试可以直接传一个 `model_copy(update=...)`
        的对象来缩短超时/压低次数，不必改环境变量。
        """
        conf = settings if settings is not None else app_settings
        return cls(
            started_at=time.monotonic(),
            timeout_seconds=conf.chat_budget_timeout_s,
            max_retrieval_calls=conf.chat_max_retrieval_calls,
        )

    # ------------------------------------------------------------ 时间轴

    def elapsed(self, now: float | None = None) -> float:
        """从开跑到 `now` 的秒数；`now` 缺省取当前单调时钟。

        用 `time.monotonic()` 而不是 `time.time()`：后者会被 NTP 校正回拨，一次回拨
        就能让 `elapsed` 变负，超时判定于是永远不成立——线上表现是"预算静默失效"，
        比直接报错更难查。传进来的 `now` 也允许早于 `started_at`（测试会这么干），
        结果夹到 0，不产生负的已用时长。
        """
        current = time.monotonic() if now is None else now
        return max(0.0, current - self.started_at)

    def remaining(self, now: float | None = None) -> float:
        """剩余时间（秒），已超时的返回 0.0 而不是负数。

        负数直接喂给 `asyncio.wait_for` 会立即超时，语义上没问题，但把它当成"还能等
        多久"读会得出荒唐的结论；夹到 0 之后，"<= 0 就是没得等"这条判断才成立。
        """
        return max(0.0, self.timeout_seconds - self.elapsed(now))

    def timed_out(self, now: float | None = None) -> bool:
        """是否已用满时长预算。

        用 `>=` 而不是 `>`：刚好等于 `timeout_seconds` 时留给上游的余量已经是 0 秒，
        再"等一小会儿"必然超预算，所以这一瞬间就算超时。
        """
        return self.elapsed(now) >= self.timeout_seconds

    # ------------------------------------------------------------ 次数

    @property
    def calls_exhausted(self) -> bool:
        """下一次检索是否已不被允许。

        同样是 `>=`：`max_retrieval_calls=2` 时用掉 2 次意味着第 3 次超限，而不是
        "用掉 2 次的这一轮已经超限"——否则正常答完的一轮会被误判成
        `BUDGET_EXCEEDED`。
        """
        return self.retrieval_calls >= self.max_retrieval_calls

    # ------------------------------------------------------------ 闸门

    def blocked(self, now: float | None = None) -> RunStatus | None:
        """**不抛异常**的判定：返回"现在不能继续"的终态；放行则 `None`。

        为什么与 `check()` 并存：流一旦开始，异常会撞 Starlette 的
        "response already started"，客户端只看到连接被掐断、连错误体都没有
        （AGENTS §8 第 9 条）。所以流**外**用 `check()`（能提前判的必须提前抛），
        流**内**只能用本方法，把结论降级成 `done.status`。

        `cancelled` 返回 `None`：`CANCELLED` 不在 `RunStatus` 里（见其 docstring），
        断连处理归 T5.2；本方法只保证"取消了就不再放行新的检索"，由 `check()` 落实。
        """
        if self.timed_out(now):
            return RunStatus.TIMEOUT
        if self.calls_exhausted:
            return RunStatus.BUDGET_EXCEEDED
        return None

    def check(self, now: float | None = None) -> None:
        """在新一次检索**之前**调用：已取消 / 超时 / 超次数 → 抛 `AppError`。

        错误码选 `INTERNAL_ERROR` 的理由（这一段是决策记录，不是装饰）：

        - `INVALID_REQUEST`(422) 说的是**请求形状**不合法。预算耗尽时请求本身是合法
          的，是这一次运行把服务端资源用超了；打成 422 会让客户端回去查参数，方向错。
        - `CONFLICT`(409) 是并发状态冲突（重名、重放幂等键、旧版本），与时间、次数
          无关，套上去只会让人以为要重读一遍再重试。
        - `DEPENDENCY_UNAVAILABLE`(503) 是上游依赖坏了。超时勉强沾边，但"检索次数
          用完"根本不是上游故障；把两种原因挤进一个码，观测时分不清是模型慢还是我们
          自己不放行。
        - `INTERNAL_ERROR`(500) 的语义是"服务端没能完成这次请求"，且 `core/errors.py`
          把它标成 `retryable=True`——预算拒绝确实属于"换个更短的问题或稍后再试可能
          成功"这一类，方向对。

        根治是新增一个 `BUDGET_EXCEEDED` 错误码，但那要改 `core/errors.py`（17 个码
        是登记在 `docs/tdd/07` 的跨模块契约）并同步文档，属于跨模块变更，不在本轮
        单写者范围；已作为建议写进交付简报。

        另：这个异常**不会**以 HTTP 500 落到客户端——`api/v1/chat.py` 在建流前就把预算
        结论降级成 `done.status`，`details` 只给日志与测试看。
        """
        reason: str | None = None
        if self.cancelled:
            reason = "cancelled"
        elif self.timed_out(now):
            reason = "timeout"
        elif self.calls_exhausted:
            reason = "retrieval_calls"
        if reason is None:
            return
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            f"run budget refused the next retrieval ({reason})",
            details={
                "reason": reason,
                "retrieval_calls": self.retrieval_calls,
                "max_retrieval_calls": self.max_retrieval_calls,
                "timeout_seconds": self.timeout_seconds,
                "elapsed_seconds": round(self.elapsed(now), 3),
            },
        )

    def begin_retrieval(self, now: float | None = None) -> None:
        """记一次检索：先过闸门再计数。

        先 `check()` 再 `+1`，被拒绝的那一次不算"用掉一次"——否则连续两次拒绝会把
        计数推高，日志里就说不清到底是第几次被拦的。
        """
        self.check(now)
        self.retrieval_calls += 1

    def cancel(self) -> None:
        """标记本次运行已被放弃（客户端断连等）；标记后 `check()` 一律拒绝。"""
        self.cancelled = True

    # ------------------------------------------------------------ 收尾

    def finish(self, now: float | None = None) -> RunStatus:
        """答完之后判终态：只有"整体超时"能把 `COMPLETED` 改判成 `TIMEOUT`。

        为什么收尾还要再判一次时长：`chat_timeout_s`(60s) 比预算(25s) 大，而上游可能
        断续地回 token——正文都已经发出去了，`done` 是唯一还能改口的地方。一次答了
        40 秒的回答即使完整，也已经超出这次运行被允许的时长，状态必须如实写。
        """
        return RunStatus.TIMEOUT if self.timed_out(now) else RunStatus.COMPLETED

    def stop_message(self, status: RunStatus) -> str:
        """预算拒绝/超时时发给用户的正文（中文，保证非空）。

        `done.status` 只在流末尾，且是一个枚举值；正文里不写人话解释，用户看到的就是
        "一条什么也没说的回答"。预算中止必须被解释清楚，这是"不许空回"的落实点。
        """
        if status is RunStatus.TIMEOUT:
            return (
                f"本次回答超过运行预算的 {self.timeout_seconds:g} 秒，已中止生成，"
                "没有在预算内给出完整答案。可以把问题缩小到更具体的条款后再试。"
            )
        if status is RunStatus.BUDGET_EXCEEDED:
            return (
                f"本次问答已达到运行预算的检索次数上限（{self.max_retrieval_calls} 次），"
                "为避免继续扩大开销，不再发起新的检索，因此这次不给答案。"
            )
        raise ValueError(f"no stop message for status {status!r}")
