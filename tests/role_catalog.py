"""测试夹具：把 `roles` / `role_grants` 参考数据恢复成迁移种下的样子。

## 为什么单独一份

`roles` / `role_grants` 是**迁移种下的参考数据**，不是用例造的业务数据。两个 DB
夹具（`tests/api/conftest.py`、`tests/integration/conftest.py`）为了用例互不串味都会
`TRUNCATE` 它们，于是清场必须与补种成对出现：

1. **不补种** → 运行期判权查 `role_grants`（fail-closed），每个走 HTTP 的用例都 403，
   现象像业务 bug；
2. **不补种且跑的是集成测试** → 跑完全量测试后开发库的 `roles` 是空的，此后任何新用户
   注册都会在 `workspace_memberships_role_fkey` 上违约。真实事故：表现为
   `409 account already exists`，与账号完全无关，排查方向被带偏。

补种逻辑只写这一份，`permissions.SEED` 是唯一真相源（`migrations/0006_role_grants.sql`
的矩阵与它逐项对应，`tests/unit/test_permissions.py` 会比对）。
"""

from __future__ import annotations

from sqlalchemy import text

from pyknora.services.auth.permissions import SEED


async def seed_role_catalog(session) -> None:
    """把内置角色与授权矩阵 upsert 回库，并清掉用例自建的角色。

    顺序是有原因的：`0008_roles_are_data.sql` 给 `workspace_memberships.role` 加了
    指向 `roles(role_key)` 的外键（`ON DELETE RESTRICT`），所以不能整表删了重灌：

    1. 把挂在自建角色上的成员退回内置角色（否则下一步 DELETE 会被外键拦住）；
    2. 删用例自建的角色（内置的不动），它们的授权行随 `ON DELETE CASCADE` 走；
    3. 清空全部授权行（矩阵要以 `SEED` 为准整体重灌）；
    4. upsert 内置角色，再灌授权矩阵。

    用一条 `VALUES` 列表写完：逐个 INSERT 是 50+ 次 round-trip，会让每个用例
    平白多出几十毫秒的固定成本。
    """
    builtin = ", ".join(f"'{role_key}'" for role_key in SEED)

    # 夹具自愈：还留着"挂在自建角色上的成员"说明上一轮清场没覆盖到。
    await session.execute(
        text(f"UPDATE workspace_memberships SET role = 'member' WHERE role NOT IN ({builtin})")
    )
    await session.execute(text(f"DELETE FROM roles WHERE role_key NOT IN ({builtin})"))
    await session.execute(text("DELETE FROM role_grants"))

    role_values: list[str] = []
    role_params: dict[str, str] = {}
    for index, role_key in enumerate(SEED):
        role_params[f"k{index}"] = role_key
        role_params[f"d{index}"] = f"test seed: {role_key}"
        role_values.append(f"(CAST(:k{index} AS text), CAST(:d{index} AS text))")
    await session.execute(
        text(
            f"INSERT INTO roles (role_key, description) VALUES {', '.join(role_values)} "
            "ON CONFLICT (role_key) DO UPDATE SET description = EXCLUDED.description"
        ),
        role_params,
    )

    pairs = [
        (role_key, permission)
        for role_key, permissions in SEED.items()
        for permission in sorted(permissions)
    ]
    grant_values: list[str] = []
    grant_params: dict[str, str] = {}
    for index, (role_key, permission) in enumerate(pairs):
        grant_params[f"k{index}"] = role_key
        grant_params[f"p{index}"] = permission
        grant_values.append(f"(CAST(:k{index} AS text), CAST(:p{index} AS text))")
    await session.execute(
        text(
            "INSERT INTO role_grants (role_id, permission) "
            "SELECT r.role_id, v.permission FROM (VALUES "
            f"{', '.join(grant_values)}"
            ") AS v(role_key, permission) JOIN roles AS r ON r.role_key = v.role_key "
            "ON CONFLICT (role_id, permission) DO NOTHING"
        ),
        grant_params,
    )
