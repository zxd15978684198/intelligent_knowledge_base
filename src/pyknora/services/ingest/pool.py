"""解析进程池：全项目唯一的进程隔离入口（ADR 0008 D6）。

**为什么常驻**：解析子进程不是"用完就扔"的便宜对象。实测口径（ADR 0008 D10-4，
记录见 `docs/plans/parsing-bench-pdf.md`）：RapidOCR 引擎构造 7.55 s、子进程
spawn → 出首页整链路 30.9 s，而引擎稳态构造仅 0.98–1.05 s。池每被回收一次就要
重付一次冷启动，所以 `ProcessPoolExecutor(max_workers=1)` 是**模块级懒创建 +
进程内单例 + `atexit` 关闭**，只在两种必须换新进程的情况下重建：解析硬超时、
子进程崩溃。这两个代价（7.55 s / 30.9 s）就是"常驻"的全部理由，不是顺手优化。

**为什么不在这里 import 解析器**：子进程按 pickle 里的模块名自己 import 目标函数。
本模块若在顶层 import `services.ingest.parser`，等于让"池 → 解析器 → 池"的循环
import 成立，并把解析器的整条 import 链（pypdf / python-docx / …）拖进每个子进程的
启动路径。这里只依赖标准库与 `core.errors`。

**超时语义**（`PARSE_TIMEOUT`，不可重试，HTTP 422）：预算到点后先 `future.cancel()`，
再**丢弃整个池**并重建。只取消任务不够：一个跑飞的解析器仍占着 3.45–3.8 MB/页 ×
页数的内存（1032 页外推 3.9 GB），不换进程就等于让它在后台继续吃内存，而新任务已经
在另一个进程里开跑，两份 pdfplumber 同时驻留。

**`AppError` 不能跨进程**：`AppError.__init__(code, message, *, details)` 与
`BaseException.__reduce__`（只回放 `self.args` = `(message,)`）不匹配，pickle 往返
会在父进程炸成 `TypeError: missing ... 'message'`。若不管，子进程里"格式不支持"
（`UNSUPPORTED_FORMAT`）与"文件坏了"（`PARSE_FAILED`）到了父进程就退化成
`INTERNAL_ERROR`——它是**可重试**的，于是一份坏文件会白占满 3 次重试（见
`workers/ingest_worker.py` 的 `_NO_RETRY`）。所以子进程侧用 `_run_in_child` 把
`AppError` 就地降级成可 pickle 的三元组，父进程再原样重建。
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import multiprocessing
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Final, cast

from pyknora.core.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

#: 单批页数上限。pdfplumber 内存 3.45–3.8 MB/页且**近线性**（ADR 0008 D5-1）：
#: 100 页 ≈ 380 MB 尚可接受；1032 页外推 3.9 GB，会先把 Worker 打死再超时。
MAX_PAGES_PER_BATCH: Final[int] = 100

#: 解析硬超时默认值。**必须小于租约**（`core/settings.py:104` 的
#: `lease_seconds` = 90 s）：否则解析还没判超时，作业已被第二个 Worker 重新认领。
PARSE_TIMEOUT_S: Final[float] = 60.0

_OK: Final[str] = "ok"
_APP_ERROR: Final[str] = "app_error"

_pool: ProcessPoolExecutor | None = None
#: 创建/重建互斥。心跳与主协程可能同时到达（`asyncio.to_thread` 里就是另一个线程）。
_pool_lock = threading.Lock()


def _new_pool() -> ProcessPoolExecutor:
    """建一个 spawn 单进程池。

    显式指定 `spawn` 而不是用平台默认：POSIX 默认的 `fork` 在"多线程进程 + 有打开的
    数据库 socket"的 Worker 里 fork，是有名的死锁姿势（而且我们正是在 `to_thread`
    的工作线程里提交任务）。spawn 只多付一次启动代价，换确定性。
    """
    return ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = _new_pool()
            logger.debug("创建解析进程池：spawn, max_workers=1")
        return _pool


def _discard_pool(pool: ProcessPoolExecutor) -> None:
    """丢弃一个池：先尽力杀掉子进程，再不等候地关掉它。

    只 `shutdown(wait=False)` 不够——它不杀正在跑的任务（`cancel_futures` 只作用于
    还没开始的）。`_processes` 是私有属性：池坏掉之后它会变成 `None`，所以这里
    `or {}` 兜住（第一版漏了，崩溃路径上会抛 `AttributeError: 'NoneType' object has
    no attribute 'values'`，把该报的 `PARSE_FAILED` 吃掉）。
    """
    processes = getattr(pool, "_processes", None) or {}
    for proc in list(processes.values()):
        with contextlib.suppress(Exception):
            proc.terminate()
    with contextlib.suppress(Exception):
        pool.shutdown(wait=False, cancel_futures=True)


def _reset_pool(pool: ProcessPoolExecutor) -> None:
    """把全局池置空（下一次调用重建），并丢弃传进来的这个池。"""
    global _pool
    with _pool_lock:
        if _pool is pool:
            _pool = None
    _discard_pool(pool)


def _run_in_child(fn: Callable[[bytes, str], Any], data: bytes, filename: str) -> tuple[str, Any]:
    """子进程侧的薄壳：只负责把 `AppError` 变成可 pickle 的元组。

    见模块 docstring 末段——`AppError` 实例本身跨不了进程边界。
    """
    try:
        return _OK, fn(data, filename)
    except AppError as exc:
        return _APP_ERROR, (exc.code, exc.message, exc.details)


def run_in_pool[T](
    fn: Callable[[bytes, str], T],
    data: bytes,
    filename: str,
    *,
    timeout_s: float = PARSE_TIMEOUT_S,
) -> T:
    """在常驻子进程里跑 `fn(data, filename)`，超出预算即判超时。

    - 超时 → `AppError(PARSE_TIMEOUT, "解析超时")`，并重建池；
    - 子进程崩溃（`BrokenProcessPool`）→ `AppError(PARSE_FAILED, "解析进程异常退出")`，并重建池；
    - `fn` 抛的其它异常原样上抛（含 `AppError`，经 `_run_in_child` 还原）。

    `fn` 必须是**模块级可 import 的函数**（spawn 子进程按模块名 + 限定名 pickle）。
    """
    pool = _get_pool()
    try:
        future = pool.submit(_run_in_child, fn, data, filename)
    except BrokenProcessPool as exc:
        # 池在两次调用之间坏掉了（上一个任务把子进程弄死），换新的再抛业务错误。
        _reset_pool(pool)
        raise AppError(ErrorCode.PARSE_FAILED, "解析进程异常退出") from exc

    try:
        outcome, payload = future.result(timeout=timeout_s)
    except FuturesTimeoutError:
        # 子进程自己抛 `TimeoutError` 时 future 已经 done——那是业务异常，原样上抛，
        # 不能当成预算到点（否则会把一次正常失败误判成超时并白白重建池）。
        if future.done():
            raise
        future.cancel()
        _reset_pool(pool)
        logger.warning("解析超时（>%.1fs），已丢弃进程池并重建", timeout_s)
        raise AppError(
            ErrorCode.PARSE_TIMEOUT,
            "解析超时",
            details={"timeout_seconds": timeout_s},
        ) from None
    except BrokenProcessPool as exc:
        _reset_pool(pool)
        raise AppError(ErrorCode.PARSE_FAILED, "解析进程异常退出") from exc

    if outcome == _APP_ERROR:
        code, message, details = payload
        raise AppError(code, message, details=details)
    return cast(T, payload)


def assert_batch_size(pages: int) -> None:
    """一批的页数必须 ≤ `MAX_PAGES_PER_BATCH`，否则抛 `PARSE_FAILED`。

    闭区间：恰好 100 页放行，101 就拒。依据是内存而不是耗时——耗时超了会被 60 s
    硬超时兜住，内存超了是先把 Worker 打死（OOM），连 `PARSE_TIMEOUT` 都记不上。
    别和 `services/ingest/guards.py` 的 `MAX_PAGES` 混：那个是单文档的解析安全上限
    （管"这份文件能不能读"），本常量是单批任务的页数上限（管"一次批多大"）。
    """
    if pages > MAX_PAGES_PER_BATCH:
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "本批页数超过进程池上限",
            details={"metric": "pages", "limit": MAX_PAGES_PER_BATCH, "actual": pages},
        )


def shutdown_pool() -> None:
    """立刻收掉池（含正在跑的子进程）。Worker 退出前显式调用。

    **别只依赖 `atexit`**：`concurrent.futures.process` 把收尾挂在
    `threading._register_atexit(_python_exit)` 上，而它在 `atexit` 回调**之前**执行，
    会先 join 掉正在跑的任务。实测：一个 `sleep(30)` 的子任务让解释器退出多等了
    27.8 s——Ctrl-C 看起来就像卡死。所以在 `run_worker.py` 的 `finally` 里主动调一次
    （此时不该再有新任务提交）；模块级的 `atexit.register` 只作为兜底。
    """
    if _pool is not None:
        _reset_pool(_pool)


atexit.register(shutdown_pool)
