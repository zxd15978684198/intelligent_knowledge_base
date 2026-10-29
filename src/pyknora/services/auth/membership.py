"""成员关系判定与事务级工作区上下文。

这里是 `api/deps.py` 曾经那个 `return True` 占位对应的真实实现。三件事：

1. `load_membership`：这个人是不是这个空间的成员。**唯一**的授权空洞就在这一行查询里，
   把 SQL 写对（同时带 workspace_id 与 user_id）比任何重试/缓存都重要。
2. `set_workspace_context`：把当前 scope 的工作区写进事务局部变量 `app.workspace_id`，
   给 RLS 兜底用。必须用 `set_config(..., is_local=true)` 而不是 `SET`——
   `SET` 是会话级的，连接池复用后上一个请求的工作区会漏给下一个请求。
3. `create_default_workspace_for`：注册时给新用户建一个自己的空间。注册与建空间
   必须在同一事务：分开提交会出现"用户建好了但没有任何可用空间"的半小时空窗。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.db.repos.principals import SqlIdentityRepo
from pyknora.models.tables import PrincipalRow, WorkspaceRow


async def load_membership(session: AsyncSession, principal_id: UUID, workspace_id: UUID) -> bool:
    """无成员关系即无权限（fail-closed），不区分"空间不存在"与"不是成员"。

    这条查询**故意**每个请求都跑一次，不做缓存、也不把 role 塞进令牌：
    它同时承担"改角色/停用账号下一次请求即生效"的职责（`find_membership` 里 join 了
    `principals.status`）。缓存它省下的是几毫秒，换来的是最长一个 TTL 的授权滞后，
    方向反了。
    """
    membership = await SqlIdentityRepo(session).find_membership(workspace_id, principal_id)
    return membership is not None


async def load_permissions(
    session: AsyncSession, *, principal_id: UUID, workspace_id: UUID
) -> frozenset[str]:
    """该成员在本工作区拥有的权限点集合。

    链路：`workspace_memberships.role` → `roles` → `role_grants` → 权限点。
    非成员（含账号被停用）→ **空集**，即 fail-closed：拿不到权限点就没有任何动作权限。

    两个 id **强制关键字传参**：它们的类型相同（都是 UUID），调用方写成位置参数时，
    传反了不会有任何报错，只会稳定返回空集——排起来像"权限没生效"，实际是查错了人。
    这条规则是被踩出来的，不是风格偏好。

    和 `load_membership` 一样是刻意"每请求查一次、不缓存"：`role_grants` 是可改配置，
    缓存会让"撤掉某人的写权限"滞后一个 TTL 才生效。真正的成本是两次查询
    （成员关系 + 权限点），但它们都在同一个事务、同一条连接上，且都由
    `get_scope`/`get_permissions` 的依赖缓存兜住——同一请求里多处 `require_permission`
    只查一次。
    """
    repo = SqlIdentityRepo(session)
    membership = await repo.find_membership(workspace_id, principal_id)
    if membership is None:
        return frozenset()
    return frozenset(await repo.list_permissions_for_role(membership.role))


async def set_workspace_context(session: AsyncSession, workspace_id: UUID) -> None:
    """给当前事务打上 `app.workspace_id`，供 RLS 策略读取。

    为什么用 `set_config` 而不是 `SET LOCAL app.workspace_id = ...`：
    PostgreSQL 的 `SET` 语法**不接受参数占位符**，拼字符串又会把 UUID 变成 SQL 注入面；
    `set_config(name, value, is_local)` 收参数，且 `is_local=true` 与会话级设置等价于
    `SET LOCAL`——事务结束自动失效。

    这一行只是兜底开关：正常情况下应用连接是表 owner，RLS 本就不生效。
    真正生效的是用 `pyknora_app` 这类非 owner 角色直连时（见 tests/integration/test_rls.py）。
    """
    await session.execute(
        text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)}
    )


async def create_default_workspace_for(
    session: AsyncSession, principal: PrincipalRow
) -> WorkspaceRow:
    """为新注册用户建一个默认空间，并把他登记为 owner。"""
    repo = SqlIdentityRepo(session)
    display = principal.display_name or principal.username
    workspace = await repo.create_workspace(
        name=f"{display} 的工作区", owner_principal_id=principal.principal_id
    )
    await repo.add_member(
        workspace_id=workspace.workspace_id, user_id=principal.principal_id, role="owner"
    )
    return workspace


async def resolve_default_workspace(session: AsyncSession, principal_id: UUID) -> UUID | None:
    return await SqlIdentityRepo(session).find_default_workspace(principal_id)
