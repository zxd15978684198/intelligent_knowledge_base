"""`role_grants` 是否**真的**在判权：改表即改权限，不改代码、不重启。

这条用例验证的不是某个端点的业务，而是"接入"本身。最关键的是
`test_role_permission_endpoints_are_guarded_and_effective` 的后半段：owner 在**库层面**
把 `member` 的 `document.write` 撤掉，然后用同一个成员 token 再上传一次。它红的所有原因
都值得看——

- 权限被塞进了 `Scope`/JWT（一次签发管到过期）→ 撤权不生效；
- 权限被进程级缓存 → 撤权不生效；
- 判权仍然读 `_ADMIN_ROLES` 这类硬编码集合 → 撤权对整个角色都没影响。

反过来，它通过就说明"权限的唯一真相是那张表"，这才叫落地。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text

from pyknora.core.scope import WORKSPACE_A
from pyknora.db.repos.principals import SqlIdentityRepo
from pyknora.services.auth.permissions import (
    DOCUMENT_WRITE,
    WORKSPACE_MANAGE_ROLES,
)
from tests.api.tokens import KB_ID, bearer, make_token

WORKSPACE_A_ID = UUID(str(WORKSPACE_A))
ROLES_URL = f"/api/v1/workspaces/{WORKSPACE_A}/roles"


def _as(principal_id: UUID) -> dict[str, str]:
    token = make_token(sub=str(principal_id), workspace_id=str(WORKSPACE_A))
    return {"Authorization": f"Bearer {token}"}


async def _add_member(db_session: Any, role: str = "member") -> UUID:
    """造一个已注册的成员并加入 A 区。返回 principal_id。"""
    principal_id = uuid4()
    suffix = principal_id.hex[:8]
    await db_session.execute(
        text(
            "INSERT INTO principals (principal_id, username, email, password_hash) "
            "VALUES (:pid, :u, :e, 'test-fixture-not-a-login-hash')"
        ),
        {"pid": principal_id, "u": f"user_{suffix}", "e": f"{suffix}@test.local"},
    )
    await SqlIdentityRepo(db_session).add_member(
        workspace_id=WORKSPACE_A_ID, user_id=principal_id, role=role
    )
    await db_session.commit()
    return principal_id


async def _upload(client: httpx.AsyncClient, headers: dict[str, str]) -> httpx.Response:
    return await client.post(
        f"/api/v1/knowledge-bases/{KB_ID}/documents",
        headers=headers,
        files={"file": ("note.md", b"# note\n\nhello pyknora\n", "text/markdown")},
    )


async def test_member_keeps_write_access_from_seed(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """默认矩阵必须与接入前的行为一致：普通成员照旧能上传。"""
    member_id = await _add_member(db_session)
    r = await _upload(client, _as(member_id))
    assert r.status_code == 202, r.text


async def test_guest_is_read_only(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """`guest` 从"没人用"变成真的有语义：只读，上传被拒且点名缺哪个权限点。"""
    guest_id = await _add_member(db_session, role="guest")

    readable = await client.get(f"/api/v1/knowledge-bases/{KB_ID}", headers=_as(guest_id))
    assert readable.status_code == 200, readable.text

    blocked = await _upload(client, _as(guest_id))
    assert blocked.status_code == 403, blocked.text
    assert DOCUMENT_WRITE in blocked.json()["error"]["message"]


async def test_revoking_document_write_takes_effect_on_next_request(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """撤掉 `member` 的 `document.write`，同一个 token 的下一次请求立即 403。"""
    member_id = await _add_member(db_session)
    headers = _as(member_id)
    assert (await _upload(client, headers)).status_code == 202

    repo = SqlIdentityRepo(db_session)
    assert await repo.remove_role_grant(role_key="member", permission=DOCUMENT_WRITE) is True
    await db_session.commit()

    blocked = await _upload(client, headers)
    assert blocked.status_code == 403, blocked.text
    assert DOCUMENT_WRITE in blocked.json()["error"]["message"]


async def test_role_permission_endpoints_are_guarded_and_effective(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    member_id = await _add_member(db_session)
    member_role_url = f"{ROLES_URL}/member/permissions"

    # 普通成员既读不了目录（manage_members），也改不了矩阵（manage_roles）
    assert (await client.get(ROLES_URL, headers=_as(member_id))).status_code == 403
    denied = await client.put(
        member_role_url, headers=_as(member_id), json={"permissions": [DOCUMENT_WRITE]}
    )
    assert denied.status_code == 403, denied.text

    # owner 读目录 → 整组替换 member 的权限点（去掉文档写）
    catalog = await client.get(ROLES_URL, headers=bearer())
    assert catalog.status_code == 200, catalog.text
    by_role = {row["role_key"]: row["permissions"] for row in catalog.json()["roles"]}
    assert DOCUMENT_WRITE in by_role["member"], "前置：默认矩阵里 member 应能写文档"

    narrowed = [permission for permission in by_role["member"] if permission != DOCUMENT_WRITE]
    replaced = await client.put(member_role_url, headers=bearer(), json={"permissions": narrowed})
    assert replaced.status_code == 200, replaced.text
    assert DOCUMENT_WRITE not in replaced.json()["permissions"]

    # 立刻生效：同一个成员 token 再上传就是 403
    blocked = await _upload(client, _as(member_id))
    assert blocked.status_code == 403, blocked.text
    assert DOCUMENT_WRITE in blocked.json()["error"]["message"]

    # 再读一次目录，确认是落库结果而不是内存里的一致性幻觉
    again = await client.get(ROLES_URL, headers=bearer())
    persisted = {row["role_key"]: row["permissions"] for row in again.json()["roles"]}
    assert DOCUMENT_WRITE not in persisted["member"]


async def test_unknown_permission_is_rejected(client: httpx.AsyncClient, clean_db: None) -> None:
    """拼错的权限点必须 400，不能"插进去了但永远匹配不上"。"""
    r = await client.put(
        f"{ROLES_URL}/guest/permissions",
        headers=bearer(),
        json={"permissions": ["document.reed"]},
    )
    # `INVALID_REQUEST` 在全局异常映射里是 422（见 `api/main.py` 的错误码→状态码表）
    assert r.status_code == 422, r.text
    assert "document.reed" in r.text


async def test_owner_permissions_cannot_be_replaced(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    """能改 owner 权限 = 能把唯一能恢复权限的人自锁死，必须不可逆地拒。"""
    r = await client.put(
        f"{ROLES_URL}/owner/permissions", headers=bearer(), json={"permissions": []}
    )
    assert r.status_code == 422, r.text


async def test_member_cannot_widen_own_role(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """提权路径：普通成员改不了 `member` 这个角色，哪怕改的是自己所在的角色。"""
    member_id = await _add_member(db_session)
    r = await client.put(
        f"{ROLES_URL}/member/permissions",
        headers=_as(member_id),
        json={"permissions": [WORKSPACE_MANAGE_ROLES]},
    )
    assert r.status_code == 403, r.text


async def test_member_can_read_own_permissions(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """自省接口不要求管理权限：每个人都能看自己有什么。"""
    member_id = await _add_member(db_session)
    r = await client.get(f"/api/v1/workspaces/{WORKSPACE_A}/permissions", headers=_as(member_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "member"
    assert DOCUMENT_WRITE in body["permissions"]
    assert WORKSPACE_MANAGE_ROLES not in body["permissions"]
