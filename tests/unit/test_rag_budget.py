"""运行预算的边界（计划 §7.3 / T5.1；实现在 `services/rag/budget.py`）。

六类断言：

1. `from_settings` 只读这两个配置项，计数从 0 起；
2. **刚好用满**次数：第 `max` 次之前放行，用满之后的下一次拒绝——`>=` 的边界，
   判成 `>` 会让"用掉 2 次就超限"，正常跑完的一轮被误报成 `BUDGET_EXCEEDED`；
3. **刚好等于** `timeout_seconds` 即算超时，剩余时间夹到 0 而不是负数；
4. 被拒绝的那一次**不计数**，否则日志里说不清到底是第几次被拦的；
5. `cancelled` 后一律拒绝，但 `blocked()` 返回 `None`：`CANCELLED` 不在
   `RunStatus` 里（枚举只留本轮真能产出的终态），取消走 `check()`；
6. 错误码与 `details` 是给日志/测试的事实：`INTERNAL_ERROR`（500、可重试）的选择理由
   写在 `budget.py` 的 `check()` docstring 里，这里只锁行为。

全部用例都显式传 `now`，不真的睡觉——预算是纯计算，测试也应当是纯计算。
"""

from __future__ import annotations

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.settings import settings
from pyknora.schemas.chat import RunStatus
from pyknora.services.rag.budget import RunBudget

# 固定起点，避免 `time.monotonic()` 让断言依赖真实时钟。
_START = 100.0


def _budget(
    *,
    timeout_seconds: float = 25.0,
    max_retrieval_calls: int = 2,
    retrieval_calls: int = 0,
) -> RunBudget:
    return RunBudget(
        started_at=_START,
        timeout_seconds=timeout_seconds,
        max_retrieval_calls=max_retrieval_calls,
        retrieval_calls=retrieval_calls,
    )


# ---------------------------------------------------------------- 配置


def test_from_settings_reads_only_the_two_budget_fields() -> None:
    conf = settings.model_copy(
        update={"chat_budget_timeout_s": 12.5, "chat_max_retrieval_calls": 3}
    )
    budget = RunBudget.from_settings(conf)
    assert budget.timeout_seconds == 12.5
    assert budget.max_retrieval_calls == 3
    assert budget.retrieval_calls == 0
    assert budget.cancelled is False


# ---------------------------------------------------------------- 次数边界


def test_calls_just_below_the_limit_are_allowed() -> None:
    budget = _budget(max_retrieval_calls=2)
    budget.begin_retrieval(now=_START)
    # 用掉 1 次，还剩 1 次：这一步必须放行，否则"允许 2 次"实际只允许 1 次。
    budget.check(now=_START)
    assert budget.retrieval_calls == 1


def test_calls_exactly_at_the_limit_refuse_the_next_retrieval() -> None:
    budget = _budget(max_retrieval_calls=2)
    budget.begin_retrieval(now=_START)
    budget.begin_retrieval(now=_START)
    assert budget.retrieval_calls == 2
    assert budget.calls_exhausted is True

    with pytest.raises(AppError) as excinfo:
        budget.check(now=_START)

    assert excinfo.value.code == ErrorCode.INTERNAL_ERROR
    assert excinfo.value.http_status == 500
    assert excinfo.value.details["reason"] == "retrieval_calls"
    assert excinfo.value.details["retrieval_calls"] == 2
    assert excinfo.value.details["max_retrieval_calls"] == 2


def test_refused_retrieval_does_not_consume_a_call() -> None:
    budget = _budget(max_retrieval_calls=1, retrieval_calls=1)
    with pytest.raises(AppError):
        budget.begin_retrieval(now=_START)
    # 被拒绝的那次不算"用掉"：否则连续两次拒绝会把计数推高，日志里说不清第几次被拦。
    assert budget.retrieval_calls == 1


# ---------------------------------------------------------------- 超时边界


def test_exactly_at_the_deadline_counts_as_timed_out() -> None:
    budget = _budget(timeout_seconds=25.0)
    # 刚好等于：留给上游的余量已经是 0 秒，再等必然超预算。
    assert budget.elapsed(now=_START + 25.0) == 25.0
    assert budget.timed_out(now=_START + 25.0) is True
    assert budget.remaining(now=_START + 25.0) == 0.0


def test_just_before_the_deadline_still_has_room() -> None:
    budget = _budget(timeout_seconds=25.0)
    now = _START + 24.999
    assert budget.timed_out(now=now) is False
    assert budget.remaining(now=now) > 0.0


def test_overtime_raises_timeout_and_clamps_remaining() -> None:
    budget = _budget(timeout_seconds=25.0)
    now = _START + 30.0
    assert budget.timed_out(now=now) is True
    assert budget.remaining(now=now) == 0.0

    with pytest.raises(AppError) as excinfo:
        budget.check(now=now)

    assert excinfo.value.details["reason"] == "timeout"
    assert excinfo.value.details["elapsed_seconds"] == 30.0


def test_elapsed_clamps_negative_clock_skew_to_zero() -> None:
    # 传入比 started_at 更早的 now（测试/时钟异常）时不许出现负的已用时长：
    # 负值会让 `remaining` 变大，超时判定失效。
    budget = _budget(timeout_seconds=25.0)
    assert budget.elapsed(now=_START - 5.0) == 0.0
    assert budget.remaining(now=_START - 5.0) == 25.0


# ---------------------------------------------------------------- 取消


def test_cancelled_budget_refuses_everything() -> None:
    budget = _budget()
    budget.cancel()
    assert budget.cancelled is True

    with pytest.raises(AppError) as excinfo:
        budget.check(now=_START)
    assert excinfo.value.details["reason"] == "cancelled"

    # 取消后 `blocked()` 仍是 None：没有对应的终态可写（CANCELLED 不进枚举），
    # 所以"取消"只能由 check() 拒绝，不能伪装成一个 done.status。
    assert budget.blocked(now=_START) is None


# ---------------------------------------------------------------- 非抛版判定与收尾


def test_blocked_maps_to_the_wire_status() -> None:
    assert _budget().blocked(now=_START) is None
    assert _budget(timeout_seconds=1.0).blocked(now=_START + 1.0) is RunStatus.TIMEOUT
    assert _budget(max_retrieval_calls=0).blocked(now=_START) is RunStatus.BUDGET_EXCEEDED


def test_finish_only_downgrades_completed_to_timeout() -> None:
    within = _budget(timeout_seconds=25.0)
    assert within.finish(now=_START + 3.0) is RunStatus.COMPLETED

    overtime = _budget(timeout_seconds=25.0)
    assert overtime.finish(now=_START + 25.0) is RunStatus.TIMEOUT


def test_stop_message_is_non_empty_chinese_for_budget_endings() -> None:
    budget = _budget(timeout_seconds=25.0, max_retrieval_calls=2)

    timeout_text = budget.stop_message(RunStatus.TIMEOUT)
    exceeded_text = budget.stop_message(RunStatus.BUDGET_EXCEEDED)

    # 预算中止必须给人话解释：`done.status` 只是枚举，正文空着会被读成"知识库没资料"。
    assert "25" in timeout_text  # 秒数要写出来，否则用户不知道等多久算超
    assert "2" in exceeded_text  # 次数上限同理
    for text in (timeout_text, exceeded_text):
        assert text.strip()
        assert any("\u4e00" <= char <= "\u9fff" for char in text)


def test_stop_message_rejects_statuses_it_has_no_text_for() -> None:
    budget = _budget()
    with pytest.raises(ValueError, match="no stop message"):
        budget.stop_message(RunStatus.COMPLETED)
