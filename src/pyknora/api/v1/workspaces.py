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

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.api.deps import get_scope, get_session, require_permission
from pyknora.core.errors import AppError, ErrorCode, not_found
from pyknora.core.scope import Scope
from pyknora.db.repos.principals import SqlIdentityRepo
from pyknora.services.auth.membership import load_permissions
from pyknora.services.auth.permissions import (
    ROLE_KEYS,
    WORKSPACE_MANAGE_MEMBERS,
    WORKSPACE_MANAGE_ROLES,
    is_known_permission,
)

router = APIRouter(tags=["workspaces"])

# `owner` 不在可赋值集合里：转移所有权是另一件事（要连带改 workspaces.owner_principal_id），
# 顺手在一个"改角色"接口里做，迟早出现两个 owner 或零个 owner。
AssignableRole = Literal["admin", "member", "guest"]


class MemberRoleUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: AssignableRole


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
    """把 `role_grants` 的现状原样返回，不做任何"应有权限"的补充推导。"""
    if ws_id != scope.workspace_id:
        raise not_found("workspace")

    grants = await SqlIdentityRepo(session).list_role_grants()
    per_role: dict[str, list[str]] = {key: [] for key in ROLE_KEYS}
    for role_key, permission in grants:
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
    if role_key not in ROLE_KEYS:
        raise not_found("role")
    if role_key == "owner":
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

    repo = SqlIdentityRepo(session)
    await repo.replace_role_grants(role_key=role_key, permissions=payload.permissions)
    await session.commit()
    return RolePermissionView(
        role_key=role_key,
        permissions=sorted(set(payload.permissions)),
    )
