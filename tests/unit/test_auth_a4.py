"""A4：请求里伪造身份必须被完全忽略；token 被篡改一律 401。

这组用例是纯单元（不连库）：把 `load_membership` / `set_workspace_context` 换成桩，
就能只看"身份从哪来"这一件事。跨区的真实 SQL 谓词由 tests/integration 覆盖。

为什么值得单独一组：A4 的失败方式非常安静——请求体多一个 `principal_id=admin`，
接口照样返回 200，只是返回的是别人的数据。靠"看一眼代码觉得没问题"挡不住这种回归。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from pyknora.api import deps
from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B, Scope
from pyknora.services.auth import jwt as jwt_mod
from pyknora.services.auth.jwt import decode_token, issue_token

SECRET = "unit-secret"
PRINCIPAL = UUID("33333333-3333-3333-3333-333333333333")
ADMIN_ID = UUID("99999999-9999-9999-9999-999999999999")


class _FakeRequest:
    """只提供 `get_scope` 会写的那一个属性。"""

    def __init__(self) -> None:
        self.state = SimpleNamespace()


class _FakeSession:
    """get_scope 里的两条 DB 调用在用例中都被替换掉，这个对象不会被真正使用。"""

    async def execute(self, *args, **kwargs):  # pragma: no cover - 不会被调用
        raise AssertionError("该用例不应触达数据库")


def _token_with_extra_claims(**extra: str) -> str:
    """造一个签名合法、但载荷里塞了伪造身份字段的 token。

    真实攻击者控制不了签名，所以"载荷里多塞字段"本身不构成越权；
    这里要钉的是**服务端不该去读那些字段**，哪怕它们就在载荷里。
    """
    header = jwt_mod._b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = jwt_mod._b64url_encode(
        json.dumps(
            {
                "sub": str(PRINCIPAL),
                "default_workspace_id": str(WORKSPACE_A),
                "exp": 9999999999,
                **extra,
            }
        ).encode()
    )
    sig = jwt_mod._b64url_encode(
        hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{sig}"


def _bearer(token: str) -> str:
    return f"Bearer {token}"


async def _resolve_scope(monkeypatch: pytest.MonkeyPatch, token: str, *, member: bool = True):
    async def _membership(session, principal_id, workspace_id) -> bool:
        return member

    async def _context(session, workspace_id) -> None:
        return None

    monkeypatch.setenv("PYKNORA_JWT_SECRET", SECRET)
    monkeypatch.setattr(deps, "load_membership", _membership)
    monkeypatch.setattr(deps, "set_workspace_context", _context)
    return await deps.get_scope(_FakeRequest(), _FakeSession(), _bearer(token), None)


async def test_forged_identity_claims_in_token_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """载荷里写 `principal_id=admin` / `workspace_id=B` 不改变 Scope。

    `sub` 与 `default_workspace_id` 才是唯一来源——这两个字段由 `issue_token`
    在服务端写入，客户端改不了（改了签名就错）。
    """
    token = _token_with_extra_claims(
        principal_id=str(ADMIN_ID),
        workspace_id=str(WORKSPACE_B),
        user_id="admin",
        role="owner",
    )
    scope = await _resolve_scope(monkeypatch, token)
    assert scope == Scope(workspace_id=WORKSPACE_A, principal_id=PRINCIPAL)


async def test_header_cannot_select_a_workspace_you_do_not_belong_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`X-Workspace-ID` 只能在已加入的空间里选，不能凭空造出别人的空间。"""

    async def _membership(session, principal_id, workspace_id) -> bool:
        # 这个人在 A 空间，不在 B 空间。
        return workspace_id == WORKSPACE_A

    async def _context(session, workspace_id) -> None:
        return None

    monkeypatch.setenv("PYKNORA_JWT_SECRET", SECRET)
    monkeypatch.setattr(deps, "load_membership", _membership)
    monkeypatch.setattr(deps, "set_workspace_context", _context)

    token = _token_with_extra_claims()
    with pytest.raises(AppError) as exc:
        await deps.get_scope(
            _FakeRequest(),
            _FakeSession(),
            _bearer(token),
            str(WORKSPACE_B),
        )
    assert exc.value.code == ErrorCode.FORBIDDEN


async def test_tampered_signature_is_rejected() -> None:
    token = issue_token(PRINCIPAL, WORKSPACE_A, secret=SECRET)
    head, body, _ = token.split(".")
    frozen = f"{head}.{body}.{base64.urlsafe_b64encode(b'not-the-signature').decode().rstrip('=')}"

    with pytest.raises(AppError) as exc:
        decode_token(frozen, secret=SECRET)
    assert exc.value.code == ErrorCode.UNAUTHENTICATED
    assert exc.value.message == "bad signature"


def test_alg_none_is_rejected() -> None:
    """`alg: none` 是 JWT 最经典的绕过；解码器固定 HS256，不看头部自报算法。"""
    header = jwt_mod._b64url_encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = jwt_mod._b64url_encode(
        json.dumps({"sub": str(PRINCIPAL), "default_workspace_id": str(WORKSPACE_A)}).encode()
    )
    with pytest.raises(AppError) as exc:
        decode_token(f"{header}.{body}.", secret=SECRET)
    assert exc.value.message == "unsupported alg"


def test_expired_token_is_rejected() -> None:
    token = issue_token(PRINCIPAL, WORKSPACE_A, ttl_seconds=-1, secret=SECRET)
    with pytest.raises(AppError) as exc:
        decode_token(token, secret=SECRET)
    assert exc.value.message == "token expired"


def test_issued_token_carries_only_identity_claims() -> None:
    """载荷形状是给成员2/5 的契约：多余的字段会诱导别人去读不可信的"身份"。"""
    token = issue_token(PRINCIPAL, WORKSPACE_A, secret=SECRET)
    payload = decode_token(token, secret=SECRET)
    assert set(payload) == {"sub", "default_workspace_id", "exp"}
    assert payload["sub"] == str(PRINCIPAL)
    assert payload["default_workspace_id"] == str(WORKSPACE_A)


def test_non_numeric_exp_is_401_not_500() -> None:
    """`exp` 是客户端数据，格式错必须是 401；让它冒成 500 就是在响应里暗示内部结构。"""
    payload = jwt_mod._b64url_encode(
        json.dumps(
            {"sub": str(PRINCIPAL), "default_workspace_id": str(WORKSPACE_A), "exp": "x"}
        ).encode()
    )
    header = jwt_mod._b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    sig = jwt_mod._b64url_encode(
        hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )
    with pytest.raises(AppError) as exc:
        decode_token(f"{header}.{payload}.{sig}", secret=SECRET)
    assert exc.value.code == ErrorCode.UNAUTHENTICATED


def test_token_without_exp_is_rejected() -> None:
    """无 `exp` 的令牌等于永久令牌。

    `issue_token` 永远会写 `exp`，所以"签名正确但没有过期时间"只可能来自手工构造或
    伪造。放行它就等于把有效期做成可选项——这正是 fail-open 的典型形状。
    """
    payload = jwt_mod._b64url_encode(
        json.dumps({"sub": str(PRINCIPAL), "default_workspace_id": str(WORKSPACE_A)}).encode()
    )
    header = jwt_mod._b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    sig = jwt_mod._b64url_encode(
        hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )

    with pytest.raises(AppError) as exc:
        decode_token(f"{header}.{payload}.{sig}", secret=SECRET)

    assert exc.value.code == ErrorCode.UNAUTHENTICATED
    assert exc.value.message == "malformed token"
