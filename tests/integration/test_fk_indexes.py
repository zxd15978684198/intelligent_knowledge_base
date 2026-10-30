"""`migrations/0010_fk_indexes.sql` 的结构守卫：两条转出端外键必须有前导列索引。

为什么用"查系统目录"而不是测行为：
`workspaces.owner_principal_id` 与 `workspace_memberships.role` 缺索引的代价只在
"删账号 / 动角色 / 按角色找人"时才出现，表现形式是**慢**而不是**错**——写行为断言
需要一个几十万行的库才有意义，集成测试每例 TRUNCATE，根本测不出差别。
所以这里守两件事：

1. 索引确实存在，且**前导列**就是外键列（前导列不对等于没有）；
2. 被索引的那两条外键约束仍然存在（约束被删了，索引就没有存在理由，
   用例要跟着红，提示把迁移一起清掉）。

`pg_indexes` 只给 `indexdef` 文本，用 `indexdef.split("(")[1]` 取列清单再取前导列，
不做正则猜列名——`indexdef` 里 `USING btree (col)` 的括号位置是稳定的。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

# (表名, 外键约束名, 外键列, 期望的索引名)
FK_INDEX_CASES = [
    (
        "workspaces",
        "workspaces_owner_principal_id_fkey",
        "owner_principal_id",
        "workspaces_owner_principal_idx",
    ),
    (
        "workspace_memberships",
        "workspace_memberships_role_fkey",
        "role",
        "workspace_memberships_role_idx",
    ),
]


async def _index_leading_column(session, indexname: str) -> str | None:
    row = (
        await session.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = :name"
            ),
            {"name": indexname},
        )
    ).first()
    if row is None:
        return None
    # indexdef 形如：CREATE INDEX x ON public.t USING btree (col)
    columns = row[0].split("(", 1)[1].rsplit(")", 1)[0]
    return columns.split(",")[0].strip()


@pytest.mark.parametrize(("table", "constraint", "column", "indexname"), FK_INDEX_CASES)
async def test_referencing_fk_column_has_leading_index(
    session, table: str, constraint: str, column: str, indexname: str
) -> None:
    leading = await _index_leading_column(session, indexname)
    assert leading == column, (
        f"{table}.{column} 期望索引 {indexname} 的前导列是 {column}，实际是 {leading!r}；"
        "索引缺失或前导列不对，外键 RESTRICT/CASCADE 检查就会全表扫"
    )


@pytest.mark.parametrize(("table", "constraint", "column", "indexname"), FK_INDEX_CASES)
async def test_indexed_fk_constraint_still_exists(
    session, table: str, constraint: str, column: str, indexname: str
) -> None:
    row = (
        await session.execute(
            text(
                """
                SELECT pg_get_constraintdef(c.oid)
                FROM pg_constraint AS c
                JOIN pg_class AS t ON t.oid = c.conrelid
                WHERE c.conname = :name AND t.relname = :table
                """
            ),
            {"name": constraint, "table": table},
        )
    ).first()
    assert row is not None, (
        f"外键 {constraint} 不存在了：{indexname} 已无服务对象，请把迁移与索引用例一起清掉"
    )
    assert column in row[0], f"外键 {constraint} 的定义里没有 {column}：{row[0]!r}"
