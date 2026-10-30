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
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pyknora.core.scope import WORKSPACE_A
from pyknora.db.repos.principals import SqlIdentityRepo
from pyknora.services.auth.permissions import (
    DOCUMENT_WRITE,
    PLATFORM_MANAGE_ROLES,
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


async def _membership_id(db_session: Any, principal_id: UUID) -> str:
    """取成员行 id（PATCH/DELETE 成员要用它，不是 principal_id）。"""
    return str(
        (
            await db_session.execute(
                text(
                    "SELECT membership_id FROM workspace_memberships "
                    "WHERE workspace_id = :ws AND user_id = :pid"
                ),
                {"ws": WORKSPACE_A_ID, "pid": principal_id},
            )
        ).scalar_one()
    )


async def _ensure_role(db_session: Any, role_key: str, permissions: list[str]) -> None:
    """幂等造角色：角色目录不在 `clean_db` 的清场清单里，重跑时同名角色可能已存在。"""
    repo = SqlIdentityRepo(db_session)
    if not await repo.role_exists(role_key):
        assert await repo.create_role(role_key=role_key) is True
    assert await repo.replace_role_grants(role_key=role_key, permissions=permissions) is True
    await db_session.commit()


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


# ------------------------------------------------------------------- 目录写入是平台动作
# `A1`：`roles` / `role_grants` 是**全局表**（`0003_identity_acl.sql` 里没有
# `workspace_id`），所以改它们的动作天然是平台级的。三条写端点改挂
# `platform.manage_roles`——这个权限点没有任何角色持有，因此租户侧（含 owner、
# admin）一律 403，跨租户改写全局矩阵的路径被关掉。
#
# 代价：这三条端点从 HTTP 侧不可达，它们内部的参数校验（角色名格式、owner 保护、
# 未知权限点）也一起封存，等平台后台需要时再以平台身份开放——届时要补的是"谁持有
# 平台权限"，那条断言已经钉在 `tests/unit/test_permissions.py` 里。

_WRITES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("PUT", f"{ROLES_URL}/member/permissions", {"permissions": []}),
    ("POST", ROLES_URL, {"role_key": "should_not_exist_role", "permissions": []}),
    ("DELETE", f"{ROLES_URL}/guest", None),
]


async def _headers_for(db_session: Any, role: str) -> dict[str, str]:
    """owner 用真 owner 的 token（最有说服力），其余角色现造一个。"""
    if role == "owner":
        return bearer()
    return _as(await _add_member(db_session, role=role))


@pytest.mark.parametrize("role", ["owner", "admin", "member", "guest"])
@pytest.mark.parametrize(("verb", "url", "body"), _WRITES)
async def test_role_catalog_writes_are_platform_only(
    client: httpx.AsyncClient,
    clean_db: None,
    db_session: Any,
    role: str,
    verb: str,
    url: str,
    body: dict[str, Any] | None,
) -> None:
    """四个角色 × 三个写端点 = 12 条全 403，且**库里一点没变**。

    只断言状态码不够：依赖抛错之前若已经写了库，403 就成了遮掩。
    """
    response = await client.request(
        verb, url, headers=await _headers_for(db_session, role), json=body
    )
    assert response.status_code == 403, response.text
    assert PLATFORM_MANAGE_ROLES in response.json()["error"]["message"]

    repo = SqlIdentityRepo(db_session)
    assert await repo.role_exists("should_not_exist_role") is False, "POST 不该建出角色"
    assert await repo.role_exists("guest") is True, "DELETE 不该删掉角色"
    assert DOCUMENT_WRITE in await repo.list_permissions_for_role("member"), "PUT 不该改矩阵"


async def test_catalog_change_takes_effect_on_next_request(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """改矩阵不用发版：去掉 `member` 的 `document.write`，同一个 token 下一次请求立即 403。

    `A1` 之后改矩阵的入口是平台运维直接动表，所以这里用 repo 造现场。守的仍是那句
    话——权限的唯一真相是 `role_grants`，不是 JWT、不是进程缓存、不是硬编码角色集。
    """
    member_id = await _add_member(db_session)
    headers = _as(member_id)
    assert (await _upload(client, headers)).status_code == 202

    repo = SqlIdentityRepo(db_session)
    current = await repo.list_permissions_for_role("member")
    assert DOCUMENT_WRITE in current, "前置：默认矩阵里 member 应能写文档"
    narrowed = [permission for permission in current if permission != DOCUMENT_WRITE]
    assert await repo.replace_role_grants(role_key="member", permissions=narrowed) is True
    await db_session.commit()

    blocked = await _upload(client, headers)
    assert blocked.status_code == 403, blocked.text
    assert DOCUMENT_WRITE in blocked.json()["error"]["message"]

    # 读目录确认是落库结果，而不是内存里的一致性幻觉
    catalog = await client.get(ROLES_URL, headers=bearer())
    by_role = {row["role_key"]: row["permissions"] for row in catalog.json()["roles"]}
    assert DOCUMENT_WRITE not in by_role["member"]


async def test_catalog_crud_out_of_band(db_session: Any) -> None:
    """平台运维绕过 HTTP 直接动表：建角色 / 挂权限 / 不覆盖 / 删空角色。"""
    await _ensure_role(db_session, "crud_probe", ["document.read"])
    repo = SqlIdentityRepo(db_session)

    assert await repo.create_role(role_key="crud_probe") is False, "重复建不能静默覆盖"
    assert (
        await repo.replace_role_grants(
            role_key="crud_probe", permissions=["chunk.read", "document.read"]
        )
        is True
    )
    assert await repo.replace_role_grants(role_key="nonexistent_role", permissions=[]) is False
    await db_session.commit()

    assert sorted(await repo.list_permissions_for_role("crud_probe")) == [
        "chunk.read",
        "document.read",
    ]
    assert await repo.delete_role("crud_probe") is True
    await db_session.commit()
    assert await repo.delete_role("crud_probe") is False


async def test_new_role_is_assignable_without_any_migration(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """新建 `auditor` → 指派给成员 → 立刻按它的矩阵判权。建角色动表，指派仍走 HTTP。"""
    await _ensure_role(db_session, "auditor", ["chunk.read", "document.read"])

    member_id = await _add_member(db_session)
    patched = await client.patch(
        f"/api/v1/workspaces/{WORKSPACE_A}/members/{await _membership_id(db_session, member_id)}",
        headers=bearer(),
        json={"role": "auditor"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["role"] == "auditor"

    # 只读角色上传被拒——证明判权走的是新角色的矩阵，不是某个内置角色的兜底
    blocked = await _upload(client, _as(member_id))
    assert blocked.status_code == 403, blocked.text
    assert DOCUMENT_WRITE in blocked.json()["error"]["message"]


async def test_deleting_a_role_in_use_is_blocked_by_foreign_key(db_session: Any) -> None:
    """还有人挂在这个角色上就删不掉——由数据库 `ON DELETE RESTRICT` 兜底。

    应用层「先 count 再删」有窗口（中间仍可能有人被加进来），所以真正的保证必须在
    DDL 上。这里直接撞外键，确认那不是一条只存在于文档里的约束。
    """
    await _ensure_role(db_session, "fk_probe", [])
    repo = SqlIdentityRepo(db_session)
    member_id = await _add_member(db_session, role="fk_probe")
    assert await repo.count_role_members("fk_probe") == 1

    with pytest.raises(IntegrityError):
        await repo.delete_role("fk_probe")
    await db_session.rollback()

    # 收尾：把成员移出去再删角色，保证同一会话里重跑仍然成立
    membership_id = UUID(await _membership_id(db_session, member_id))
    assert (
        await repo.delete_membership(workspace_id=WORKSPACE_A_ID, membership_id=membership_id)
        is True
    )
    assert await repo.delete_role("fk_probe") is True
    await db_session.commit()


async def test_assigning_a_role_that_does_not_exist_is_rejected(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """外键是 `ON DELETE RESTRICT` 的数据库保证，接口层要给出可读的 422 而不是 500。"""
    member_id = await _add_member(db_session)
    r = await client.patch(
        f"/api/v1/workspaces/{WORKSPACE_A}/members/{await _membership_id(db_session, member_id)}",
        headers=bearer(),
        json={"role": "auditor_typo"},
    )
    assert r.status_code == 422, r.text
    assert "unknown role" in r.text
