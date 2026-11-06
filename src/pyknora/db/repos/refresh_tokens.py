"""会话令牌仓储（`migrations/0015_refresh_tokens.sql`）。

只做存取，不裁决「这次刷新算不算重用」——那是鉴权路由的事：只有它知道
请求里的 Cookie 与主体当前状态。本仓储的 `get_for_update` 用行锁把并发刷新
串行化，端点据此决定轮换 / 宽限 204 / 撤销整族。

方法一律不自行 commit：事务边界归调用方（鉴权端点）统一提交，否则「撤销
家族 + 抛 401」会被会话作用域的异常回滚一起回滚掉。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.models.tables import RefreshTokenRow


class SqlRefreshTokenRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_update(self, token_hash: str) -> RefreshTokenRow | None:
        """按哈希加锁读一行。行锁是单次轮换正确性的前提。"""
        stmt = (
            select(RefreshTokenRow)
            .where(RefreshTokenRow.token_hash == token_hash)
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def insert_successor(
        self, current: RefreshTokenRow, new_token_hash: str
    ) -> RefreshTokenRow:
        """插入后继行：家族、CSRF 哈希、绝对过期时间原样复制（绝不滑动延长）。"""
        successor = RefreshTokenRow(
            token_hash=new_token_hash,
            family_id=current.family_id,
            principal_id=current.principal_id,
            csrf_hash=current.csrf_hash,
            expires_at=current.expires_at,
        )
        self._session.add(successor)
        await self._session.flush()
        return successor

    async def mark_rotated(self, token_hash: str, new_token_hash: str) -> None:
        """把当前行标记为「已被消费」，记下后继哈希。"""
        stmt = (
            update(RefreshTokenRow)
            .where(RefreshTokenRow.token_hash == token_hash)
            .values(rotated_at=datetime.now(UTC), replaced_by=new_token_hash)
        )
        await self._session.execute(stmt)

    async def revoke_family(self, family_id: UUID) -> None:
        """整族撤销：重用检测命中、登出、主体停用都走这里。"""
        stmt = (
            update(RefreshTokenRow)
            .where(RefreshTokenRow.family_id == family_id)
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.execute(stmt)

    async def has_live_successor(self, family_id: UUID) -> bool:
        """家族里是否还有活跃（未撤销、未被轮换）的后继行。

        宽限内的重复投递据此判「已经有人成功轮换过了」，返回 204 而不撤销整族。
        """
        stmt = (
            select(RefreshTokenRow.token_hash)
            .where(
                RefreshTokenRow.family_id == family_id,
                RefreshTokenRow.revoked_at.is_(None),
                RefreshTokenRow.replaced_by.is_(None),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def purge_expired_for(self, principal_id: UUID) -> int:
        """登录顺带清理：只删该主体已过绝对期的行，返回删除条数。"""
        stmt = (
            delete(RefreshTokenRow)
            .where(RefreshTokenRow.principal_id == principal_id)
            .where(RefreshTokenRow.expires_at < datetime.now(UTC))
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)
