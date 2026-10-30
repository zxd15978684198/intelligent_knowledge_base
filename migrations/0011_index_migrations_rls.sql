-- 0011 · 补 `index_migrations` 的 RLS —— 全新装库会漏掉这一张
--
-- 问题不在策略写法，在**执行顺序**：
-- `migrations/0003_identity_acl.sql:124-128` 的 RLS 表清单里已经写了
-- `index_migrations`，但这张表是 `0004_index_migrations.sql` 建的，0003 比 0004
-- 先执行。0003 那一刻 `to_regclass('public.index_migrations')` 恒为 NULL，
-- 于是 `CONTINUE` 把它安静地跳过——没有任何报错，只是在"将来才建的表"那一档里
-- 被顺带跳过了，而它其实永远不会再来一次。
--
-- 实测差异（同一套迁移、两种结果）：
--   · 全新库（0001→0010 一次跑完）：`pg_policies` 只有 **9** 条，
--     `index_migrations` 没有策略、`relrowsecurity = false`；
--   · 长期开发库：**10** 条，`index_migrations` 有 `ws_isolation`——
--     因为这张表建好之后又被人重新应用过一次 0003。这是历史重放的巧合，不是设计。
--
-- 后果：全新部署的环境里，`index_migrations`（带 `workspace_id`、`/admin` 侧
-- 暴露的迁移台账）失去数据库这一层的隔离兜底，只剩应用层谓词。这是"升级库一切
-- 正常、全新装库缺一层防护"的典型漂移，靠人眼很难发现，只有真正从零重放才会暴露。
--
-- 修法：**不改 0003**（I4：已合并的号段不改历史文件，且改它还会让已升级的库
-- 与全新库继续分叉）。改为在追加的迁移里单独给这张表补同一套策略。
--
-- 幂等：`ALTER TABLE ... ENABLE ROW LEVEL SECURITY` 本身幂等；策略沿用 0003 的
-- `DROP POLICY IF EXISTS` + `CREATE POLICY` 写法（与 0003:132-138 逐字一致，
-- 便于对照），重复应用只会重建同名策略，不会出现两条。

DO $$
BEGIN
    IF to_regclass('public.index_migrations') IS NULL THEN
        -- 只跑过 0003 之前的迁移时会是这个分支；此时无事可做。
        RAISE NOTICE '0011: index_migrations 尚不存在，跳过';
        RETURN;
    END IF;

    EXECUTE 'ALTER TABLE index_migrations ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS ws_isolation ON index_migrations';
    EXECUTE $q$
        CREATE POLICY ws_isolation ON index_migrations
            USING (workspace_id = current_setting('app.workspace_id', true)::uuid)
            WITH CHECK (workspace_id = current_setting('app.workspace_id', true)::uuid)
    $q$;
END
$$;

COMMENT ON TABLE index_migrations IS
    '全新建库时 0003 会跳过本表（0004 才建），0009 之后由 0011 单独补 RLS';
