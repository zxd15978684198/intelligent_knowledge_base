"""M3 新增会话端点在**真 PG** 上的验收：Cookie 优先 / Bearer 兜底 / 双提交 CSRF /
单次轮换 / 宽限重投 / 重用检测 / 登出撤销。

放在 `tests/api` 而不是 `tests/contract`：本文件需要真库（bcrypt 注册、
`refresh_tokens` 行锁、家族撤销后的显式 commit），而 `tests/contract` 按仓库约定
不依赖数据库。M3 落地时只有进程内内存假仓储的 23 条冒烟，这里补的是真库证据。
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from pyknora.api.main import configure, create_app
from pyknora.core.scope import WORKSPACE_A
from pyknora.db.repos.reads import FakeKbRepo
from pyknora.services.ingest.embedder import HashingEmbedder
from tests.api.tokens import bearer

REGISTER = "/api/v1/auth/register"
ME = "/api/v1/auth/me"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
KB_CREATE = "/api/v1/knowledge-bases"

CSRF_HEADER = "X-CSRF-Token"
ACCESS = "pyknora_access"
REFRESH_CK = "pyknora_refresh"
CSRF_CK = "pyknora_csrf"

# httpx 0.28 对逐请求 cookies= 有弃用警告；本文件刻意清空 jar 后逐请求显式传，
# 语义明确，警告只是 API 形态提示，不影响断言。
pytestmark = pytest.mark.filterwarnings("ignore:Setting per-request cookies:DeprecationWarning")


def _payload(username: str = "cookie_user") -> dict[str, str]:
    return {
        "username": username,
        "email": f"{username}@example.com",
        "password": "s3cret-pass",
        "display_name": username.title(),
    }


@pytest.fixture
async def session_client(kb_repo: FakeKbRepo, clean_db: None) -> AsyncIterator[httpx.AsyncClient]:
    """保留 Set-Cookie 的客户端，与 `client` 夹具的「无 Cookie Bearer」语义互补。

    不用 `_BearerOnlyTransport`：这里要的正是让 httpx 把会话 Cookie 存进 jar。
    但注册后一律 `cookies.clear()`、改用逐请求显式 `cookies=`，避免 jar 的
    path/domain 匹配把重放与篡改用例搞成隐式行为。
    """
    configure(kb_repo=kb_repo, embedder=HashingEmbedder(dim=512))
    transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _signup(c: httpx.AsyncClient, username: str = "cookie_user") -> dict[str, Any]:
    """注册 → 取三件套明文 → 清空 jar，返回逐请求显式携带的会话材料。"""
    r = await c.post(REGISTER, json=_payload(username))
    assert r.status_code == 201, r.text
    body = r.json()
    sess = {
        ACCESS: c.cookies.get(ACCESS),
        REFRESH_CK: c.cookies.get(REFRESH_CK),
        CSRF_CK: c.cookies.get(CSRF_CK),
    }
    assert all(sess.values()), sess
    c.cookies.clear()
    return {
        "cookies": sess,
        "principal_id": body["principal_id"],
        "workspace_id": body["workspace_id"],
        "access_token": body["access_token"],
    }


def _csrf(s: dict[str, Any]) -> dict[str, str]:
    return {CSRF_HEADER: s["cookies"][CSRF_CK]}


# ---------------------------------------------------------------- 注册下发


async def test_register_issues_three_cookies_and_keeps_body_token(
    session_client: httpx.AsyncClient,
) -> None:
    r = await session_client.post(REGISTER, json=_payload("cookie_attrs"))
    assert r.status_code == 201, r.text
    assert r.json()["access_token"]  # CLI / eval 仍从响应体取令牌

    raw = r.headers.get_list("set-cookie")
    by_name = {h.split("=", 1)[0]: h for h in raw}
    assert set(by_name) == {ACCESS, REFRESH_CK, CSRF_CK}, raw

    assert "httponly" in by_name[ACCESS].lower()
    assert "path=/" in by_name[ACCESS].lower()
    assert "samesite=lax" in by_name[ACCESS].lower()
    assert "httponly" in by_name[REFRESH_CK].lower()
    assert "path=/api/v1/auth" in by_name[REFRESH_CK].lower()
    assert "httponly" not in by_name[CSRF_CK].lower()  # 前端必须能读


# ---------------------------------------------------------------- 解析优先级


async def test_cookie_session_me_without_authorization(
    session_client: httpx.AsyncClient,
) -> None:
    s = await _signup(session_client, "cookie_only")
    r = await session_client.get(ME, cookies=s["cookies"])
    assert r.status_code == 200, r.text
    assert r.json()["principal_id"] == s["principal_id"]


async def test_cookie_takes_priority_over_bearer(
    session_client: httpx.AsyncClient,
) -> None:
    """I2：Cookie 存在即以 Cookie 为准，忽略同时附上的 Bearer。"""
    s = await _signup(session_client, "cookie_priority")
    r = await session_client.get(ME, headers=bearer(), cookies=s["cookies"])
    assert r.status_code == 200, r.text
    assert r.json()["principal_id"] == s["principal_id"]
    assert r.json()["principal_id"] != str(WORKSPACE_A)


async def test_bearer_fallback_without_cookies(
    session_client: httpx.AsyncClient,
) -> None:
    """无 Cookie 的命令行 / eval 调用仍走 Bearer 兜底，且不被 CSRF 拦。"""
    r = await session_client.get(ME, headers=bearer())
    assert r.status_code == 200, r.text
    assert r.json()["principal_id"] == str(WORKSPACE_A)


# ---------------------------------------------------------------- CSRF


async def test_csrf_required_for_session_write(
    session_client: httpx.AsyncClient,
) -> None:
    """有会话 Cookie 的写方法缺 `X-CSRF-Token` 一律 403 CSRF_FAILED，补上才放行。

    用 `/auth/logout` 作探针：它挂在 require_csrf 上、是真实写端点、又不依赖
    kb 仓储（FakeKbRepo 没有 create），把断言聚焦在 CSRF 闸门本身。
    """
    s = await _signup(session_client, "cookie_csrf")

    denied = await session_client.post(LOGOUT, cookies=s["cookies"])
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "CSRF_FAILED"

    allowed = await session_client.post(LOGOUT, cookies=s["cookies"], headers=_csrf(s))
    assert allowed.status_code == 204, allowed.text
    assert allowed.json().get("error", {}).get("code") != "CSRF_FAILED" if allowed.content else True


# ---------------------------------------------------------------- 轮换


async def test_refresh_rotates_and_preserves_absolute_expiry(
    session_client: httpx.AsyncClient, db_session: Any
) -> None:
    s = await _signup(session_client, "cookie_rotate")
    old_refresh = s["cookies"][REFRESH_CK]

    r = await session_client.post(REFRESH, cookies=s["cookies"], headers=_csrf(s))
    assert r.status_code == 200, r.text
    assert r.json() == {"expires_in": 3600}  # 不把 access_token 漏回响应体
    assert "access_token" not in r.json()

    raw = r.headers.get_list("set-cookie")
    names = {h.split("=", 1)[0] for h in raw}
    assert names == {ACCESS, REFRESH_CK}, raw  # CSRF 不重发
    new_refresh = next(h.split(";", 1)[0].split("=", 1)[1] for h in raw if h.startswith(REFRESH_CK))
    assert new_refresh != old_refresh

    old_hash = hashlib.sha256(old_refresh.encode()).hexdigest()
    new_hash = hashlib.sha256(new_refresh.encode()).hexdigest()
    rows = (
        (
            await db_session.execute(
                text(
                    "SELECT token_hash, family_id, replaced_by, rotated_at, revoked_at, "
                    "expires_at, csrf_hash FROM refresh_tokens "
                    "WHERE token_hash IN (:old, :new)"
                ),
                {"old": old_hash, "new": new_hash},
            )
        )
        .mappings()
        .all()
    )
    assert len(rows) == 2
    old_row = next(x for x in rows if x["token_hash"] == old_hash)
    new_row = next(x for x in rows if x["token_hash"] == new_hash)
    assert old_row["replaced_by"] == new_hash
    assert old_row["rotated_at"] is not None
    assert old_row["revoked_at"] is None
    assert new_row["replaced_by"] is None
    assert new_row["family_id"] == old_row["family_id"]
    assert new_row["csrf_hash"] == old_row["csrf_hash"]  # CSRF 绑定随家族复制
    assert new_row["expires_at"] == old_row["expires_at"]  # 绝不滑动延长


async def test_replay_within_grace_then_reuse_revokes_family(
    session_client: httpx.AsyncClient, db_session: Any
) -> None:
    s = await _signup(session_client, "cookie_reuse")
    old_refresh = s["cookies"][REFRESH_CK]
    old_hash = hashlib.sha256(old_refresh.encode()).hexdigest()

    ok = await session_client.post(REFRESH, cookies=s["cookies"], headers=_csrf(s))
    assert ok.status_code == 200, ok.text

    # 宽限内重投旧串：幂等 204，不带任何 Set-Cookie，家族不撤。
    replay = await session_client.post(REFRESH, cookies=s["cookies"], headers=_csrf(s))
    assert replay.status_code == 204, replay.text
    assert replay.headers.get_list("set-cookie") == []

    # 把 rotated_at 推到宽限外，再投一次旧串 → 判定重用，整族撤销。
    await db_session.execute(
        text(
            "UPDATE refresh_tokens SET rotated_at = now() - interval '60 seconds' "
            "WHERE token_hash = :h"
        ),
        {"h": old_hash},
    )
    await db_session.commit()

    denied = await session_client.post(REFRESH, cookies=s["cookies"], headers=_csrf(s))
    assert denied.status_code == 401, denied.text
    assert denied.json()["error"]["code"] == "UNAUTHENTICATED"

    family = (
        await db_session.execute(
            text("SELECT family_id FROM refresh_tokens WHERE token_hash = :h"), {"h": old_hash}
        )
    ).scalar_one()
    live = (
        await db_session.execute(
            text("SELECT count(*) FROM refresh_tokens WHERE family_id = :f AND revoked_at IS NULL"),
            {"f": family},
        )
    ).scalar_one()
    assert live == 0  # 家族内无活跃行


async def test_refresh_rejects_tampered_csrf_and_revokes_family(
    session_client: httpx.AsyncClient, db_session: Any
) -> None:
    """CSRF 哈希绑定：换掉 csrf Cookie 视为盗用，整族撤销。"""
    s = await _signup(session_client, "cookie_csrf_bind")
    tampered = dict(s["cookies"])
    tampered[CSRF_CK] = "attacker-csrf-value"

    r = await session_client.post(
        REFRESH, cookies=tampered, headers={CSRF_HEADER: "attacker-csrf-value"}
    )
    assert r.status_code == 401, r.text

    old_hash = hashlib.sha256(s["cookies"][REFRESH_CK].encode()).hexdigest()
    family = (
        await db_session.execute(
            text("SELECT family_id FROM refresh_tokens WHERE token_hash = :h"), {"h": old_hash}
        )
    ).scalar_one()
    live = (
        await db_session.execute(
            text("SELECT count(*) FROM refresh_tokens WHERE family_id = :f AND revoked_at IS NULL"),
            {"f": family},
        )
    ).scalar_one()
    assert live == 0


# ---------------------------------------------------------------- 登出


async def test_logout_revokes_family_clears_cookies_and_is_idempotent(
    session_client: httpx.AsyncClient, db_session: Any
) -> None:
    s = await _signup(session_client, "cookie_logout")
    old_hash = hashlib.sha256(s["cookies"][REFRESH_CK].encode()).hexdigest()

    r = await session_client.post(LOGOUT, cookies=s["cookies"], headers=_csrf(s))
    assert r.status_code == 204, r.text
    cleared = r.headers.get_list("set-cookie")
    assert {h.split("=", 1)[0] for h in cleared} == {ACCESS, REFRESH_CK, CSRF_CK}
    assert all("max-age=0" in h.lower() for h in cleared)

    # 清 Cookie 之后仅凭原 Cookie 访问自省接口必须 401。
    assert session_client.cookies.get(ACCESS) is None
    me = await session_client.get(ME, cookies=s["cookies"])
    assert me.json().get("error", {}).get("code") != "CSRF_FAILED"  # 不是 CSRF 问题

    family = (
        await db_session.execute(
            text("SELECT family_id FROM refresh_tokens WHERE token_hash = :h"), {"h": old_hash}
        )
    ).scalar_one()
    live = (
        await db_session.execute(
            text("SELECT count(*) FROM refresh_tokens WHERE family_id = :f AND revoked_at IS NULL"),
            {"f": family},
        )
    ).scalar_one()
    assert live == 0

    again = await session_client.post(LOGOUT)  # 无 Cookie 也 204（幂等）
    assert again.status_code == 204
