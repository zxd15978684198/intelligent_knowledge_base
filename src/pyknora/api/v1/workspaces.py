"""任务10：部门与角色权限管理（最小集）。

只做一件事：把某个成员的角色改掉。**不做多角色树、不做权限编辑器**——
角色权限点已在 `role_grants` 里留了位置，等真的需要再长。

三个边界条件，漏任何一个都算越权：

1. 路径上的 `ws_id` 必须等于 `scope.workspace_id`：否则 A 区管理员可以拿 B 区的
   membership_id 去改人。这里回 404 而不是 403，理由同全仓：403 等于承认那一行存在。
2. 操作者自己必须是该空间的 owner/admin。用 `scope` 查自己的成员关系，不信任请求体里
   任何"我是管理员"的字段。
3. `UPDATE ... WHERE workspace_id = :ws AND membership_id = :mid` 影响 0 行即 404，
   不靠"先 SELECT 再 UPDATE"——中间那一瞬间行可能被删掉。
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.api.deps import get_scope, get_session
from pyknora.core.errors import AppError, ErrorCode, not_found
from pyknora.core.scope import Scope
from pyknora.db.repos.principals import SqlIdentityRepo

router = APIRouter(tags=["workspaces"])

# `owner` 不在可赋值集合里：转移所有权是另一件事（要连带改 workspaces.owner_principal_id），
# 顺手在一个"改角色"接口里做，迟早出现两个 owner 或零个 owner。
AssignableRole = Literal["admin", "member", "guest"]

# 角色判定的**唯一**定义在代码里，不在 `role_grants` 表：`0003_identity_acl.sql` 建了
# 那张表并写入默认角色，但当前没有任何代码读取它（纯预留）。接表意味着每次权限判定多
# 一次 join 或多一份缓存，而收益是"4 个角色、单维度"——为零，ADR-0006 的备选方案里已经
# 算过这笔账。想改权限改这里；改 role_grants 不会生效。
_ADMIN_ROLES = frozenset({"owner", "admin"})


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
    if actor is None or actor.role not in _ADMIN_ROLES:
        # 用 404 而不是 403：不告诉"你不是管理员"，只说"这条成员关系找不到"。
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
