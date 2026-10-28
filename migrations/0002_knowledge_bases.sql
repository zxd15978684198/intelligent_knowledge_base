-- 成员1：知识库表与跨模块 documents -> knowledge_bases 复合外键。
--
-- 配置字段在知识库创建时固化；后续变更必须走显式重建流程，不能把不同
-- embedding 空间静默混在同一知识库中。

\set ON_ERROR_STOP on

CREATE TABLE IF NOT EXISTS knowledge_bases (
    kb_id                uuid PRIMARY KEY,
    workspace_id         uuid        NOT NULL,
    name                 text        NOT NULL,
    embedding_model_key  text        NOT NULL,
    embedding_dim        int         NOT NULL CHECK (embedding_dim > 0),
    chunk_size           int         NOT NULL CHECK (chunk_size > 0),
    chunk_overlap        int         NOT NULL CHECK (chunk_overlap >= 0),
    normalize            bool        NOT NULL DEFAULT true,
    active_index_build_id uuid,
    row_version          int         NOT NULL DEFAULT 1 CHECK (row_version > 0),
    generation           int         NOT NULL DEFAULT 1 CHECK (generation > 0),
    deleted_at           timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, kb_id),
    CHECK (chunk_overlap < chunk_size)
);

CREATE UNIQUE INDEX IF NOT EXISTS knowledge_bases_active_name_uq
    ON knowledge_bases (workspace_id, lower(name))
    WHERE deleted_at IS NULL;

-- scripts/migrate.py 会重放所有编号 SQL，因此用目录查询保证重复执行安全。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'documents_kb_fk'
          AND conrelid = 'documents'::regclass
    ) THEN
        ALTER TABLE documents
            ADD CONSTRAINT documents_kb_fk
            FOREIGN KEY (workspace_id, kb_id)
            REFERENCES knowledge_bases (workspace_id, kb_id)
            ON DELETE RESTRICT;
    END IF;
END
$$;
