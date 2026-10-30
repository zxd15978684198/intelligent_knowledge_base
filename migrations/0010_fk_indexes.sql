-- 0010 · 给两条「被引用转出端」外键补索引
--   `workspaces.owner_principal_id` / `workspace_memberships.role`
--
-- 问题：这两列都是外键的 **referencing side**（转出端）。PG 只保证被引用端唯一，
-- 从不为转出端自动建索引——`workspaces` 上只有 `workspaces_pkey`，
-- `workspace_memberships` 上只有 `_pkey` / `_user_idx` / `(workspace_id, user_id)`，
-- 这两列都不在其中（实测 `pg_indexes` 确认）。代价落在三个真实路径上：
--
--   1. 删账号：`workspaces_owner_principal_id_fkey` 是 `ON DELETE RESTRICT`，
--      删 principals 前要按 `owner_principal_id` 反查 workspaces，无索引即全表扫；
--   2. 动角色：`workspace_memberships_role_fkey` 是 `ON UPDATE CASCADE
--      ON DELETE RESTRICT`，改 `roles.role_key` 或删角色都要按 `role` 反查成员表，
--      同样是全表扫；
--   3. 读侧两个谓词：`WHERE owner_principal_id = $1`（"我拥有哪些工作区"）与
--      `WHERE workspace_id = $1 AND role = 'admin'`（"这个库有几个管理员"）。
--      后者虽然能借 `(workspace_id, user_id)` 的前缀缩到单个工作区，但 role 过滤
--      仍需逐行判；单列 role 索引让"按角色找人"这类查询也能独立走索引。
--
-- 为什么分两条而不是合成一条复合索引：前缀不同（一条以 `owner_principal_id` 打头，
-- 一条以 `role` 打头），一条复合索引无法同时服务；且两张表本就不同。
-- `role` 的基数是"角色个数"（个位数），索引很小，代价可以忽略。
--
-- 证据口径：本机库当前是空表（`workspaces`/`workspace_memberships` 均 0 行，
-- 集成测试每例 TRUNCATE），**所以没有 EXPLAIN 前后对比**——这条迁移的依据是
-- "外键检查必然要扫转出端"这一结构性事实，不是实测提速。等有真实数据再补
-- `EXPLAIN (ANALYZE)` 记录。
--
-- 幂等：`IF NOT EXISTS`，重复应用不报错；不写 DROP（I4：迁移只追加）。
-- 不用 `CONCURRENTLY`：本仓库迁移在单事务里顺序执行（`scripts/migrate.py`），
-- 且建索引时表为空，不涉及长时间锁——与 0009 同一取舍。

CREATE INDEX IF NOT EXISTS workspaces_owner_principal_idx
    ON workspaces (owner_principal_id);

CREATE INDEX IF NOT EXISTS workspace_memberships_role_idx
    ON workspace_memberships (role);
