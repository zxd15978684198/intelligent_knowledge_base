"""统一错误体与错误码。

响应形状固定为：{"error": {"code","message","retryable","details"}, "request_id"}
details 里禁出现堆栈、密钥、原始 URL query。
"""

from __future__ import annotations

from typing import Any


class ErrorCode:
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    INVALID_REQUEST = "INVALID_REQUEST"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    REVISION_MISSING = "REVISION_MISSING"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    PARSE_FAILED = "PARSE_FAILED"
    PARSE_INCOMPLETE = "PARSE_INCOMPLETE"
    INDEX_INCOMPLETE = "INDEX_INCOMPLETE"
    STALE_ATTEMPT = "STALE_ATTEMPT"
    VERSION_SUPERSEDED = "VERSION_SUPERSEDED"
    CURSOR_EXPIRED = "CURSOR_EXPIRED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_HTTP_STATUS: dict[str, int] = {
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.RESOURCE_NOT_FOUND: 404,
    ErrorCode.CURSOR_EXPIRED: 410,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
    ErrorCode.INVALID_REQUEST: 422,
    ErrorCode.UNSUPPORTED_FORMAT: 422,
    ErrorCode.PARSE_FAILED: 422,
    ErrorCode.PARSE_INCOMPLETE: 422,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.REVISION_CONFLICT: 409,
    ErrorCode.STALE_ATTEMPT: 409,
    ErrorCode.VERSION_SUPERSEDED: 409,
    ErrorCode.REVISION_MISSING: 428,
    ErrorCode.INDEX_INCOMPLETE: 502,
    ErrorCode.INTERNAL_ERROR: 500,
}

_RETRYABLE = {
    ErrorCode.STALE_ATTEMPT,
    ErrorCode.INDEX_INCOMPLETE,
    ErrorCode.DEPENDENCY_UNAVAILABLE,
    ErrorCode.INTERNAL_ERROR,
}


class AppError(Exception):
    """业务错误。`details` 只放安全信息，禁止堆栈/密钥/原始 query。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS.get(self.code, 500)

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE

    def to_body(self, request_id: str) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            },
            "request_id": request_id,
        }


def not_found(what: str = "resource") -> AppError:
    """非授权与不存在统一返回 404，避免用状态码探测存在性。"""
    return AppError(ErrorCode.RESOURCE_NOT_FOUND, f"{what} not found")
