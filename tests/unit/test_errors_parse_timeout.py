"""`PARSE_TIMEOUT` 错误码的登记完整性与语义（ADR 0008 D7）。

ADR 0008 的 D7 决策要求解析硬超时（60s 杀子进程）用**独立**错误码表达，
语义是「不可重试」、HTTP 422。本文件是那条决策的可执行版本：码存在、
三处登记没有漏、序列化形状与全局约定一致。

为什么值得单列测试：`core/errors.py` 里同一个码要在枚举、`_HTTP_STATUS`、
`_RETRYABLE`（"缺席即不可重试"）三处保持一致，任何一处漏了都不会报错，
只会安静地退化成 500 或"可重试"，等到线上重试风暴才发现。
"""

from __future__ import annotations

from pyknora.core.errors import _HTTP_STATUS, _RETRYABLE, AppError, ErrorCode
from pyknora.workers.ingest_worker import _NO_RETRY

REQUEST_ID = "req_0123456789abcdef"


def test_parse_timeout_exists_in_enum() -> None:
    """码本身存在，且字符串值就是 `PARSE_TIMEOUT`（与其它码同风格）。"""
    assert ErrorCode.PARSE_TIMEOUT == "PARSE_TIMEOUT"


def test_parse_timeout_http_status_is_422() -> None:
    """超时对客户端是"这份文档按当前契约处理不了"，落 422 而非 5xx。"""
    assert _HTTP_STATUS[ErrorCode.PARSE_TIMEOUT] == 422


def test_parse_timeout_is_not_retryable() -> None:
    """不可重试：重试同样会超时，只会在队头再压 60s。"""
    assert ErrorCode.PARSE_TIMEOUT not in _RETRYABLE
    assert AppError(ErrorCode.PARSE_TIMEOUT, "parse exceeded 60s").retryable is False


def test_parse_timeout_body_shape() -> None:
    """响应体形状与全局约定一致，且 details 原样透传。"""
    error = AppError(
        ErrorCode.PARSE_TIMEOUT,
        "parse exceeded 60s",
        details={"timeout_seconds": 60},
    )

    assert error.http_status == 422
    assert error.to_body(REQUEST_ID) == {
        "error": {
            "code": "PARSE_TIMEOUT",
            "message": "parse exceeded 60s",
            "retryable": False,
            "details": {"timeout_seconds": 60},
        },
        "request_id": REQUEST_ID,
    }


def test_parse_timeout_body_shape_without_details() -> None:
    """不给 details 时是空 dict，不是 None——前端不必做非空判断。"""
    body = AppError(ErrorCode.PARSE_TIMEOUT, "parse exceeded 60s").to_body(REQUEST_ID)
    assert body["error"]["details"] == {}


def test_parse_timeout_in_worker_no_retry() -> None:
    """Worker 侧必须认得它，否则一件超时作业会白占满 max_attempts 次重试。"""
    assert ErrorCode.PARSE_TIMEOUT in _NO_RETRY


def test_every_error_code_has_an_http_status() -> None:
    """护栏：所有码都登记了状态码。

    `_HTTP_STATUS.get(code, 500)` 的默认值会把"漏登记"伪装成 500，
    所以这里逐条对齐，新加码时忘了登记会立刻红。
    """
    codes = {
        value
        for name, value in vars(ErrorCode).items()
        if not name.startswith("_") and isinstance(value, str)
    }
    assert codes, "ErrorCode 上没取到任何码，取值方式可能变了"
    missing = sorted(codes - set(_HTTP_STATUS))
    assert missing == [], f"这些码没有登记 HTTP 状态：{missing}"
