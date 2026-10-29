"""成员管理 API 验收：列人、加人、移人、主动退出、转移所有权、身份自省。

这些用例都要真 PG：唯一约束（重复加人 409）、外键（角色必须存在）、复合主键、
以及 `transfer_ownership` 的三条 UPDATE 是否在同一个事务里生效，都是数据库行为。

越权语义在整份文件里保持一致：**跨区一律 404**（不承认那行存在），**没权限一律 403**
（身份是真的，只是不够格）。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B
from tests.api.tokens import bearer, make_token

REGISTER = "/api/v1/auth/register"
ME = "/api/v1/auth/me"

WS_A = UUID(str(WORKSPACE_A))
WS_B = UUID(str(WORKSPACE_B))


def _members_url(workspace_id: UUID = WS_A) -> str:
    return f"/api/v1/workspaces/{workspace_id}/members"


def _owner_url(workspace_id: UUID = WS_A) -> str:
    return f"/api/v1/workspaces/{workspace_id}/owner"


def _token(principal_id: UUID | str, workspace_id: UUID = WS_A) -> dict[str, str]:
    """给"非 owner"的主体造请求头。owner 用 `bearer()`，因为它的 sub 就是工作区 id。"""
    token = make_token(sub=str(principal_id), workspace_id=str(workspace_id))
    return {"Authorization": f"Bearer {token}"}


async def _register(client: httpx.AsyncClient, username: str) -> UUID:
    """注册一个真实 principal（走公开 API，不手插表——插表会绕过密码哈希等约束）。"""
    response = await client.post(
        REGISTER,
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "s3cret-pass",
            "display_name": username.title(),
        },
    )
    assert response.status_code == 201, response.text
    return UUID(str(response.json()["principal_id"]))


async def _add_member(
    client: httpx.AsyncClient,
    principal_id: UUID,
    role: str = "member",
    *,
    workspace_id: UUID = WS_A,
) -> httpx.Response:
    return await client.post(
        _members_url(workspace_id),
        headers=bearer(str(workspace_id)),
        json={"user_id": str(principal_id), "role": role},
    )


async def _membership_id(db_session: Any, principal_id: UUID, workspace_id: UUID = WS_A) -> UUID:
    row = await db_session.execute(
        text(
            "SELECT membership_id FROM workspace_memberships "
            "WHERE workspace_id = :ws AND user_id = :pid"
        ),
        {"ws": workspace_id, "pid": principal_id},
    )
    return UUID(str(row.scalar_one()))


async def _members_with_role(db_session: Any, workspace_id: UUID = WS_A) -> dict[UUID, str]:
    rows = await db_session.execute(
        text("SELECT user_id, role FROM workspace_memberships WHERE workspace_id = :ws"),
        {"ws": workspace_id},
    )
    return {UUID(str(user_id)): role for user_id, role in rows.all()}


# ---------------------------------------------------------------- T1 列表


async def test_owner_lists_members(client: httpx.AsyncClient, clean_db: None) -> None:
    response = await client.get(_members_url(), headers=bearer())

    assert response.status_code == 200, response.text
    members = response.json()["members"]
    assert [m["role"] for m in members] == ["owner"]
    # 列表不带账号资料：有 manage_members 的人不需要全局用户目录。
    assert "username" not in members[0]
    assert "email" not in members[0]


# ---------------------------------------------------------------- T2 加人


async def test_add_member_then_duplicate_conflicts(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    bob = await _register(client, "bob")

    created = await _add_member(client, bob, "member")
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "member"

    duplicate = await _add_member(client, bob, "member")
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "CONFLICT"

    assert await _members_with_role(db_session) == {WS_A: "owner", bob: "member"}


async def test_add_unknown_principal_is_404(client: httpx.AsyncClient, clean_db: None) -> None:
    response = await _add_member(client, uuid4(), "member")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


async def test_add_member_with_owner_role_is_rejected(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    bob = await _register(client, "bob")

    response = await _add_member(client, bob, "owner")

    assert response.status_code == 422, response.text
    # 报错要指向正确入口：加人不能造出第二个 owner，转所有权才行。
    assert "transfer" in response.json()["error"]["message"]


async def test_add_member_with_unknown_role_is_rejected(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    bob = await _register(client, "bob")

    response = await _add_member(client, bob, "no_such_role")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["role_key"] == "no_such_role"


async def test_guest_cannot_list_members(client: httpx.AsyncClient, clean_db: None) -> None:
    guest = await _register(client, "gina")
    assert (await _add_member(client, guest, "guest")).status_code == 201

    response = await client.get(_members_url(), headers=_token(guest))

    assert response.status_code == 403, response.text
    assert "missing permission" in response.json()["error"]["message"]


# ---------------------------------------------------------------- T3 移人 / 退出


async def test_owner_membership_cannot_be_removed(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    owner_membership = await _membership_id(db_session, WS_A)

    response = await client.delete(f"{_members_url()}/{owner_membership}", headers=bearer())

    assert response.status_code == 422, response.text
    # 工作区必须还有 owner，否则 owner 权限点从此无人持有。
    assert await _members_with_role(db_session) == {WS_A: "owner"}


async def test_member_can_remove_self(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    bob = await _register(client, "bob")
    assert (await _add_member(client, bob, "member")).status_code == 201
    bob_membership = await _membership_id(db_session, bob)

    response = await client.delete(f"{_members_url()}/{bob_membership}", headers=_token(bob))

    assert response.status_code == 204, response.text
    assert await _members_with_role(db_session) == {WS_A: "owner"}


async def test_member_cannot_remove_someone_else(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    bob = await _register(client, "bob")
    carol = await _register(client, "carol")
    assert (await _add_member(client, bob, "member")).status_code == 201
    assert (await _add_member(client, carol, "member")).status_code == 201
    carol_membership = await _membership_id(db_session, carol)

    response = await client.delete(f"{_members_url()}/{carol_membership}", headers=_token(bob))

    # 404 而不是 403：没有管理权的人不该知道那行存在。
    assert response.status_code == 404, response.text
    assert carol in await _members_with_role(db_session)


async def test_remove_cross_workspace_membership_is_404(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    response = await client.delete(f"{_members_url()}/{uuid4()}", headers=bearer())

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------- T4 转移所有权


async def test_owner_transfers_ownership(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    bob = await _register(client, "bob")
    assert (await _add_member(client, bob, "admin")).status_code == 201

    response = await client.put(
        _owner_url(), headers=bearer(), json={"owner_principal_id": str(bob)}
    )

    assert response.status_code == 204, response.text
    # 三条写入必须同时生效：workspaces 指针 + 两个成员行。
    assert await _members_with_role(db_session) == {WS_A: "admin", bob: "owner"}
    owner = await db_session.execute(
        text("SELECT owner_principal_id FROM workspaces WHERE workspace_id = :ws"),
        {"ws": WS_A},
    )
    assert UUID(str(owner.scalar_one())) == bob


async def test_non_owner_cannot_transfer_ownership(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    bob = await _register(client, "bob")
    assert (await _add_member(client, bob, "admin")).status_code == 201

    response = await client.put(
        _owner_url(),
        headers=_token(bob),
        json={"owner_principal_id": str(bob)},
    )

    assert response.status_code == 403, response.text


async def test_transfer_to_non_member_is_rejected(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    stranger = await _register(client, "stranger")

    response = await client.put(
        _owner_url(), headers=bearer(), json={"owner_principal_id": str(stranger)}
    )

    assert response.status_code == 422, response.text
    # 关键：不能把 owner_principal_id 指到一个非成员身上（不可恢复的坏状态）。
    assert await _members_with_role(db_session) == {WS_A: "owner"}


# ---------------------------------------------------------------- T5/T6 自省与诊断


async def test_me_returns_role_and_permissions(client: httpx.AsyncClient, clean_db: None) -> None:
    response = await client.get(ME, headers=bearer())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["role"] == "owner"
    assert body["workspace_id"] == str(WS_A)
    # owner 是 15 个权限点全有（见 services/auth/permissions.py 的 SEED）。
    assert len(body["permissions"]) == 15


async def test_me_rejects_token_of_workspace_not_joined(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    # owner 只在 A 区；拿 A 区的身份去 B 区调自省 → 403（身份真，但不属于那个区）。
    response = await client.get(ME, headers=_token(WS_A, WS_B))

    assert response.status_code in {401, 403}, response.text


async def test_member_permissions_diagnostics(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    guest = await _register(client, "gina")
    assert (await _add_member(client, guest, "guest")).status_code == 201
    guest_membership = await _membership_id(db_session, guest)

    response = await client.get(
        f"{_members_url()}/{guest_membership}/permissions", headers=bearer()
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["role"] == "guest"
    # guest 是只读档：7 个权限点，且一个写权限都没有（`query.run`/`chat.run` 是读路径）。
    assert len(body["permissions"]) == 7
    assert not [p for p in body["permissions"] if p.endswith(".write") or p.endswith(".delete")]
    assert not [p for p in body["permissions"] if ".manage_" in p]


@pytest.mark.parametrize("path", ["members", "owner"])
async def test_cross_workspace_path_is_404(
    client: httpx.AsyncClient, clean_db: None, path: str
) -> None:
    url = f"/api/v1/workspaces/{WS_B}/{path}"

    if path == "owner":
        response = await client.put(
            url, headers=bearer(), json={"owner_principal_id": str(uuid4())}
        )
    else:
        response = await client.get(url, headers=bearer())

    assert response.status_code == 404, response.text
