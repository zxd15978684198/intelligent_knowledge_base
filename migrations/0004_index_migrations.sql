-- 成员5：重建 / 迁库的记账表。
--
-- 为什么不复用 ingestion_jobs：那张表是"一次摄取作业"的队列行，承载 claim / lease /
-- attempt 的排队语义。重建与迁库是"把已发布的内容按新参数重算一遍"或"把元信息搬走"，
-- 失败重试、审计和并发口径都不同，混进队列表会把 Worker 的租约语义搞乱。
--
-- 为什么不给 source_build_id / target_build_id 加外键：build 行会随文档 purge 清理，
-- 记账行反过来卡住清理会让"删文档"永久失败。这里只记 id 做追溯，不承担引用完整性。

\set ON_ERROR_STOP on

CREATE TABLE IF NOT EXISTS index_migrations (
    migration_id      uuid        PRIMARY KEY,
    workspace_id      uuid        NOT NULL,
    kb_id             uuid        NOT NULL,
    source_build_id   uuid,
    target_build_id   uuid,
    mode              text        NOT NULL,
    state             text        NOT NULL,
    manifest          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    error_code        text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,

    -- 与其它表一致：业务主键在 workspace 内唯一，跨区同名不算冲突
    UNIQUE (workspace_id, migration_id),
    CHECK (mode IN ('export', 'replay', 'reindex')),
    CHECK (state IN ('pending', 'running', 'succeeded', 'failed'))
);

CREATE INDEX IF NOT EXISTS index_migrations_kb_idx
    ON index_migrations (workspace_id, kb_id, created_at);

-- 知识库必须真实存在且同区。复合外键是唯一能同时校验"存在"与"同区"的约束，
-- 单列 FK 会放行"B 区的记账行指向 A 区的 KB"。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'index_migrations_kb_fk'
          AND conrelid = 'index_migrations'::regclass
    ) THEN
        ALTER TABLE index_migrations
            ADD CONSTRAINT index_migrations_kb_fk
            FOREIGN KEY (workspace_id, kb_id)
            REFERENCES knowledge_bases (workspace_id, kb_id)
            ON DELETE RESTRICT;
    END IF;
END
$$;
