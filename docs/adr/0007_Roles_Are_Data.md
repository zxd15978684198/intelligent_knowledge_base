# ADR-0007 · 角色是数据，不是 DDL 枚举

- **状态**：提议（待用户接受；本文件按 `AGENTS.md` §3.2 第 4 条记录，未标注"已定"）
- **日期**：2026-10-29
- **影响范围**：`migrations/0008_roles_are_data.sql`、`workspace_memberships` 表、`src/pyknora/db/repos/principals.py`、`src/pyknora/services/auth/permissions.py`、`src/pyknora/api/v1/workspaces.py`
- **关联**：ADR-0006（身份/角色/RLS）、`migrations/0003_identity_acl.sql`、`migrations/0006_role_grants.sql`

## 背景

`role_grants` 落地（0006）之后，**权限点是数据**：给某个角色加一个权限，只需 `INSERT INTO role_grants`，不用改代码、不用发版。

但**角色本身不是**。同一个角色集合被定义了三处：

| 位置 | 形式 |
| --- | --- |
| `migrations/0003_identity_acl.sql:53-54` | `CHECK (role IN ('owner','admin','member','guest'))` |
| `migrations/0003_identity_acl.sql:67` | `roles.role_key UNIQUE`（一条行就是一个角色） |
| `src/pyknora/api/v1/workspaces.py`（改造前） | `AssignableRole = Literal["admin","member","guest"]` |

后果是自相矛盾：**权限可以配，角色不能加**。要新增一个「审计员」角色，必须写新迁移改 `CHECK`、改 `Literal`、再发版——这跟任务 10 的名字「部门角色权限」正好相反，也跟 `roles` 表存在的事实冲突。

## 决策

1. **摘掉列举式 `CHECK`**，换成格式约束 `CHECK (role ~ '^[a-z][a-z0-9_]{0,63}$')`——只约束"它得像标识符"，不约束"有哪几个"。
2. **加外键** `workspace_memberships.role → roles(role_key)`，`ON UPDATE CASCADE ON DELETE RESTRICT`。
   - `CASCADE`：重命名角色时成员跟着走，不必写批量 UPDATE。
   - `RESTRICT`：还有人在用的角色删不掉——比"应用层先 `count(*)` 再删"可靠，因为两步之间仍可能有人加进来。
3. **代码里只保留两条硬规则**：`owner` 不可在成员接口里被赋值/删除（它唯一具有"不可被剥夺"语义）；角色名必须真实存在于 `roles`（否则 422，而不是让外键抛 500）。
4. `roles` 表因此成为**唯一真相源**：`role_grants` 给它挂权限点，`workspace_memberships` 通过外键指向它。`permissions.py` 的 `ROLE_KEYS` 改名为 `BUILTIN_ROLES`，语义从"角色闭集"收窄为"内置角色"。

## 为什么不做成完全自由（比如允许 `owner` 被删）

`owner` 是**唯一能恢复权限的角色**：如果所有 admin 把自己锁在外面，只有 owner 能救回来。允许删除或篡改它，等于给系统留一个"永久锁死"的入口。所以它继续被代码保护，但它仍然是 `roles` 里的一行数据（可改描述，权限不可替换）。

## 后果

- 新增角色 = 一个 HTTP `POST /workspaces/{ws}/roles` + 若干权限点，**不需要迁移、不需要发版**；新角色建完立刻可指派给成员（外键保证存在性）。
- 删角色多了一种失败模式：仍有成员在用 → 409（`details.members` 给出人数）。调用方要先把人换掉。
- 迁移必须**幂等且可重跑**（`0008` 全程 `DO $$ ... IF NOT EXISTS ... $$`；号段原为 `0007`，因并发成员的 `0007_cjk_bigram_fts.sql` 先到而让号）：本地库可能处于任何历史状态，夹具也会 `TRUNCATE` 后重灌。
- 数据库多了一条外键的成本：写 `workspace_memberships` 前角色必须已存在。夹具因此必须**先种角色、再插成员**（`tests/api/conftest.py` 的 `seed_role_catalog` 已按此顺序调用）。

## 未做的事

- 角色**跨工作区共享**（`roles` 是全局表，没有 `workspace_id`）：本轮不做租户级自定义角色，那需要 `roles.workspace_id` 与外键拆分，属另一次决策。
- 权限点的**继承**（角色：`admin ⊃ member`）：目前每个角色的权限矩阵是平的，`ALL_PERMISSIONS` 只是种子值，不是层叠关系。
