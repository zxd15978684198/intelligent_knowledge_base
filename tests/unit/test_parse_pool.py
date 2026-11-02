"""进程池 + 租约心跳的单元测试（不需要数据库、不需要 docker）。

这些测试会真起子进程（Windows 上只能是 spawn），所以**被测函数必须定义在本模块的
顶层**：`ProcessPoolExecutor` 用 pickle 把可调用对象送到子进程，pickle 只记
"模块名 + 限定名"，子进程靠重新 import 这个模块把函数取回来。定义在测试函数里的
闭包拿不到限定名，会在提交阶段就 `PicklingError`（或子进程侧 `AttributeError`）。

测的四件事，对应 ADR 0008 D6：

1. 正常解析走子进程，返回值能过进程边界；
2. 超时变成 `PARSE_TIMEOUT`（422、不可重试），且**池能立刻重建**——超时后第二次
   调用必须还能成功，否则第一次慢文件就把整个 Worker 废了；
3. 子进程崩溃（段错误 / `os._exit`）变成 `PARSE_FAILED`，同样能重建；
4. 子进程抛的 `AppError` 在父进程被还原成**同一个错误码**（`AppError` 本身过不了
   pickle 边界，见 `services/ingest/pool.py` 的 `_run_in_child`）。

心跳部分不碰真 PG：续租函数是可注入的（`renew=`），只验证节奏、停止条件与
"失败只 warning 不杀作业"。
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import time
from uuid import uuid4

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.settings import settings
from pyknora.services.ingest import pool
from pyknora.services.queue.jobs import ClaimedJob
from pyknora.workers.ingest_worker import (
    HEARTBEAT_INTERVAL_S,
    HEARTBEAT_MAX_CONSECUTIVE_FAILURES,
    heartbeat_loop,
    lease_heartbeat,
)

# --------------------------------------------------------------- 子进程侧的函数
# 必须是模块顶层（见模块 docstring）；参数签名与 `parser.parse` 的
# `(data, filename)` 前两位保持一致，因为池是按这个签名调用的。


def _double(data: bytes, filename: str) -> bytes:
    return b"x" * 3


def _child_pid(data: bytes, filename: str) -> int:
    return os.getpid()


def _sleep_a_while(data: bytes, filename: str) -> bytes:
    time.sleep(5.0)
    return b"never"


def _die_hard(data: bytes, filename: str) -> bytes:
    os._exit(1)


def _raise_app_error(data: bytes, filename: str) -> bytes:
    raise AppError(ErrorCode.UNSUPPORTED_FORMAT, "不认识这个格式", details={"ext": ".bin"})


def _make_job() -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        workspace_id=uuid4(),
        document_id=uuid4(),
        job_type="ingest_document",
        attempt=1,
        lease_epoch=1,
        attempt_id=uuid4(),
        payload={},
    )


# --------------------------------------------------------------------- 进程池


def test_run_in_pool_returns_child_result() -> None:
    """稳态路径：结果能过进程边界，且**真的**在另一个进程里算。"""
    assert pool.run_in_pool(_double, b"payload", "a.bin") == b"xxx"

    first = pool.run_in_pool(_child_pid, b"payload", "a.bin")
    second = pool.run_in_pool(_child_pid, b"payload", "b.bin")

    assert first != os.getpid(), "解析必须发生在子进程，否则隔离形同虚设"
    # 池是**常驻**的：两次调用复用同一个子进程。每次重建池都要重付 OCR 引擎
    # 构造 7.55 s、spawn→首帧 30.9 s 的代价（ADR 0008 D6），这条断言就是防它。
    assert first == second


def test_run_in_pool_timeout_raises_parse_timeout_then_recovers() -> None:
    """超时 = `PARSE_TIMEOUT`（422、不可重试），且池必须重建后继续可用。

    "之后仍然可用"是这条用例的重点：只断言抛错的话，一个把池搞坏就不管的实现
    也能通过——那种实现跑一个慢文件之后就再也解析不了任何东西。
    """
    with pytest.raises(AppError) as excinfo:
        pool.run_in_pool(_sleep_a_while, b"payload", "slow.bin", timeout_s=0.2)

    err = excinfo.value
    assert err.code == ErrorCode.PARSE_TIMEOUT
    assert err.code == "PARSE_TIMEOUT"
    assert err.retryable is False, "超时是 422 不可重试：重试只会再超时一次"
    assert err.details == {"timeout_seconds": 0.2}

    # 池已重建：同一进程里的下一次调用必须成功。
    assert pool.run_in_pool(_double, b"payload", "after-timeout.bin") == b"xxx"


def test_run_in_pool_child_crash_raises_parse_failed_then_recovers() -> None:
    """子进程硬崩（`os._exit`）不能让 Worker 跟着一起死。"""
    with pytest.raises(AppError) as excinfo:
        pool.run_in_pool(_die_hard, b"payload", "boom.bin")

    err = excinfo.value
    assert err.code == ErrorCode.PARSE_FAILED
    assert err.retryable is False, "解析进程崩溃重试同一份文件只会再崩一次"

    assert pool.run_in_pool(_double, b"payload", "after-crash.bin") == b"xxx"


def test_app_error_survives_process_boundary() -> None:
    """子进程抛的 `AppError` 到父进程必须还是同一个错误码。

    `AppError` 自己过不了 pickle：`BaseException.__reduce__` 只回放 `args`
    （= `(message,)`），与 `__init__(code, message, ...)` 对不上，直接
    `pickle.loads` 会 `TypeError: missing 1 required positional argument`。
    池里因此把错误降级成 `(code, message, details)` 三元组传回来。若这里退化成
    `INTERNAL_ERROR`（可重试），坏文件会白占 3 次重试。
    """
    with pytest.raises(AppError) as excinfo:
        pool.run_in_pool(_raise_app_error, b"payload", "weird.bin")

    err = excinfo.value
    assert err.code == ErrorCode.UNSUPPORTED_FORMAT
    assert err.message == "不认识这个格式"
    assert err.details == {"ext": ".bin"}
    assert err.retryable is False


def test_assert_batch_size_limit() -> None:
    """上限 100 页：pdfplumber 内存 3.45–3.8 MB/页近线性，1032 页外推就是 3.9 GB。"""
    assert pool.MAX_PAGES_PER_BATCH == 100
    pool.assert_batch_size(1)
    pool.assert_batch_size(pool.MAX_PAGES_PER_BATCH)

    with pytest.raises(AppError) as excinfo:
        pool.assert_batch_size(pool.MAX_PAGES_PER_BATCH + 1)

    err = excinfo.value
    assert err.code == ErrorCode.PARSE_FAILED
    assert err.details == {"metric": "pages", "limit": 100, "actual": 101}


def test_shutdown_pool_then_lazy_recreate() -> None:
    """显式收池后下一次调用自动重建；重复收不报错（幂等）。"""
    before = pool.run_in_pool(_child_pid, b"payload", "a.bin")
    pool.shutdown_pool()
    pool.shutdown_pool()  # 幂等

    after = pool.run_in_pool(_child_pid, b"payload", "b.bin")
    assert after != before, "收池之后必须是新起的子进程"


def test_discard_pool_tolerates_dead_pool() -> None:
    """池坏掉后 `_processes` 会变成 `None`，丢弃它不许抛。

    真实触发路径是"强制收池时子进程正被杀"，单测里没法稳定复现那个竞态，所以直接
    喂一个形状正确的假池（第一版这里会 `AttributeError: 'NoneType' object has no
    attribute 'values'`，把该报的 `PARSE_FAILED` 吃掉）。
    """

    class _DeadPool:
        _processes = None

        def __init__(self) -> None:
            self.shutdown_calls: list[dict[str, bool]] = []

        def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
            self.shutdown_calls.append({"wait": wait, "cancel_futures": cancel_futures})

    dead = _DeadPool()
    pool._discard_pool(dead)  # type: ignore[arg-type]  # 只为验证容错分支

    assert dead.shutdown_calls == [{"wait": False, "cancel_futures": True}]


# ----------------------------------------------------------------------- 心跳


def test_heartbeat_interval_leaves_room_for_retries() -> None:
    """30 s 心跳 / 90 s 租约：丢一次还有一次，别把心跳做成 1:1。"""
    assert HEARTBEAT_INTERVAL_S == 30.0
    assert settings.lease_seconds == 90
    assert settings.lease_seconds >= 2 * HEARTBEAT_INTERVAL_S


async def test_heartbeat_renews_on_interval_until_stopped() -> None:
    """按间隔续租，`stop` 置位后立刻停——不是每轮都发，也不是发一次就完。"""
    stop = asyncio.Event()
    calls: list[float] = []

    async def renew(job: ClaimedJob) -> bool:
        calls.append(time.monotonic())
        if len(calls) >= 3:
            stop.set()
        return True

    # 间隔取 50 ms 而不是 10 ms：Windows 上 `time.monotonic()` 的实际粒度约 15.6 ms，
    # 若相邻两次续租只隔 10 ms，两次采样可能落进同一个计时器刻度，差值为 0，
    # 于是「至少睡了一个间隔」的断言会随机失败（实测全量跑时复现过一次）。
    # 阈值取 20 ms：远小于 50 ms（真睡过就必然满足），又远大于计时器粒度。
    await asyncio.wait_for(
        heartbeat_loop(job=_make_job(), stop=stop, renew=renew, interval_s=0.05),
        timeout=5.0,
    )

    assert len(calls) == 3
    for earlier, later in itertools.pairwise(calls):
        # 每轮之间至少睡一个间隔（留一点调度余量），证明不是空转刷 DB。
        assert later - earlier >= 0.02


async def test_heartbeat_errors_only_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DB 抖动只 warning：连续失败到上限才放弃，且**不抛**——抛了就是主动杀作业。"""
    stop = asyncio.Event()
    calls = 0

    async def renew(job: ClaimedJob) -> bool:
        nonlocal calls
        calls += 1
        raise RuntimeError("connection reset by peer")

    with caplog.at_level(logging.WARNING, logger="pyknora.workers.ingest_worker"):
        await asyncio.wait_for(  # 不抛 = 用例通过
            heartbeat_loop(
                job=_make_job(),
                stop=stop,
                renew=renew,
                interval_s=0.01,
                max_consecutive_failures=HEARTBEAT_MAX_CONSECUTIVE_FAILURES,
            ),
            timeout=5.0,
        )

    assert HEARTBEAT_MAX_CONSECUTIVE_FAILURES == 3
    assert calls == 3, "连续 3 次失败后才停止心跳"

    ours = [r for r in caplog.records if r.name == "pyknora.workers.ingest_worker"]
    per_failure = [r for r in ours if "续租失败（连续" in r.getMessage()]
    stop_lines = [r for r in ours if "停止心跳" in r.getMessage()]
    assert len(per_failure) == 3
    assert len(stop_lines) == 1
    assert all(r.levelno == logging.WARNING for r in ours), "只许 warning，不许升级成 error"


async def test_heartbeat_stops_when_renew_rejected() -> None:
    """续租被拒（lease_epoch 过期，行已被别人接走）= 立即停手，继续跑没有意义。"""
    stop = asyncio.Event()
    calls = 0

    async def renew(job: ClaimedJob) -> bool:
        nonlocal calls
        calls += 1
        return False

    await asyncio.wait_for(
        heartbeat_loop(job=_make_job(), stop=stop, renew=renew, interval_s=0.01),
        timeout=5.0,
    )
    assert calls == 1


async def test_lease_heartbeat_stops_after_context_exit() -> None:
    """上下文退出后不留悬空 task：否则作业结束后心跳还在续一个已完成的租约。"""
    calls = 0

    async def renew(job: ClaimedJob) -> bool:
        nonlocal calls
        calls += 1
        return True

    async with lease_heartbeat(_make_job(), renew=renew, interval_s=0.01):
        await asyncio.sleep(0.05)

    seen = calls
    assert seen >= 1, "作业体期间至少续租一次"

    await asyncio.sleep(0.05)
    assert calls == seen, "退出上下文后不许再有续租"
