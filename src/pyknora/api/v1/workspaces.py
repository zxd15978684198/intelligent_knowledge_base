"""任务10 + 权限点接入：工作区成员角色与角色权限管理。

判权源**只有一个**：`role_grants` 表（`roles` 是它的名字目录）。运行期链路是

    workspace_memberships.role → roles.role_key → role_grants.permission → 权限点集合

原先那套 `_ADMIN_ROLES` 闭集硬编码已经删掉：同一个问题只允许有一个答案，留在代码里
就会和表里的数据打架（改表不生效、改代码丢数据）。

三个边界条件，漏任何一个都算越权：

1. 路径上的 `ws_id` 必须等于 `scope.workspace_id`：否则 A 区管理员可以拿 B 区的
   membership_id 去改人。这里回 404 而不是 403，理由同全仓：403 等于承认那一行存在。
2. 操作者自己必须有 `workspace.manage_members` / `workspace.manage_roles` 权限。权限从
   `role_grants` 现查，不信任请求体里任何"我是管理员"的字段。
3. `UPDATE ... WHERE workspace_id = :ws AND membership_id = :mid` 影响 0 行即 404，
   不靠"先 SELECT 再 UPDATE"——中间那一瞬间行可能被删掉。

`owner` 角色是例外：它的权限**不可改**（PUT 会拒）。能改 owner 权限就等于能把
唯一能恢复权限的人自锁死，这是不可逆的。
"""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.api.deps import get_scope, get_session, require_permission
from pyknora.core.errors import AppError, ErrorCode, not_found
from pyknora.core.scope import Scope
from pyknora.db.repos.principals import SqlIdentityRepo
from pyknora.services.auth.membership import load_permissions
from pyknora.services.auth.permissions import (
    OWNER_ROLE,
    WORKSPACE_MANAGE_MEMBERS,
    WORKSPACE_MANAGE_ROLES,
    is_known_permission,
)

router = APIRouter(tags=["workspaces"])

# 角色名不再枚举在代码里：`0008_roles_are_data.sql` 之后，合法取值由 `roles` 表决定。
# 代码里只剩两条硬规则——不能在这个接口把成员改成 `owner`（转移所有权要连带改
# `workspaces.owner_principal_id`），以及角色名必须是 `roles` 里真实存在的一行。
_ROLE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class MemberRoleUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str


class MembershipDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    membership_id: UUID
    workspace_id: UUID
    user_id: UUID
    role: str


@router.patch(
    "/workspaces/{ws_id}/members/{membership_id}",
    response_model=MembershipDetail,
    summary="修改成员角色（受工作区边界约束）",
)
async def update_member_role(
    ws_id: UUID,
    membership_id: UUID,
    payload: MemberRoleUpdate,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MembershipDetail:
    if ws_id != scope.workspace_id:
        raise not_found("workspace")

    repo = SqlIdentityRepo(session)

    actor = await repo.find_membership(ws_id, scope.principal_id)
    # 判权走 role_grants。这里**不**用 `require_permission` 依赖：那个会回 403，而本接口
    # 对"不存在"与"无权限"一直给同一个 404 响应（403 等于承认那一行存在）。
    permissions = frozenset()
    if actor is not None:
        permissions = await load_permissions(
            session, principal_id=scope.principal_id, workspace_id=ws_id
        )
    if WORKSPACE_MANAGE_MEMBERS not in permissions:
        raise not_found("membership")

    target = await repo.find_membership_by_id(ws_id, membership_id)
    if target is None:
        raise not_found("membership")
    if target.role == "owner":
        raise AppError(ErrorCode.INVALID_REQUEST, "owner role cannot be changed here")

    # 角色名必须是 `roles` 表里存在的一行。不查库就让外键去拦的话，用户拿到的是 500
    # 而不是"你拼错了角色名"。
    if payload.role == OWNER_ROLE:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "owner role cannot be assigned here; transfer ownership instead",
            details={"role": payload.role},
        )
    if not await repo.role_exists(payload.role):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "unknown role",
            details={"role": payload.role},
        )

    updated = await repo.update_member_role(
        workspace_id=ws_id, membership_id=membership_id, role=payload.role
    )
    if not updated:
        raise not_found("membership")
    await session.commit()

    return MembershipDetail(
        membership_id=target.membership_id,
        workspace_id=target.workspace_id,
        user_id=target.user_id,
        role=payload.role,
    )


# --------------------------------------------------------------------------- 权限点目录


class RolePermissionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    role_key: str
    permissions: list[str]


class RoleCatalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    roles: list[RolePermissionView]


class RolePermissionsPut(BaseModel):
    model_config = ConfigDict(frozen=True)

    permissions: list[str]


class PermissionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace_id: UUID
    role: str
    permissions: list[str]


@router.get(
    "/workspaces/{ws_id}/permissions",
    response_model=PermissionSummary,
    summary="我在这个工作区拥有的权限点",
)
async def my_permissions(
    ws_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PermissionSummary:
    """客户端自省用：登录后先问一次"我能做什么"，不必把权限矩阵写死在前端。

    刻意**不**要求任何管理权限：每个人都能看自己的权限，这是最不容易出错的信息。
    """
    if ws_id != scope.workspace_id:
        raise not_found("workspace")

    repo = SqlIdentityRepo(session)
    membership = await repo.find_membership(ws_id, scope.principal_id)
    if membership is None:
        raise not_found("workspace")

    permissions = await load_permissions(
        session, principal_id=scope.principal_id, workspace_id=ws_id
    )
    return PermissionSummary(
        workspace_id=ws_id,
        role=membership.role,
        permissions=sorted(permissions),
    )


@router.get(
    "/workspaces/{ws_id}/roles",
    response_model=RoleCatalog,
    summary="角色与权限点目录",
    dependencies=[Depends(require_permission(WORKSPACE_MANAGE_MEMBERS))],
)
async def list_role_catalog(
    ws_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoleCatalog:
    """角色目录：**以 `roles` 表为准**，逐个附上它当前的权限点。

    不做任何"应有权限"的推导——`roles` 里没有 `document.write` 就是没有，不会因为
    "admin 应该什么都能干"被补上。一个刚建好、还没配权限点的角色也会出现在目录里
    （空数组），而不是隐身。
    """
    if ws_id != scope.workspace_id:
        raise not_found("workspace")

    repo = SqlIdentityRepo(session)
    roles = await repo.list_roles()
    per_role: dict[str, list[str]] = {role_key: [] for role_key, _ in roles}
    for role_key, permission in await repo.list_role_grants():
        per_role.setdefault(role_key, []).append(permission)
    return RoleCatalog(
        roles=[
            RolePermissionView(role_key=key, permissions=sorted(values))
            for key, values in sorted(per_role.items())
        ]
    )


@router.put(
    "/workspaces/{ws_id}/roles/{role_key}/permissions",
    response_model=RolePermissionView,
    summary="整组替换某个角色的权限点",
    dependencies=[Depends(require_permission(WORKSPACE_MANAGE_ROLES))],
)
async def replace_role_permissions(
    ws_id: UUID,
    role_key: str,
    payload: RolePermissionsPut,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RolePermissionView:
    """**整组替换**，而不是 grant/revoke 两条路径。

    理由与 `update_member_role` 一致：一组权限的终态是唯一的，用"替换"表达就不会出现
    "发了两条 revoke、只成功一条"的中间态，调用方也不必先读后写。表上仍是逐行增删。
    """
    if ws_id != scope.workspace_id:
        raise not_found("workspace")

    repo = SqlIdentityRepo(session)
    if not await repo.role_exists(role_key):
        raise not_found("role")
    if role_key == OWNER_ROLE:
        # 能改 owner 权限 = 能把唯一能恢复权限的人自锁死，不可逆，直接拒。
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "owner permissions are fixed",
            details={"role_key": role_key},
        )

    unknown = sorted({p for p in payload.permissions if not is_known_permission(p)})
    if unknown:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "unknown permission",
            details={"permissions": unknown},
        )

    await repo.replace_role_grants(role_key=role_key, permissions=payload.permissions)
    await session.commit()
    return RolePermissionView(
        role_key=role_key,
        permissions=sorted(set(payload.permissions)),
    )


class RoleCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    role_key: str
    description: str | None = None
    permissions: list[str] = Field(default_factory=list)


@router.post(
    "/workspaces/{ws_id}/roles",
    response_model=RolePermissionView,
    status_code=status.HTTP_201_CREATED,
    summary="新建角色（角色集合是数据，不是 DDL）",
    dependencies=[Depends(require_permission(WORKSPACE_MANAGE_ROLES))],
)
async def create_role(
    ws_id: UUID,
    payload: RoleCreate,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RolePermissionView:
    """建角色 = 往 `roles` 插一行 + 往 `role_grants` 插若干行，**不改代码、不加迁移**。

    这是这次改造的目的本身：`0008_roles_are_data.sql` 给成员表加了
    `role → roles(role_key)` 外键，所以新角色一旦建成，立刻可以被成员使用；
    在此之前，加角色必须同时改 DDL 的 CHECK 和 `workspaces.py` 里的 `Literal`。
    """
    if ws_id != scope.workspace_id:
        raise not_found("workspace")
    if not _ROLE_KEY_RE.match(payload.role_key):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "invalid role_key: expect ^[a-z][a-z0-9_]{0,63}$",
            details={"role_key": payload.role_key},
        )
    if payload.role_key == OWNER_ROLE:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "owner is built in and cannot be re-created",
            details={"role_key": payload.role_key},
        )

    unknown = sorted({p for p in payload.permissions if not is_known_permission(p)})
    if unknown:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "unknown permission",
            details={"permissions": unknown},
        )

    repo = SqlIdentityRepo(session)
    if not await repo.create_role(role_key=payload.role_key, description=payload.description):
        # 已存在就 409，不静默覆盖——覆盖会把那个角色现有的权限矩阵悄悄清空。
        raise AppError(
            ErrorCode.CONFLICT,
            "role already exists",
            details={"role_key": payload.role_key},
        )
    if payload.permissions:
        await repo.replace_role_grants(role_key=payload.role_key, permissions=payload.permissions)
    await session.commit()
    return RolePermissionView(
        role_key=payload.role_key,
        permissions=sorted(set(payload.permissions)),
    )


@router.delete(
    "/workspaces/{ws_id}/roles/{role_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除角色（仍有成员在用时拒绝）",
    dependencies=[Depends(require_permission(WORKSPACE_MANAGE_ROLES))],
)
async def delete_role(
    ws_id: UUID,
    role_key: str,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """删角色。`role_grants` 随角色级联删除，但成员表是 `ON DELETE RESTRICT`：

    还有人在用这个角色时删不掉——先给这些人换角色再删。让数据库拦，比在应用层
    "先 count 再删"可靠：中间那一下仍可能有人被加进这个角色。
    """
    if ws_id != scope.workspace_id:
        raise not_found("workspace")
    if role_key == OWNER_ROLE:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "owner role cannot be deleted",
            details={"role_key": role_key},
        )

    repo = SqlIdentityRepo(session)
    if not await repo.role_exists(role_key):
        raise not_found("role")
    members = await repo.count_role_members(role_key)
    if members:
        raise AppError(
            ErrorCode.CONFLICT,
            "role is still in use; reassign its members first",
            details={"role_key": role_key, "members": members},
        )
    await repo.delete_role(role_key)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
