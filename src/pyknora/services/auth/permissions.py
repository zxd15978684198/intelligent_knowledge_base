"""权限点目录：`role_grants.permission` 的**唯一**合法取值集合。

为什么单独一个模块，而不是把字符串散在各路由里：权限点是跨模块契约——成员1 的
知识库路由、成员2 的文档/作业路由、成员3 的检索问答、成员5 的 lineage 都要引用
同一批字符串。拼错一个字母的后果不是报错，而是**静默 403**（查不到这个权限点 =
没有这个权限），这类 bug 在生产里排查成本极高。所以这里集中定义常量，
`migrations/0006_role_grants.sql` 的种子必须与 `SEED` 一一对应，
`tests/unit/test_permissions.py` 会逐个比对两边。

命名规则 `<资源>.<动作>`，资源取 URL 里的一级路径段（knowledge_base / document /
chunk / job / query / chat / lineage），管理面用 `workspace.*` 前缀。

**运行期判权只读 `role_grants` 表**，本模块的 `SEED` 只用于种子一致性测试与文档，
不是第二个真相源——改权限要写 SQL（或走 `workspace.manage_roles` 端点），改这里的
常量不会让判权生效。反过来，本模块的 `ALL_PERMISSIONS` 是"合法取值"的硬边界：
新增权限点必须同时加常量 + 迁移，否则 `test_permissions` 会红。
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------- 业务面：知识库
KNOWLEDGE_BASE_READ: Final = "knowledge_base.read"
KNOWLEDGE_BASE_WRITE: Final = "knowledge_base.write"
KNOWLEDGE_BASE_DELETE: Final = "knowledge_base.delete"

# ---------------------------------------------------------------- 业务面：文档 / 切片
DOCUMENT_READ: Final = "document.read"
DOCUMENT_WRITE: Final = "document.write"
DOCUMENT_DELETE: Final = "document.delete"
CHUNK_READ: Final = "chunk.read"

# ---------------------------------------------------------------- 业务面：作业 / 检索 / 问答
JOB_READ: Final = "job.read"
JOB_CANCEL: Final = "job.cancel"
QUERY_RUN: Final = "query.run"
CHAT_RUN: Final = "chat.run"
LINEAGE_READ: Final = "lineage.read"

# ---------------------------------------------------------------- 管理面
INDEX_REBUILD: Final = "index.rebuild"
WORKSPACE_MANAGE_MEMBERS: Final = "workspace.manage_members"
WORKSPACE_MANAGE_ROLES: Final = "workspace.manage_roles"

ALL_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        KNOWLEDGE_BASE_READ,
        KNOWLEDGE_BASE_WRITE,
        KNOWLEDGE_BASE_DELETE,
        DOCUMENT_READ,
        DOCUMENT_WRITE,
        DOCUMENT_DELETE,
        CHUNK_READ,
        JOB_READ,
        JOB_CANCEL,
        QUERY_RUN,
        CHAT_RUN,
        LINEAGE_READ,
        INDEX_REBUILD,
        WORKSPACE_MANAGE_MEMBERS,
        WORKSPACE_MANAGE_ROLES,
    }
)

# 管理面权限：不是"业务动作"，改它的影响面是整个工作区，只有 owner/admin 有。
ADMIN_ONLY: Final[frozenset[str]] = frozenset(
    {INDEX_REBUILD, WORKSPACE_MANAGE_MEMBERS, WORKSPACE_MANAGE_ROLES}
)

# 只读集合：guest 的全部权限。
READ_ONLY: Final[frozenset[str]] = frozenset(
    {
        KNOWLEDGE_BASE_READ,
        DOCUMENT_READ,
        CHUNK_READ,
        JOB_READ,
        QUERY_RUN,
        CHAT_RUN,
        LINEAGE_READ,
    }
)

# 与 migrations/0006_role_grants.sql 的矩阵逐项对应（测试会比对）。
SEED: Final[dict[str, frozenset[str]]] = {
    "owner": ALL_PERMISSIONS,
    "admin": ALL_PERMISSIONS,
    # member 是"业务读写 + 检索问答"，管理面三件（重建 / 管人 / 管角色）都不给。
    # 注意 `index.rebuild` 也算管理面：全量重建是重操作，一次误触能把整个工作区的
    # 索引停摆十几分钟，不该由每个成员触发。
    "member": ALL_PERMISSIONS - ADMIN_ONLY,
    "guest": READ_ONLY,
}

# **内置**角色：`0006` 种下的四个。它们有特殊语义（owner 的权限不可改、owner 不可在
# 成员接口里被赋值/删除），所以仍然写成常量；但角色集合本身就**不是**闭集了——
# 合法的角色名以 `roles` 表为准（`0008_roles_are_data.sql` 用外键把成员表指向它）。
# 判断"某个角色名是否存在"要查库（`SqlIdentityRepo.role_exists`），不要查这个常量。
BUILTIN_ROLES: Final[frozenset[str]] = frozenset(SEED)

# 旧名保留，语义已从"角色闭集"收窄为"内置角色"。新代码请用 `BUILTIN_ROLES`。
ROLE_KEYS: Final[frozenset[str]] = BUILTIN_ROLES

# 唯一拥有"不可被剥夺"语义的角色：它的权限不可整组替换，成员表里也不可被改成别的角色。
OWNER_ROLE: Final = "owner"


def is_known_permission(permission: str) -> bool:
    """该字符串是不是合法权限点。给管理端点做前置校验用（非法值 422 而不是静默写入）。"""
    return permission in ALL_PERMISSIONS
