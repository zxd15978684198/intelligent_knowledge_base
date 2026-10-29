-- 0008 · 角色集合改为"数据"，DDL 不再列举角色名
--
-- 号段说明：本文件最初编为 0007，但工作区里同时存在并发成员新建的
-- `0007_cjk_bigram_fts.sql`（21:48，早于本文件 21:51）。两个迁移不能同号，
-- 按"先到先得"本文件让号为 0008。两者互不依赖，可任意顺序应用。
--
-- 问题：`workspace_memberships.role` 原本带 CHECK，把四个角色名写死在 DDL 里；而
-- `roles` 表本身就是角色目录。同一个集合被定义两次，后果是"新增一个角色"必须改迁移
-- 并重新发布，跟任务10「部门角色权限」的名字对不上——权限点已经数据驱动
-- （0006 的 `role_grants`），角色却还被 DDL 锁死。
--
-- 本迁移做三件事：
--   1. 摘掉列举式 CHECK；
--   2. 换成格式约束（只约束"能不能当标识符"，不约束"有哪几个角色"）；
--   3. 加一条 `role → roles(role_key)` 外键：把"角色必须存在"从应用层约定升级成
--      数据库保证。写一个不存在的角色会被数据库拒绝；删除仍有成员在用的角色会被
--      RESTRICT 拒绝——这两条正是"角色可配置"必须有的安全网。
--
-- 已合并的 0003 不动（I4 只追加），四个内置角色的种子仍在 0006（幂等，可重跑）。

-- ---------------------------------------------------------------- 1. 摘掉闭集
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'workspace_memberships_role_check'
          AND conrelid = 'workspace_memberships'::regclass
    ) THEN
        ALTER TABLE workspace_memberships DROP CONSTRAINT workspace_memberships_role_check;
        COMMENT ON TABLE workspace_memberships IS
            '成员表；role 指向 roles.role_key（外键），角色集合由数据决定，不在 DDL 枚举。';
    END IF;
END $$;

-- ---------------------------------------------------------------- 2. 格式约束
-- 只保证它是"像标识符的串"：小写字母开头，允许数字与下划线，最长 64（同 roles.role_key）。
-- 真正的合法性由第 3 步的外键判定。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'workspace_memberships_role_format_check'
          AND conrelid = 'workspace_memberships'::regclass
    ) THEN
        ALTER TABLE workspace_memberships
            ADD CONSTRAINT workspace_memberships_role_format_check
            CHECK (role ~ '^[a-z][a-z0-9_]{0,63}$');
    END IF;
END $$;

-- ---------------------------------------------------------------- 3. 外键
-- `ON UPDATE CASCADE`：重命名角色时成员跟着走（不必写批量 UPDATE）。
-- `ON DELETE RESTRICT`：还有人在用的角色删不掉——比在应用层 `count(*)` 再删更可靠，
-- 因为中间那一下仍可能有人加进来。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'workspace_memberships_role_fkey'
          AND conrelid = 'workspace_memberships'::regclass
    ) THEN
        ALTER TABLE workspace_memberships
            ADD CONSTRAINT workspace_memberships_role_fkey
            FOREIGN KEY (role) REFERENCES roles (role_key)
            ON UPDATE CASCADE ON DELETE RESTRICT;
    END IF;
END $$;
