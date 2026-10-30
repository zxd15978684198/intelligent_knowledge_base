"""权限点目录与迁移种子必须**逐项一致**。

`role_grants` 是运行期判权的唯一真相源，而"合法权限点"的集合定义在
`services/auth/permissions.py`。两边一旦漂移，症状都是**静默的**：

- 种子里多一个没人认识的权限点 → 这一行永远不会被任何 `require_permission` 命中，
  看起来授权了、实际没生效；
- 常量里多一个种子里没有的权限点 → 所有角色都缺它，端点恒定 403，却查不出为什么。

两个方向都不会抛异常，只会让"我以为授权了"和"实际能不能访问"悄悄分叉。所以这里直接
解析 `migrations/0006_role_grants.sql` 的矩阵，与 `SEED` 做全等比较。
"""

from __future__ import annotations

import re
from pathlib import Path

from pyknora.services.auth.permissions import (
    ADMIN_ONLY,
    ALL_PERMISSIONS,
    PLATFORM_MANAGE_ROLES,
    PLATFORM_PERMISSIONS,
    READ_ONLY,
    ROLE_KEYS,
    SEED,
    is_known_permission,
)

_MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "0006_role_grants.sql"
# 匹配 `('document.read',      '{owner,admin,member}')` 这种行
_ROW = re.compile(r"\('(?P<permission>[a-z_.]+)',\s*'\{(?P<roles>[a-z,]+)\}'")


def _sql_matrix() -> dict[str, frozenset[str]]:
    matrix: dict[str, set[str]] = {}
    for match in _ROW.finditer(_MIGRATION.read_text(encoding="utf-8")):
        permission = match.group("permission")
        for role_key in match.group("roles").split(","):
            matrix.setdefault(role_key, set()).add(permission)
    return {role_key: frozenset(values) for role_key, values in matrix.items()}


def test_migration_matrix_equals_python_seed() -> None:
    assert _sql_matrix() == {role_key: frozenset(values) for role_key, values in SEED.items()}


def test_sql_matrix_covers_exactly_the_four_roles() -> None:
    """SQL 里出现 `workspace_memberships.role` CHECK 之外的角色 = 死角色。"""
    assert frozenset(_sql_matrix()) == ROLE_KEYS


def test_every_seeded_permission_is_declared() -> None:
    for role_key, permissions in SEED.items():
        assert permissions <= ALL_PERMISSIONS, role_key


def test_owner_and_admin_have_every_permission() -> None:
    assert SEED["owner"] == ALL_PERMISSIONS
    assert SEED["admin"] == ALL_PERMISSIONS


def test_member_is_exactly_all_minus_admin_surface() -> None:
    assert SEED["member"] == ALL_PERMISSIONS - ADMIN_ONLY
    assert not (SEED["member"] & ADMIN_ONLY)


def test_guest_is_read_only_and_a_strict_subset_of_member() -> None:
    assert SEED["guest"] == READ_ONLY
    assert SEED["guest"] < SEED["member"]
    assert not (SEED["guest"] & ADMIN_ONLY)


def test_role_keys_match_the_membership_check_constraint() -> None:
    """与 `workspace_memberships.role` 的 CHECK、`workspaces.AssignableRole` 三处一致。"""
    assert frozenset({"owner", "admin", "member", "guest"}) == ROLE_KEYS


def test_is_known_permission() -> None:
    assert is_known_permission("document.read")
    assert is_known_permission("workspace.manage_roles")
    # 平台面也是"已声明"的权限点（否则写端点之外的校验会把它当非法值拒掉）
    assert is_known_permission(PLATFORM_MANAGE_ROLES)
    assert not is_known_permission("document.reed")
    assert not is_known_permission("")
    assert not is_known_permission("DOCUMENT.READ")


def test_platform_permissions_are_a_separate_namespace() -> None:
    """平台面与工作区面不能混：混进来就等于每个租户都能改全局角色目录。

    `roles` / `role_grants` 是全局表（无 workspace_id），写它们必须是平台级动作。
    这条断言钉住"两个命名空间不相交"，防止有人把 `platform.*` 塞进 `ALL_PERMISSIONS`
    ——那样 `SEED["owner"] == ALL_PERMISSIONS` 就会把平台权限发给每个租户的 owner。
    """
    assert PLATFORM_MANAGE_ROLES.startswith("platform.")
    assert PLATFORM_MANAGE_ROLES in PLATFORM_PERMISSIONS
    assert PLATFORM_PERMISSIONS.isdisjoint(ALL_PERMISSIONS)


def test_platform_permissions_are_held_by_nobody() -> None:
    """没有任何角色持有平台面权限——这是 `A1` 方案的全部内容。

    三条写角色目录的端点用 `require_permission(PLATFORM_MANAGE_ROLES)` 当闸，而所有
    角色都没有这个点，所以任何租户（包括 owner）调用都是 403。若哪天有人把它加进
    `SEED` 或 `0006` 的矩阵，这条会红——那时必须同时决定"哪个平台角色持有它"。
    """
    holders = sorted(role for role, perms in SEED.items() if perms & PLATFORM_PERMISSIONS)
    assert holders == [], f"平台权限被这些角色持有了：{holders}"
    assert not (ADMIN_ONLY & PLATFORM_PERMISSIONS)
