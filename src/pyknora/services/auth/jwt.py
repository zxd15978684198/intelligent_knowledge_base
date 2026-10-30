"""JWT 签发与校验（HS256）。纯函数，不碰 DB、不碰 HTTP。

为什么不引入 PyJWT：本仓库只需要 HS256 一种算法和一个 claims 集合，多一个依赖就多一份
"算法协商"的攻击面。三条护栏写在函数里而不是配置里：

1. 校验时**不看 token 头部自报的 alg**，只接受 HS256。看了就等于允许 `alg: none`。
2. 验签用 `hmac.compare_digest`：普通 `==` 会在第一个不同字节返回，泄出签名前缀。
3. 令牌里的 `sub` 必须是 UUID 字符串。`sub="admin"` 这类自造身份在入口就拒掉。

载荷约定（成员2/5 从这个载荷取可信身份）：
`{"sub": "<principal_id>", "default_workspace_id": "<uuid>", "exp": <unix 秒>}`
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from typing import Any
from uuid import UUID

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.settings import DEV_JWT_SECRET, settings

# 兼容旧引用名；真实来源是 `core/settings.py` 的 DEV_JWT_SECRET（只定义一次）。
DEFAULT_SECRET = DEV_JWT_SECRET

# 一小时。太长会把"改密码/停用账号"的生效时间拖到不可接受。
DEFAULT_TTL_SECONDS = 3600


def jwt_secret(secret: str | None = None) -> str:
    """按调用时读环境变量，而不是 import 时读常量——测试要能 monkeypatch.setenv。

    优先级：显式入参 → 环境变量（测试可 monkeypatch）→ Settings → 开发默认值。
    生产走不到最后一档：`Settings` 的护栏在非 dev 环境下用默认密钥会直接构造失败
    （见 `core/settings.py` 的 `_guard_production_secrets`）。
    """
    return secret or os.environ.get("PYKNORA_JWT_SECRET") or settings.jwt_secret or DEV_JWT_SECRET


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64url_decode(segment: str) -> bytes:
    """JWT 段是 base64url 且**去掉 padding**，必须补齐才能解。"""
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _sign(message: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode(), message, hashlib.sha256).digest()


def issue_token(
    principal_id: UUID,
    workspace_id: UUID,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    secret: str | None = None,
) -> str:
    """签发 access token。

    `default_workspace_id` 放进载荷是为了让"只登录过一个空间"的客户端不必额外带
    `X-Workspace-ID`。它**不是权限**：`get_scope` 仍会查一遍成员关系。
    """
    if not isinstance(principal_id, UUID) or not isinstance(workspace_id, UUID):
        raise TypeError("principal_id and workspace_id must be UUIDs parsed from trusted data")
    now = int(time.time())
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(
        json.dumps(
            {
                "sub": str(principal_id),
                "default_workspace_id": str(workspace_id),
                "exp": now + ttl_seconds,
            }
        ).encode()
    )
    signature = _b64url_encode(_sign(f"{header}.{payload}".encode(), jwt_secret(secret)))
    return f"{header}.{payload}.{signature}"


def decode_token(token: str, secret: str | None = None) -> dict[str, Any]:
    """最小 JWT (HS256) 校验。失败一律 `UNAUTHENTICATED`，不区分原因给调用方。"""
    secret = jwt_secret(secret)
    parts = token.split(".")
    if len(parts) != 3:
        raise AppError(ErrorCode.UNAUTHENTICATED, "malformed token")

    header_seg, payload_seg, sig_seg = parts
    try:
        header = json.loads(_b64url_decode(header_seg))
        payload = json.loads(_b64url_decode(payload_seg))
        signature = _b64url_decode(sig_seg)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise AppError(ErrorCode.UNAUTHENTICATED, "malformed token") from exc

    # 关键：不看 header["alg"] 决定用什么算法，只拒绝非 HS256。
    if header.get("alg") != "HS256":
        raise AppError(ErrorCode.UNAUTHENTICATED, "unsupported alg")

    expected = _sign(f"{header_seg}.{payload_seg}".encode(), secret)
    if not hmac.compare_digest(expected, signature):
        raise AppError(ErrorCode.UNAUTHENTICATED, "bad signature")

    # `exp` 必填。`issue_token` 总是写它，所以"没有 exp"只可能来自伪造或手工构造——
    # 「没有过期时间就不校验过期」等于给这类令牌永久通行证，是 fail-open。
    if "exp" not in payload:
        raise AppError(ErrorCode.UNAUTHENTICATED, "malformed token")
    try:
        expired = float(payload["exp"]) <= time.time()
    except (TypeError, ValueError) as exc:
        # exp 不是数字时不能让它变成 500：那是客户端数据，属于 401。
        raise AppError(ErrorCode.UNAUTHENTICATED, "malformed token") from exc
    if expired:
        raise AppError(ErrorCode.UNAUTHENTICATED, "token expired")

    return payload
