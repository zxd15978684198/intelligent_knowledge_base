"""N1 日志底座（`src/pyknora/core/logging.py`）的契约测试。

覆盖验收要求的四件事：text 真的装了 handler 且带时间戳、json 可 `json.loads`、
contextvars 在并发请求之间不串号、重复 configure 不叠加 handler。
另外钉住三件容易被"顺手优化"掉的行为：未知值写 null 不写 0；不碰 root / uvicorn.access；
参数非法时先报错、logger 保持原样。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
import re
import sys
import types
from collections.abc import Iterator

import pytest

from pyknora.core.logging import (
    CONSOLE_HANDLER_MARKER,
    LOGGER_NAME,
    bind_request_context,
    configure_logging,
    log_event,
    reset_request_context,
)

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} ")

# 与计划里给的样例逐字对齐：时间戳 + 级别 + logger 名 + event + request_id + fields。
EVENT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} INFO pyknora\.retrieval "
    r"event=retrieval_completed request_id=r-8f2a duration_ms=812$"
)


@pytest.fixture(autouse=True)
def _isolated_logger(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每个用例都从"没人配置过"的状态起步，结束后把全局 logger 原样还回去。

    不清空的话，`configure_logging` 的"不叠加 handler"断言会受前一个用例影响；
    不还原的话，后面的用例（以及别的测试文件）会拿到指向已关闭 StringIO 的 handler。
    """
    monkeypatch.delenv("PYKNORA_LOG_LEVEL", raising=False)
    monkeypatch.delenv("PYKNORA_LOG_FORMAT", raising=False)
    logger = logging.getLogger(LOGGER_NAME)
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    logger.handlers.clear()
    try:
        yield
    finally:
        logger.handlers[:] = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


def _capture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    level: str | None = None,
    fmt: str | None = None,
) -> io.StringIO:
    """把 stderr 换成 StringIO 再 configure：新建的 handler 会绑定当时的 stderr。"""
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buffer)
    configure_logging(level=level, fmt=fmt)
    return buffer


def _ours(logger: logging.Logger) -> list[logging.Handler]:
    return [h for h in logger.handlers if getattr(h, CONSOLE_HANDLER_MARKER, False)]


def test_text_handler_installed_with_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = _capture(monkeypatch, fmt="text")
    logger = logging.getLogger(LOGGER_NAME)
    assert len(_ours(logger)) == 1, "配置后 pyknora 必须恰好有一个自己的 handler"
    assert logger.level == logging.INFO

    # 改造前这条会落进 lastResort，级别不到 WARNING 直接消失。
    logger.info("pyknora api starting")

    line = buffer.getvalue().strip()
    assert TS_RE.match(line), line
    assert " INFO pyknora pyknora api starting" in line


def test_text_event_line_matches_contract_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = _capture(monkeypatch, fmt="text")
    # 用"真在 pyknora 树里"的模块名调用，验证 logger 名取自调用方模块。
    module = types.ModuleType("pyknora.retrieval")
    exec(
        "from pyknora.core.logging import log_event\n"
        "\n"
        "def run() -> None:\n"
        "    log_event('retrieval_completed', duration_ms=812)\n",
        module.__dict__,
    )
    token = bind_request_context("r-8f2a")
    try:
        module.run()
    finally:
        reset_request_context(token)

    line = buffer.getvalue().strip()
    assert EVENT_RE.match(line), line


def test_json_line_is_single_parseable_object(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = _capture(monkeypatch, fmt="json")
    token = bind_request_context("r-json")
    try:
        log_event("retrieval_completed", duration_ms=812, cache_hit=None)
    finally:
        reset_request_context(token)

    raw = buffer.getvalue()
    assert raw.count("\n") == 1, "json 形态必须一行一条"
    payload = json.loads(raw)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "pyknora", "调用方不在 pyknora 树内时回落到 pyknora"
    assert payload["event"] == "retrieval_completed"
    assert payload["request_id"] == "r-json"
    assert payload["duration_ms"] == 812
    assert payload["cache_hit"] is None, "未知值写 null，不许写 0"
    assert dt.datetime.fromisoformat(payload["ts"]).tzinfo is not None


def test_text_unknown_values_render_null_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = _capture(monkeypatch, fmt="text")
    log_event("retrieval_completed", duration_ms=None, cache_hit=False, note="two words")

    line = buffer.getvalue().strip()
    assert "request_id=null" in line, "没有请求上下文时 request_id 也是 null"
    assert "duration_ms=null" in line
    assert "duration_ms=0" not in line, "没测到不许写成 0"
    assert "cache_hit=false" in line
    assert 'note="two words"' in line, "带空格的值必须加引号，否则一行日志会被拆开"


def test_explicit_request_id_overrides_context(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = _capture(monkeypatch, fmt="text")
    token = bind_request_context("r-context")
    try:
        log_event("batch_done", request_id="r-batch", rows=3)
    finally:
        reset_request_context(token)

    line = buffer.getvalue().strip()
    assert "request_id=r-batch" in line, "显式传的 request_id 优先于 contextvar"
    assert "rows=3" in line


async def test_request_context_does_not_leak_between_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = _capture(monkeypatch, fmt="text")

    async def handle(request_id: str, hops: int) -> None:
        token = bind_request_context(request_id)
        try:
            for _ in range(hops):
                await asyncio.sleep(0)  # 让两个请求交错执行
            log_event("request_done", hops=hops)
        finally:
            reset_request_context(token)

    await asyncio.gather(handle("r-a", 5), handle("r-b", 1))

    lines = buffer.getvalue().splitlines()
    assert len(lines) == 2
    pairs: dict[str, str] = {}
    for line in lines:
        rid = re.search(r"request_id=(\S+)", line)
        hop = re.search(r"hops=(\d+)", line)
        assert rid is not None and hop is not None, line
        pairs[hop.group(1)] = rid.group(1)
    assert pairs == {"5": "r-a", "1": "r-b"}, "并发请求的 request_id 串号了"

    log_event("after_tasks")
    assert buffer.getvalue().splitlines()[-1].endswith("request_id=null")


def test_repeated_configure_does_not_stack_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = _capture(monkeypatch, fmt="text")
    logger = logging.getLogger(LOGGER_NAME)
    for fmt in ("json", "text", "json"):
        configure_logging(level="DEBUG", fmt=fmt)

    assert len(_ours(logger)) == 1, "重复 configure 叠加了 handler，同一条日志会被打印多遍"
    assert logger.level == logging.DEBUG

    logger.debug("debug-visible")
    payload = json.loads(buffer.getvalue())  # 最后一次配置是 json，形态必须真的换了
    assert payload["msg"] == "debug-visible"
    assert payload["level"] == "DEBUG"


def test_env_vars_apply_only_when_arguments_are_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYKNORA_LOG_LEVEL", "debug")
    monkeypatch.setenv("PYKNORA_LOG_FORMAT", "json")
    buffer = _capture(monkeypatch)  # 不传参：走环境变量

    logger = logging.getLogger(LOGGER_NAME)
    assert logger.level == logging.DEBUG
    logger.debug("debug-visible")
    assert json.loads(buffer.getvalue())["msg"] == "debug-visible"

    configure_logging(level="WARNING", fmt="text")  # 显式参数优先于环境变量
    assert logger.level == logging.WARNING


def test_invalid_format_and_level_are_rejected_before_mutating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    before = (logger.level, list(logger.handlers))
    with pytest.raises(ValueError, match="日志形态非法"):
        configure_logging(fmt="xml")
    with pytest.raises(ValueError, match="日志级别非法"):
        configure_logging(level="NOPE")
    assert (logger.level, list(logger.handlers)) == before, "配错了却改了一半"


def test_configure_leaves_root_and_uvicorn_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    root = logging.getLogger()
    access = logging.getLogger("uvicorn.access")
    root_before = (root.level, list(root.handlers))
    access_before = (access.level, list(access.handlers), access.propagate)

    _capture(monkeypatch, fmt="json")

    assert (root.level, list(root.handlers)) == root_before, "不许动 root"
    assert (access.level, list(access.handlers), access.propagate) == access_before, (
        "不许抢 uvicorn 的 access log"
    )


def test_log_event_rejects_reserved_field_names(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch)
    with pytest.raises(TypeError, match="结构键"):
        log_event("retrieval_completed", level="INFO")
