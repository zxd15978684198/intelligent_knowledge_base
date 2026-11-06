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
    # 注册撞唯一键（用户名/邮箱已存在）。不复用 IDEMPOTENCY_CONFLICT：
    # 那个码是"重放同一个幂等键"，与"这个用户名被人占了"是两件事，混用会让前端
    # 分不清该提示"换一个名字"还是"稍后重试"。
    CONFLICT = "CONFLICT"
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
    # 解析硬超时（ADR 0008 D7：60s 到点杀子进程）。单列一个码而不是复用
    # PARSE_FAILED：前者是"这份文件本身坏了"（坏字节，重试仍坏），后者是
    # "解析器没在预算内跑完"（可能是机器慢/文件过大），前端提示与排查方向都不同。
    # 但两者的**重试语义一致**：重试同样会超时，所以不可重试、直接终态。
    PARSE_TIMEOUT = "PARSE_TIMEOUT"
    # 双提交 CSRF 校验失败。单列一个码而不是复用 FORBIDDEN：后者在前端的语义是
    # 「不是本空间成员」（见 deps.get_scope），混用会把「请重新提交」显示成「无权访问」；
    # 也不能用 UNAUTHENTICATED(401)，那会触发前端的刷新单飞，而 CSRF 失败刷新救不回来。
    CSRF_FAILED = "CSRF_FAILED"


_HTTP_STATUS: dict[str, int] = {
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.CSRF_FAILED: 403,
    ErrorCode.RESOURCE_NOT_FOUND: 404,
    ErrorCode.CURSOR_EXPIRED: 410,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
    ErrorCode.INVALID_REQUEST: 422,
    ErrorCode.UNSUPPORTED_FORMAT: 422,
    ErrorCode.PARSE_FAILED: 422,
    ErrorCode.PARSE_INCOMPLETE: 422,
    ErrorCode.CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.REVISION_CONFLICT: 409,
    ErrorCode.STALE_ATTEMPT: 409,
    ErrorCode.VERSION_SUPERSEDED: 409,
    ErrorCode.REVISION_MISSING: 428,
    ErrorCode.INDEX_INCOMPLETE: 502,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.PARSE_TIMEOUT: 422,
}

# 注意：**不进** `_RETRYABLE` 就是"不可重试"的实现方式（见 AppError.retryable）。
# PARSE_TIMEOUT 故意不在此集合内：超时是确定性的，重试只会再等 60s 再超时一次。
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
