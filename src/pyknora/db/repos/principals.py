"""身份相关表的唯一查询入口。

`repos/` 每多一个文件就多一处"谁在写 SQL"的歧义，所以这里只有**一个类**：
成员的读、写、角色变更全在一个 `SqlIdentityRepo` 里。它只碰 5 张身份表
（principals / workspaces / workspace_memberships / roles / role_grants），
不碰 documents、chunks、knowledge_bases —— 那是别人拥有的数据。

为什么 `role` 用字符串而不是 Enum：表上已经是 CHECK 约束，再在 Python 侧建一套
Enum 只会出现两个真相源，加角色时两边都会漏改。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.models.tables import (
    PrincipalRow,
    RoleGrantRow,
    RoleRow,
    WorkspaceMembershipRow,
    WorkspaceRow,
)


class SqlIdentityRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------------------------------------------------------------- principals

    async def find_principal_by_username(self, username: str) -> PrincipalRow | None:
        stmt = select(PrincipalRow).where(PrincipalRow.username == username)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def find_principal_by_email(self, email: str) -> PrincipalRow | None:
        stmt = select(PrincipalRow).where(PrincipalRow.email == email)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def find_principal(self, principal_id: UUID) -> PrincipalRow | None:
        stmt = select(PrincipalRow).where(PrincipalRow.principal_id == principal_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create_principal(
        self,
        *,
        username: str,
        email: str,
        password_hash: str,
        display_name: str | None = None,
    ) -> PrincipalRow:
        """插入一行身份。唯一冲突交给数据库报，不靠"先查后插"。

        先查后插在并发下一定被穿透（两个请求同时查到"不存在"），所以调用方要把
        `IntegrityError` 当 409 处理，这里的查询只用于给出更友好的提示。
        """
        row = PrincipalRow(
            username=username,
            email=email,
            password_hash=password_hash,
            display_name=display_name,
            status="active",
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def touch_last_login(self, principal_id: UUID) -> None:
        # 用 now() 而不是 Python 时间：多实例部署时以数据库时钟为准，避免时钟漂移。
        await self._session.execute(
            update(PrincipalRow)
            .where(PrincipalRow.principal_id == principal_id)
            .values(last_login_at=func.now())
        )

    # ---------------------------------------------------------------- workspaces

    async def create_workspace(self, *, name: str, owner_principal_id: UUID) -> WorkspaceRow:
        row = WorkspaceRow(name=name, owner_principal_id=owner_principal_id, is_public=False)
        self._session.add(row)
        await self._session.flush()
        return row

    async def find_workspace(self, workspace_id: UUID) -> WorkspaceRow | None:
        stmt = select(WorkspaceRow).where(WorkspaceRow.workspace_id == workspace_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ---------------------------------------------------------------- memberships

    async def add_member(
        self, *, workspace_id: UUID, user_id: UUID, role: str = "member"
    ) -> WorkspaceMembershipRow:
        row = WorkspaceMembershipRow(workspace_id=workspace_id, user_id=user_id, role=role)
        self._session.add(row)
        await self._session.flush()
        return row

    async def find_membership(
        self, workspace_id: UUID, user_id: UUID
    ) -> WorkspaceMembershipRow | None:
        """成员关系是否存在**且账号仍可用**。`_load_membership` 的真实现就是这一条查询。

        为什么要 join `principals.status`：令牌里只有 `sub` 与 `exp`，签发之后身份状态
        的变化（停用/删除账号）不会回写进已发出的令牌。只查 `workspace_memberships`
        的话，被停用的人还能拿旧令牌把 TTL 用完（默认 1 小时）。带上 status 过滤后，
        停用在**下一次请求**就生效，不需要维护黑名单或 refresh token。

        副作用是期望行为：`workspaces.py` 复用本方法判定"操作者是否 admin"，
        于是被停用的管理员同时失去改角色的能力。
        """
        stmt = (
            select(WorkspaceMembershipRow)
            .join(
                PrincipalRow,
                PrincipalRow.principal_id == WorkspaceMembershipRow.user_id,
            )
            .where(
                WorkspaceMembershipRow.workspace_id == workspace_id,
                WorkspaceMembershipRow.user_id == user_id,
                PrincipalRow.status == "active",
            )
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def find_membership_by_id(
        self, workspace_id: UUID, membership_id: UUID
    ) -> WorkspaceMembershipRow | None:
        """按 membership_id 定位**且**限定工作区。

        只用 membership_id 一条主键查是常见的越权写法：A 区的管理员能改 B 区的角色，
        只要他拿到了那个 UUID。
        """
        stmt = select(WorkspaceMembershipRow).where(
            WorkspaceMembershipRow.workspace_id == workspace_id,
            WorkspaceMembershipRow.membership_id == membership_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def update_member_role(
        self, *, workspace_id: UUID, membership_id: UUID, role: str
    ) -> bool:
        """返回是否真的改到了行。影响 0 行 = 不存在或跨区，调用方据此报 404。"""
        result = await self._session.execute(
            update(WorkspaceMembershipRow)
            .where(
                WorkspaceMembershipRow.workspace_id == workspace_id,
                WorkspaceMembershipRow.membership_id == membership_id,
            )
            .values(role=role)
        )
        return result.rowcount > 0

    async def list_memberships(self, workspace_id: UUID) -> list[WorkspaceMembershipRow]:
        stmt = (
            select(WorkspaceMembershipRow)
            .where(WorkspaceMembershipRow.workspace_id == workspace_id)
            .order_by(WorkspaceMembershipRow.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def find_default_workspace(self, principal_id: UUID) -> UUID | None:
        """签发 token 时的 default_workspace_id。

        优先"自己拥有的空间"，其次最早加入的那个。用 `created_at` 而不是 `now()`
        排序是为了让同一个账号多次登录拿到的默认空间稳定——否则前端每次登录都
        可能落到不同空间，等于随机行为。
        """
        stmt = (
            select(WorkspaceMembershipRow.workspace_id)
            .where(WorkspaceMembershipRow.user_id == principal_id)
            .order_by(
                case((WorkspaceMembershipRow.role == "owner", 0), else_=1),
                WorkspaceMembershipRow.created_at,
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ---------------------------------------------------------------- roles

    async def find_role(self, role_key: str) -> RoleRow | None:
        stmt = select(RoleRow).where(RoleRow.role_key == role_key)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_permissions_for_role(self, role_key: str) -> list[str]:
        """该角色被授予的权限点（`roles ⋈ role_grants`），按字母序稳定返回。

        角色不存在、或角色存在但一个授权都没有 → **空列表**。调用方据此 fail-closed：
        给默认权限就等于把"忘了授权"变成"放行"，而这类错误在权限系统里是致命的。

        按 `role_key` 而不是 `role_id` 查：成员的 `workspace_memberships.role` 存的是
        字符串 key，按 key 查少一次"先解出 role_id"的往返。
        """
        stmt = (
            select(RoleGrantRow.permission)
            .join(RoleRow, RoleRow.role_id == RoleGrantRow.role_id)
            .where(RoleRow.role_key == role_key)
            .order_by(RoleGrantRow.permission)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_role_grants(self) -> list[tuple[str, str]]:
        """全部 (role_key, permission)，给管理端点列矩阵用。"""
        stmt = (
            select(RoleRow.role_key, RoleGrantRow.permission)
            .join(RoleGrantRow, RoleGrantRow.role_id == RoleRow.role_id)
            .order_by(RoleRow.role_key, RoleGrantRow.permission)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(str(role_key), str(permission)) for role_key, permission in rows]

    async def add_role_grant(self, *, role_key: str, permission: str) -> bool:
        """给角色补一个权限点。已存在视为成功（幂等），角色不存在返回 False。"""
        role = await self.find_role(role_key)
        if role is None:
            return False
        exists = (
            await self._session.execute(
                select(RoleGrantRow.grant_id).where(
                    RoleGrantRow.role_id == role.role_id,
                    RoleGrantRow.permission == permission,
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            return True
        self._session.add(RoleGrantRow(role_id=role.role_id, permission=permission))
        await self._session.flush()
        return True

    async def remove_role_grant(self, *, role_key: str, permission: str) -> bool:
        """撤掉一个权限点。返回是否真的删到了行（调用方据此区分 404 与幂等）。"""
        role = await self.find_role(role_key)
        if role is None:
            return False
        result = await self._session.execute(
            delete(RoleGrantRow).where(
                RoleGrantRow.role_id == role.role_id,
                RoleGrantRow.permission == permission,
            )
        )
        return result.rowcount > 0

    async def replace_role_grants(self, *, role_key: str, permissions: list[str]) -> bool:
        """整组替换：先删该角色的全部授权，再逐条插入。角色不存在返回 False。

        必须与调用方在**同一个事务**里提交：分两次提交会出现"权限已清空、新权限还没写进
        来"的窗口，那一瞬间该角色的所有成员失去全部权限。
        """
        role = await self.find_role(role_key)
        if role is None:
            return False
        await self._session.execute(
            delete(RoleGrantRow).where(RoleGrantRow.role_id == role.role_id)
        )
        for permission in sorted(set(permissions)):
            self._session.add(RoleGrantRow(role_id=role.role_id, permission=permission))
        await self._session.flush()
        return True
