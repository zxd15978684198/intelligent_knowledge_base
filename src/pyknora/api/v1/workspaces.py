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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.api.deps import get_scope, get_session, require_permission
from pyknora.core.errors import AppError, ErrorCode, not_found
from pyknora.core.scope import Scope
from pyknora.db.repos.principals import SqlIdentityRepo
from pyknora.services.auth.membership import load_permissions
from pyknora.services.auth.permissions import (
    OWNER_ROLE,
    PLATFORM_MANAGE_ROLES,
    WORKSPACE_MANAGE_MEMBERS,
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
    dependencies=[Depends(require_permission(PLATFORM_MANAGE_ROLES))],
)
async def replace_role_permissions(
    ws_id: UUID,
    role_key: str,
    payload: RolePermissionsPut,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RolePermissionView:
    """**整组替换**，而不是 grant/revoke 两条路径。

    **平台级**：`roles` / `role_grants` 是全局表（没有 workspace_id），一个租户改一次
    所有租户的判权都变。所以闸门是 `PLATFORM_MANAGE_ROLES`，而它没有任何角色持有
    ——任何租户调这个接口都是 403。`ws_id` 仍保留在路径上只为不改调用方形状。

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
    dependencies=[Depends(require_permission(PLATFORM_MANAGE_ROLES))],
)
async def create_role(
    ws_id: UUID,
    payload: RoleCreate,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RolePermissionView:
    """建角色 = 往 `roles` 插一行 + 往 `role_grants` 插若干行，**不改代码、不加迁移**。

    **平台级**：`roles` 是全局表，新建的角色对所有租户可见，所以闸门是
    `PLATFORM_MANAGE_ROLES`（无角色持有 → 租户侧恒 403）。

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
    dependencies=[Depends(require_permission(PLATFORM_MANAGE_ROLES))],
)
async def delete_role(
    ws_id: UUID,
    role_key: str,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """删角色。`role_grants` 随角色级联删除，但成员表是 `ON DELETE RESTRICT`：

    **平台级**：删掉的是全局角色，所有租户的同名角色一起消失（闸门
    `PLATFORM_MANAGE_ROLES`，无角色持有 → 租户侧恒 403）。

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


# ---------------------------------------------------------------- 成员管理
#
# 这一段补的是"成员管理几乎没做"的窟窿：原来只有"改某人角色"，没有加人、移人、
# 列人、转所有权。四条都遵守上面 docstring 里的三条边界，另外加一条新的语义约定：
#
# **移除 owner 的成员行必须被拒（422），要换 owner 只能走 `PUT .../owner`。**
# 允许直接删 owner 会立刻造出"工作区没有 owner"的坏状态：`workspaces.owner_principal_id`
# 还指着那个人，但他已不是成员，`owner` 那套权限点从此没有任何人能拿到，
# 而这个状态**不可恢复**——能恢复的唯一角色被别人握在手里。


class MemberAdd(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: UUID
    role: str = "member"


class MemberPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    members: list[MembershipDetail]


class MemberPermissionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    membership_id: UUID
    user_id: UUID
    role: str
    permissions: list[str]


class OwnerTransfer(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner_principal_id: UUID


@router.get(
    "/workspaces/{ws_id}/members",
    response_model=MemberPage,
    summary="成员列表（管理用）",
    dependencies=[Depends(require_permission(WORKSPACE_MANAGE_MEMBERS))],
)
async def list_members(
    ws_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberPage:
    """列出本区成员。需要 `workspace.manage_members`。

    刻意**不**返回 username / email：成员列表要回答的是"谁在这个区、什么角色"。把
    账号资料也带上，这个接口就变成撞库工具——有 manage_members 的人根本不需要
    全局用户目录来管理成员。
    """
    if ws_id != scope.workspace_id:
        raise not_found("workspace")
    rows = await SqlIdentityRepo(session).list_memberships(ws_id)
    return MemberPage(
        members=[
            MembershipDetail(
                membership_id=row.membership_id,
                workspace_id=ws_id,
                user_id=row.user_id,
                role=row.role,
            )
            for row in rows
        ]
    )


@router.post(
    "/workspaces/{ws_id}/members",
    response_model=MembershipDetail,
    status_code=status.HTTP_201_CREATED,
    summary="加成员（按已存在的 principal）",
    dependencies=[Depends(require_permission(WORKSPACE_MANAGE_MEMBERS))],
)
async def add_member(
    ws_id: UUID,
    payload: MemberAdd,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MembershipDetail:
    """把一个**已存在**的 principal 加进本区。

    没有邀请制：调用方必须已经知道对方的 `principal_id`（对方先注册过）。这样这个
    接口不会变成"按 email 探测账号是否存在"的枚举面——**不存在的 id 与已停用的 id
    回同一个 404**，因为区分它们等于对外确认"这个 id 存在过"。
    """
    if ws_id != scope.workspace_id:
        raise not_found("workspace")
    if payload.role == OWNER_ROLE:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "owner is not assignable; transfer ownership instead",
            details={"endpoint": f"PUT /api/v1/workspaces/{ws_id}/owner"},
        )

    repo = SqlIdentityRepo(session)
    principal = await repo.find_principal(payload.user_id)
    if principal is None or principal.status != "active":
        raise not_found("principal")
    if not await repo.role_exists(payload.role):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "unknown role",
            details={"role_key": payload.role},
        )
    if await repo.find_membership(ws_id, payload.user_id) is not None:
        raise AppError(
            ErrorCode.CONFLICT,
            "already a member",
            details={"user_id": str(payload.user_id)},
        )
    try:
        row = await repo.add_member(workspace_id=ws_id, user_id=payload.user_id, role=payload.role)
    except IntegrityError as exc:
        # 两个请求同时通过了上面的 SELECT：唯一约束是最后一道闸，把 500 翻译成 409。
        await session.rollback()
        raise AppError(
            ErrorCode.CONFLICT,
            "already a member",
            details={"user_id": str(payload.user_id)},
        ) from exc
    await session.commit()
    return MembershipDetail(
        membership_id=row.membership_id,
        workspace_id=ws_id,
        user_id=row.user_id,
        role=row.role,
    )


@router.delete(
    "/workspaces/{ws_id}/members/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="移除成员（自己移除自己 = 主动退出）",
)
async def remove_member(
    ws_id: UUID,
    membership_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """移除成员。需要 `workspace.manage_members`。

    - 移除 **owner** 的成员行一律 422：那会让工作区失去 owner 且不可恢复，换人走
      `PUT .../owner`（那条会把原 owner 降级，不是删除）。
    - 移除**自己**是允许的（主动退出），前提是权限点够——`member`/`guest` 没有
      `manage_members`，所以他们退出要靠这条规则的例外：见下面 `_can_leave_self`。
    """
    if ws_id != scope.workspace_id:
        raise not_found("workspace")

    repo = SqlIdentityRepo(session)
    target = await repo.find_membership_by_id(ws_id, membership_id)
    if target is None:
        raise not_found("membership")
    if target.role == OWNER_ROLE:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "owner membership cannot be removed; transfer ownership first",
            details={"membership_id": str(membership_id)},
        )

    if not await _can_remove(session, scope, target.user_id):
        # 够不着管理权、又不是在删自己，就是越权。404 而不是 403：不承认那行存在。
        raise not_found("membership")

    if not await repo.delete_membership(workspace_id=ws_id, membership_id=membership_id):
        raise not_found("membership")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _can_remove(session: AsyncSession, scope: Scope, target_user_id: UUID) -> bool:
    """管理权，或"目标就是自己"（主动退出）。

    这两条是**与**关系里的或：有管理权可以删任何人（owner 已在上文拦掉），没有管理权
    就只能删自己。把"删自己"单独授权，是为了让 member/guest 能退出工作区，而不必让
    每个角色都背上 `manage_members`。
    """
    if target_user_id == scope.principal_id:
        return True
    permissions = await load_permissions(
        session, principal_id=scope.principal_id, workspace_id=scope.workspace_id
    )
    return WORKSPACE_MANAGE_MEMBERS in permissions


@router.put(
    "/workspaces/{ws_id}/owner",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="转移工作区所有权",
    dependencies=[Depends(require_permission(WORKSPACE_MANAGE_MEMBERS))],
)
async def transfer_owner(
    ws_id: UUID,
    payload: OwnerTransfer,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """把所有权交给本区的另一个成员。

    三层校验，顺序不能换：

    1. 路径 `ws_id` 必须是自己的区（404）；
    2. **调用者必须是当前 owner**（403，不是 422——这不是参数错，是没资格）；
    3. 目标必须已经是本区成员（422）。少了第 3 条，`transfer_ownership` 会把
       `workspaces.owner_principal_id` 指到一个非成员身上，那是不可恢复的坏状态。
    """
    if ws_id != scope.workspace_id:
        raise not_found("workspace")

    repo = SqlIdentityRepo(session)
    workspace = await repo.find_workspace(ws_id)
    if workspace is None:
        raise not_found("workspace")
    if workspace.owner_principal_id != scope.principal_id:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "only the current owner can transfer ownership",
            details={"workspace_id": str(ws_id)},
        )
    if payload.owner_principal_id == scope.principal_id:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "already the owner",
            details={"principal_id": str(scope.principal_id)},
        )
    if await repo.find_membership(ws_id, payload.owner_principal_id) is None:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "target is not a member of this workspace",
            details={"principal_id": str(payload.owner_principal_id)},
        )

    moved = await repo.transfer_ownership(
        workspace_id=ws_id,
        from_principal_id=scope.principal_id,
        to_principal_id=payload.owner_principal_id,
    )
    if not moved:
        # 三个 UPDATE 已执行过一部分，必须整体回滚；否则留下"改了一半"的状态。
        await session.rollback()
        raise AppError(
            ErrorCode.CONFLICT,
            "ownership transfer raced with a concurrent change",
            details={"workspace_id": str(ws_id)},
        )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/workspaces/{ws_id}/members/{membership_id}/permissions",
    response_model=MemberPermissionView,
    summary="某个成员实际持有的权限点（诊断用）",
    dependencies=[Depends(require_permission(WORKSPACE_MANAGE_MEMBERS))],
)
async def member_permissions(
    ws_id: UUID,
    membership_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberPermissionView:
    """回答"他为什么点不了这个按钮"。

    与 `GET /workspaces/{ws}/permissions` 的区别是那个只看**自己**、且谁都能调；这个
    能看别人，所以必须 `manage_members`。权限点现算不缓存——`role_grants` 改完下一次
    请求就变，诊断接口如果读缓存就会给出与真实判权不一致的答案。
    """
    if ws_id != scope.workspace_id:
        raise not_found("workspace")

    repo = SqlIdentityRepo(session)
    target = await repo.find_membership_by_id(ws_id, membership_id)
    if target is None:
        raise not_found("membership")
    permissions = await load_permissions(session, principal_id=target.user_id, workspace_id=ws_id)
    return MemberPermissionView(
        membership_id=target.membership_id,
        user_id=target.user_id,
        role=target.role,
        permissions=sorted(permissions),
    )
