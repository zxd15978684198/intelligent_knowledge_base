"""任务9/10 的 API 层验收：注册、登录、重名 409、角色变更的越权边界。

这些用例要真 PG（bcrypt 写库、唯一约束、UPDATE 影响行数都是数据库行为）。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from tests.api.tokens import KB_ID, bearer, make_token

WORKSPACE_A = "11111111-1111-1111-1111-111111111111"
WORKSPACE_B = "22222222-2222-2222-2222-222222222222"
MISSING_ID = "00000000-0000-0000-0000-000000000001"

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"


def _payload(username: str = "alice", email: str | None = None) -> dict[str, str]:
    return {
        "username": username,
        "email": email or f"{username}@example.com",
        "password": "s3cret-pass",
        "display_name": username.title(),
    }


async def _add_member(
    db_session: Any, workspace_id: UUID, *, role: str = "member"
) -> tuple[UUID, UUID]:
    """直接插一行"另一个成员"，返回 (principal_id, membership_id)。"""
    principal_id = uuid4()
    suffix = str(principal_id)[:8]
    await db_session.execute(
        text(
            """
            INSERT INTO principals (principal_id, username, email, password_hash)
            VALUES (:pid, :username, :email, 'test-fixture-not-a-login-hash')
            """
        ),
        {"pid": principal_id, "username": f"m_{suffix}", "email": f"m_{suffix}@t.local"},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO workspace_memberships (workspace_id, user_id, role)
            VALUES (:ws, :pid, :role)
            """
        ),
        {"ws": workspace_id, "pid": principal_id, "role": role},
    )
    await db_session.commit()
    membership_id = (
        await db_session.execute(
            text(
                "SELECT membership_id FROM workspace_memberships "
                "WHERE workspace_id = :ws AND user_id = :pid"
            ),
            {"ws": workspace_id, "pid": principal_id},
        )
    ).scalar_one()
    return principal_id, membership_id


# ---------------------------------------------------------------- 注册


async def test_register_returns_token_and_stores_only_a_hash(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    r = await client.post(REGISTER, json=_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert UUID(body["workspace_id"])

    # 注册即建默认工作区，且注册人是 owner。
    role = (
        await db_session.execute(
            text(
                "SELECT role FROM workspace_memberships WHERE workspace_id = :ws AND user_id = :pid"
            ),
            {"ws": UUID(body["workspace_id"]), "pid": UUID(body["principal_id"])},
        )
    ).scalar_one()
    assert role == "owner"

    stored = (
        await db_session.execute(
            text("SELECT password_hash FROM principals WHERE username = 'alice'")
        )
    ).scalar_one()
    assert stored != "s3cret-pass"
    assert stored.startswith("$2b$")

    # 响应体里任何字段都不许出现密码或哈希。
    assert "s3cret-pass" not in r.text
    assert stored not in r.text


async def test_token_from_register_is_accepted_by_protected_routes(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    """注册拿到的 token 直接能用：证明默认工作区的成员关系真的落库了。

    用"不存在的文档"来验：404 说明鉴权已通过，401/403 说明成员关系没建成。
    """
    r = await client.post(REGISTER, json=_payload("bob"))
    token = r.json()["access_token"]
    r2 = await client.get(
        f"/api/v1/documents/{MISSING_ID}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r2.status_code == 404, r2.text


async def test_duplicate_username_is_409(client: httpx.AsyncClient, clean_db: None) -> None:
    first = await client.post(REGISTER, json=_payload("carol"))
    assert first.status_code == 201

    dup = await client.post(REGISTER, json=_payload("carol", "other@example.com"))
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "CONFLICT"
    # 不指出撞的是用户名还是邮箱，避免变成"这个邮箱是否注册过"的查询接口。
    assert "email" not in dup.json()["error"]["message"].lower()


async def test_duplicate_email_is_409(client: httpx.AsyncClient, clean_db: None) -> None:
    first = await client.post(REGISTER, json=_payload("dave", "same@example.com"))
    assert first.status_code == 201

    dup = await client.post(REGISTER, json=_payload("dave2", "same@example.com"))
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "CONFLICT"


@pytest.mark.parametrize(
    "patch",
    [
        {"password": "short"},  # 少于 8 位
        {"email": "not-an-email"},  # 没有 @
        {"username": "has space"},  # 非法字符
        {"password": "密" * 25},  # 超过 bcrypt 的 72 字节
    ],
)
async def test_register_rejects_malformed_input_with_422(
    client: httpx.AsyncClient, clean_db: None, patch: dict[str, str]
) -> None:
    r = await client.post(REGISTER, json={**_payload("erin"), **patch})
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------- 登录


async def test_login_issues_a_usable_token(client: httpx.AsyncClient, clean_db: None) -> None:
    reg = await client.post(REGISTER, json=_payload("frank"))
    assert reg.status_code == 201

    r = await client.post(LOGIN, json={"username": "frank", "password": "s3cret-pass"})
    assert r.status_code == 200, r.text
    assert r.json()["principal_id"] == reg.json()["principal_id"]
    assert r.json()["workspace_id"] == reg.json()["workspace_id"]


async def test_wrong_password_and_unknown_user_are_indistinguishable(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    await client.post(REGISTER, json=_payload("grace"))

    wrong = await client.post(LOGIN, json={"username": "grace", "password": "wrong-pass"})
    unknown = await client.post(LOGIN, json={"username": "nobody", "password": "wrong-pass"})

    assert wrong.status_code == unknown.status_code == 401
    # 两条响应的错误体必须逐字一致，否则状态码之外又开了一个用户枚举通道。
    assert wrong.json()["error"] == unknown.json()["error"]
    assert wrong.json()["error"]["message"] == "invalid credentials"


# ---------------------------------------------------------------- A4


async def test_query_params_cannot_forge_identity(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """A4 的查询串版本：`?workspace_id=B&principal_id=B&user_id=admin` 必须无效果。

    请求体版本已在 `test_api_documents.py::test_body_cannot_forge_identity` 覆盖；
    这里补的是 GET 场景——GET 没有请求体，伪造只能走查询串，而查询串更容易被
    "顺手读一下 args 当便利"的写法带进来。
    """
    up = await client.post(
        f"/api/v1/knowledge-bases/{KB_ID}/documents",
        headers=bearer(),
        files={"file": ("note.txt", b"identity probe", "text/plain")},
    )
    assert up.status_code == 202, up.text
    document_id = up.json()["document_id"]

    plain = await client.get(f"/api/v1/documents/{document_id}", headers=bearer())
    forged = await client.get(
        f"/api/v1/documents/{document_id}",
        headers=bearer(),
        params={"workspace_id": WORKSPACE_B, "principal_id": WORKSPACE_B, "user_id": "admin"},
    )

    assert plain.status_code == forged.status_code == 200
    assert plain.json() == forged.json()
    assert forged.json()["workspace_id"] == WORKSPACE_A


# ---------------------------------------------------------------- 角色变更


async def test_owner_can_change_member_role(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    _, membership_id = await _add_member(db_session, UUID(WORKSPACE_A))

    r = await client.patch(
        f"/api/v1/workspaces/{WORKSPACE_A}/members/{membership_id}",
        headers=bearer(),
        json={"role": "admin"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"

    stored = (
        await db_session.execute(
            text("SELECT role FROM workspace_memberships WHERE membership_id = :mid"),
            # 不要再用 UUID() 包一层：asyncpg 返回的是 pgproto.UUID，
            # 它的 __init__ 和标准库不同，UUID(它) 会崩。
            {"mid": membership_id},
        )
    ).scalar_one()
    assert stored == "admin"


async def test_cross_workspace_membership_is_404(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """拿 B 区的 membership_id 去 A 区的路由改角色：404，不是 200 也不是 403。"""
    _, b_membership = await _add_member(db_session, UUID(WORKSPACE_B))

    r = await client.patch(
        f"/api/v1/workspaces/{WORKSPACE_A}/members/{b_membership}",
        headers=bearer(),
        json={"role": "admin"},
    )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    # B 区那行必须原封不动。
    stored = (
        await db_session.execute(
            text("SELECT role FROM workspace_memberships WHERE membership_id = :mid"),
            {"mid": b_membership},
        )
    ).scalar_one()
    assert stored == "member"


async def test_non_admin_cannot_change_roles(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    guest_id, _ = await _add_member(db_session, UUID(WORKSPACE_A), role="guest")
    _, target_membership = await _add_member(db_session, UUID(WORKSPACE_A))

    r = await client.patch(
        f"/api/v1/workspaces/{WORKSPACE_A}/members/{target_membership}",
        headers={
            "Authorization": f"Bearer {make_token(sub=str(guest_id), workspace_id=WORKSPACE_A)}"
        },
        json={"role": "admin"},
    )
    assert r.status_code == 404, r.text


async def test_owner_membership_role_is_immutable_here(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """不能让 owner 在这个接口里被降级：转移所有权要连带改 workspaces.owner_principal_id。"""
    owner_membership = (
        await db_session.execute(
            text(
                "SELECT membership_id FROM workspace_memberships "
                "WHERE workspace_id = :ws AND user_id = :pid"
            ),
            {"ws": UUID(WORKSPACE_A), "pid": UUID(WORKSPACE_A)},
        )
    ).scalar_one()

    r = await client.patch(
        f"/api/v1/workspaces/{WORKSPACE_A}/members/{owner_membership}",
        headers=bearer(),
        json={"role": "member"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
