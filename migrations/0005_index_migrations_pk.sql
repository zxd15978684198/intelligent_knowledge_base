-- 0005：修 0004 的主键口径（表属成员5，号段由成员2 代开——合并发生在成员2 侧）
--
-- 0004 把 `migration_id` 设成单列 PRIMARY KEY，同文件的注释却写"业务主键在
-- workspace 内唯一、跨区同名不算冲突"，集成测试也按后者断言
-- （`test_same_migration_id_in_two_workspaces_is_allowed`）。单列主键是全局唯一，
-- 两条不可能同时成立，该用例必红。
--
-- 全库口径是复合主键：documents / chunks / document_versions 都是
-- (workspace_id, xxx)。这里把 0004 的例外纠回一致。
-- I4 要求不改已合并的迁移文件，所以不回头改 0004，而是新开号段。

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF to_regclass('public.index_migrations') IS NULL THEN
        RAISE NOTICE 'index_migrations 不存在（0004 尚未执行），跳过 0005';
        RETURN;
    END IF;

    -- 只在仍是单列主键时改：重复执行安全，且不会把复合主键再拆一次。
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'index_migrations'::regclass
          AND conname = 'index_migrations_pkey'
          AND array_length(conkey, 1) = 1
    ) THEN
        ALTER TABLE index_migrations DROP CONSTRAINT index_migrations_pkey;
        ALTER TABLE index_migrations
            ADD CONSTRAINT index_migrations_pkey PRIMARY KEY (workspace_id, migration_id);
        RAISE NOTICE 'index_migrations 主键已改为 (workspace_id, migration_id)';
    END IF;
END
$$;

-- 复合主键已经承担 (workspace_id, migration_id) 的唯一性，
-- 0004 里那条 UNIQUE 于是成了重复索引，删掉省一份写入维护成本。
ALTER TABLE index_migrations
    DROP CONSTRAINT IF EXISTS index_migrations_workspace_id_migration_id_key;
