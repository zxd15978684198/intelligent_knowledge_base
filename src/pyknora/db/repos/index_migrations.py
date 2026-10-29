"""成员5：`index_migrations` 记账仓库（`migrations/0004` + `0005`）。

**每一处查询都必须带 `workspace_id` 谓词，这不是风格问题。** 表的主键是复合的
`(workspace_id, migration_id)`（`0004` 误写成单列、`0005` 纠回）——同一
`migration_id` 在两个区里合法共存。少一个谓词，`get()` 就可能把别区的行交出去，
`finish()` 则会把别区的记账改掉：越权读加静默写坏，双重事故。

这里只做存取，不做状态机校验：合法迁移（`running → succeeded|failed`）由
`services/migration/reindex.py` 负责，因为只有它知道一次重建到底跑成什么样。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.scope import Scope
from pyknora.models.tables import IndexMigrationRow

MODE_EXPORT = "export"
MODE_REPLAY = "replay"
MODE_REINDEX = "reindex"
MODES = frozenset({MODE_EXPORT, MODE_REPLAY, MODE_REINDEX})

STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"


def manifest_idempotency_key(manifest: dict[str, Any] | None) -> str | None:
    """从 manifest 里取调用方给的幂等键。

    键存在 JSONB 而不是独立列，是因为 `0004-0005` 的表结构本轮不许动（不加列），
    而幂等键天然是"这次请求的属性"，跟着 manifest 走语义也对得上。
    """
    value = (manifest or {}).get("idempotency_key")
    return str(value) if value else None


class SqlIndexMigrationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        scope: Scope,
        *,
        kb_id: UUID,
        mode: str,
        state: str = STATE_RUNNING,
        manifest: dict[str, Any] | None = None,
        source_build_id: UUID | None = None,
        target_build_id: UUID | None = None,
    ) -> IndexMigrationRow:
        """插一条记账行，返回已 flush 的 ORM 行。

        `state` 默认 `running` 而非 `pending`：调用方拿到这一行之后立刻开始改数据，
        留一个 "pending 但 build 已经在写" 的中间态只会让状态语义自相矛盾。
        """
        row = IndexMigrationRow(
            migration_id=uuid4(),
            workspace_id=scope.workspace_id,
            kb_id=kb_id,
            source_build_id=source_build_id,
            target_build_id=target_build_id,
            mode=mode,
            state=state,
            manifest=manifest or {},
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, scope: Scope, migration_id: UUID) -> IndexMigrationRow | None:
        stmt = select(IndexMigrationRow).where(
            IndexMigrationRow.workspace_id == scope.workspace_id,
            IndexMigrationRow.migration_id == migration_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_kb(
        self, scope: Scope, kb_id: UUID, *, limit: int = 50, offset: int = 0
    ) -> list[IndexMigrationRow]:
        """一个 KB 的记账行，最新的在前。

        排序键带上 `migration_id` 兜底：`created_at` 是 `now()`（事务时间戳），
        同一事务里插的多行时间完全相同，只按时间排会让分页顺序不稳定。
        """
        stmt = (
            select(IndexMigrationRow)
            .where(
                IndexMigrationRow.workspace_id == scope.workspace_id,
                IndexMigrationRow.kb_id == kb_id,
            )
            .order_by(IndexMigrationRow.created_at.desc(), IndexMigrationRow.migration_id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def find_succeeded_by_idempotency_key(
        self, scope: Scope, *, kb_id: UUID, mode: str, idempotency_key: str
    ) -> IndexMigrationRow | None:
        """按幂等键找已成功的记账行——重放同一个请求时直接复用它。

        只认 `succeeded`：失败的记账行不能拿来"复用"，否则重试会变成永远返回
        同一份失败结果，调用方没有任何办法再试一次。
        """
        stmt = (
            select(IndexMigrationRow)
            .where(
                IndexMigrationRow.workspace_id == scope.workspace_id,
                IndexMigrationRow.kb_id == kb_id,
                IndexMigrationRow.mode == mode,
                IndexMigrationRow.state == STATE_SUCCEEDED,
                IndexMigrationRow.manifest["idempotency_key"].astext == idempotency_key,
            )
            .order_by(IndexMigrationRow.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def finish(
        self,
        scope: Scope,
        migration_id: UUID,
        *,
        state: str,
        manifest: dict[str, Any] | None = None,
        error_code: str | None = None,
        target_build_id: UUID | None = None,
    ) -> IndexMigrationRow | None:
        """落终态。返回 None 表示这行在当前 scope 里不存在。

        `finished_at` 由这里写而不是交给数据库默认值：数据库没有
        `finished_at` 的默认值（列可空），漏写就永远是 null，审计时看不出耗时。
        """
        row = await self.get(scope, migration_id)
        if row is None:
            return None
        row.state = state
        row.finished_at = datetime.now(UTC)
        row.error_code = error_code
        if manifest is not None:
            row.manifest = manifest
        if target_build_id is not None:
            row.target_build_id = target_build_id
        await self._session.flush()
        return row
