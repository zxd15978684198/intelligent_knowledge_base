"""API 测试共用的令牌构造与常量。

单独成模块而不是写在 `conftest.py` 里，是为了让测试模块能直接 import：
pytest 会把 `conftest.py` 以 `tests.api.conftest` 的名字导入，测试文件若再写一次
`from conftest import ...` 就会得到**两个不同的模块实例**，常量各存一份，改一处
另一处不跟着变——这种不一致排查起来很浪费时间。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from uuid import UUID

from pyknora.core.scope import WORKSPACE_A

KB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
JWT_SECRET = "test-secret"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_token(
    *,
    sub: str,
    workspace_id: str,
    exp_offset: int = 3600,
    secret: str = JWT_SECRET,
    alg: str = "HS256",
) -> str:
    """造一个 HS256 token。

    `alg` 可覆盖是为了造 `alg: none` 的伪造样本——真实攻击者能完全控制头部，
    测试里也必须能。
    """
    header = _b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    payload = {
        "sub": sub,
        "default_workspace_id": workspace_id,
        "exp": int(time.time()) + exp_offset,
    }
    body = _b64(json.dumps(payload).encode())
    sig = _b64(hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


def bearer(workspace_id: str = str(WORKSPACE_A)) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(sub=workspace_id, workspace_id=workspace_id)}"}
