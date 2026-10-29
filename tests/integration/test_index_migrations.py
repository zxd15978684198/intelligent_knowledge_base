"""任务16/17 的记账表 `index_migrations`：DDL 级护栏（需要真 PG）。

测的是 `migrations/0004_index_migrations.sql` 里的约束，不是业务函数：
CHECK 挡住拼错的状态、复合外键挡住跨工作区挂靠。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pyknora.core.scope import Scope

# 与 tests/integration/conftest.py 的种子保持一致（B 区没有对应夹具）
KB_ID_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

_INSERT = text(
    """
    INSERT INTO index_migrations (
        migration_id, workspace_id, kb_id, source_build_id, target_build_id,
        mode, state, manifest
    ) VALUES (
        :migration_id, :ws, :kb_id, :source_build_id, :target_build_id,
        :mode, :state, '{}'::jsonb
    )
    """
)


async def _insert(
    session,
    *,
    scope: Scope,
    kb_id: UUID,
    mode: str = "reindex",
    state: str = "pending",
    migration_id: UUID | None = None,
) -> UUID:
    mid = migration_id or uuid4()
    await session.execute(
        _INSERT,
        {
            "migration_id": mid,
            "ws": scope.workspace_id,
            "kb_id": kb_id,
            "source_build_id": uuid4(),
            "target_build_id": None,
            "mode": mode,
            "state": state,
        },
    )
    return mid


async def test_accepts_a_pending_reindex_row(session, scope_a, kb_id) -> None:
    mid = await _insert(session, scope=scope_a, kb_id=kb_id)

    row = (
        await session.execute(
            text("SELECT mode, state, manifest FROM index_migrations WHERE migration_id = :mid"),
            {"mid": mid},
        )
    ).one()
    assert (row[0], row[1], row[2]) == ("reindex", "pending", {})


async def test_rejects_unknown_mode(session, scope_a, kb_id) -> None:
    """mode 是闭集：拼错成 'rebuild' 必须在库里就被拒绝，而不是写进去当脏数据。"""
    with pytest.raises(IntegrityError):
        await _insert(session, scope=scope_a, kb_id=kb_id, mode="rebuild")


async def test_rejects_unknown_state(session, scope_a, kb_id) -> None:
    with pytest.raises(IntegrityError):
        await _insert(session, scope=scope_a, kb_id=kb_id, state="sucessed")


async def test_rejects_kb_of_another_workspace(session, scope_b, kb_id) -> None:
    """B 区的记账行指向 A 区的 KB → 复合外键拒绝；单列外键在这里会放行。"""
    with pytest.raises(IntegrityError):
        await _insert(session, scope=scope_b, kb_id=kb_id)


async def test_same_migration_id_in_two_workspaces_is_allowed(
    session, scope_a, scope_b, kb_id
) -> None:
    """业务主键在 workspace 内唯一，跨区同 id 不算冲突（与其它表口径一致）。"""
    mid = uuid4()
    await _insert(session, scope=scope_a, kb_id=kb_id, migration_id=mid)
    await _insert(session, scope=scope_b, kb_id=KB_ID_B, migration_id=mid)

    count = await session.scalar(
        text("SELECT count(*) FROM index_migrations WHERE migration_id = :mid"), {"mid": mid}
    )
    assert count == 2
