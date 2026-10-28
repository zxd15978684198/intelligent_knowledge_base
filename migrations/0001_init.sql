-- PyKnora 成员2：摄取与索引基础表结构
--
-- 运行方式（不要直接 psql -f 传路径，容器内无此路径）：
--   uv run python scripts/migrate.py
-- 或手动：
--   docker exec -i pyknora-pg psql -U postgres -d pyknora -v dim=512 -v ON_ERROR_STOP=1 -f - < migrations/0001_init.sql
--
-- 维度 :dim 由外部传入。忘了传会报语法错——这是有意的，
-- 宁可建库失败，也不要静默建出维度与配置不符的表。
--
-- 外键策略（三条，改动前先看 docs/plans/全员模块实现路径.md §4.3 的判定表）：
--   1. 表间父子关系一律建**复合外键** (workspace_id, <父键>)。
--      单列 FK 挡不住跨区：A 区的行可以指向 B 区的父行，单列照样通过。
--      每张表的 UNIQUE (workspace_id, ...) 就是为这些 FK 准备的引用目标。
--   2. 全部 ON DELETE RESTRICT，不用 CASCADE。删除是软删 + 异步 purge（delete_document），
--      CASCADE 会顺手抹掉 document_versions / citations 这类审计证据；
--      RESTRICT 逼 purge 按依赖倒序显式删，删错立刻报错而不是静默丢数据。
--   3. **指针列不建 FK**：documents.active_document_version_id / desired_document_version_id /
--      active_build_id 指向"当前"，而当前是分步切换的（先插新版本，再改指针）。
--      普通 FK 会在事务中间态炸掉；要建只能 DEFERRABLE INITIALLY DEFERRED，本仓库选择不建，
--      由 publish() 的单事务（I3）保证一致性。

\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------- 文档（可变：有 deleted_at / generation） ----------
CREATE TABLE IF NOT EXISTS documents (
    document_id                 uuid PRIMARY KEY,
    workspace_id                uuid NOT NULL,
    kb_id                       uuid NOT NULL,
    desired_document_version_id uuid,
    active_document_version_id  uuid,
    active_build_id             uuid,
    active_index_version        int,
    generation                  int         NOT NULL DEFAULT 1,
    row_version                 int         NOT NULL DEFAULT 1,
    deleted_at                  timestamptz,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, document_id)
    -- kb_id 的复合外键**故意不在这里**：knowledge_bases 由成员1 建（migrations/0002）。
    -- 成员1 建表后必须补：
    --   ALTER TABLE documents ADD CONSTRAINT doc_kb_fk
    --     FOREIGN KEY (workspace_id, kb_id)
    --     REFERENCES knowledge_bases (workspace_id, kb_id) ON DELETE RESTRICT;
    -- 不用"先建空壳表"顶替：空壳会被当成已实现，C1 读契约就没人做了。
);

CREATE INDEX IF NOT EXISTS documents_kb_idx
    ON documents (workspace_id, kb_id) WHERE deleted_at IS NULL;

-- ---------- 不可变版本快照 ----------
CREATE TABLE IF NOT EXISTS document_versions (
    document_version_id     uuid PRIMARY KEY,
    workspace_id            uuid        NOT NULL,
    document_id             uuid        NOT NULL,
    content_hash            text        NOT NULL,
    byte_size               bigint      NOT NULL,
    object_key              text        NOT NULL,
    parse_status            text        NOT NULL,
    created_by_principal_id uuid        NOT NULL,
    ingestion_source        text        NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, document_version_id),
    -- 复合外键必须带 workspace_id：单列 FK 挡不住"A 区的版本挂到 B 区的文档"。
    FOREIGN KEY (workspace_id, document_id)
        REFERENCES documents (workspace_id, document_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS document_versions_hash_idx
    ON document_versions (workspace_id, document_id, content_hash);

-- ---------- 索引构建（一次构建 = 一个不可变 chunk 集合） ----------
CREATE TABLE IF NOT EXISTS index_builds (
    build_id            uuid PRIMARY KEY,
    workspace_id        uuid        NOT NULL,
    document_id         uuid        NOT NULL,
    index_version       int         NOT NULL,
    attempt_id          uuid        NOT NULL,
    embedding_model_key text        NOT NULL,
    embedding_dim       int         NOT NULL,
    chunk_size          int         NOT NULL,
    chunk_overlap       int         NOT NULL,
    normalize           bool        NOT NULL DEFAULT true,
    chunk_count         int         NOT NULL DEFAULT 0,
    state               text        NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    activated_at        timestamptz,
    retired_at          timestamptz,
    UNIQUE (workspace_id, build_id),
    UNIQUE (workspace_id, document_id, index_version, attempt_id),
    FOREIGN KEY (workspace_id, document_id)
        REFERENCES documents (workspace_id, document_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS index_builds_doc_idx
    ON index_builds (workspace_id, document_id, index_version);

-- ---------- 切片与向量 ----------
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id      uuid PRIMARY KEY,
    workspace_id  uuid NOT NULL,
    document_id   uuid NOT NULL,
    build_id      uuid NOT NULL,
    index_version int  NOT NULL,
    attempt_id    uuid NOT NULL,
    chunk_ordinal int  NOT NULL,
    content       text NOT NULL,
    content_hash  text NOT NULL,
    page_no       int,
    heading_path  text,
    char_start    int,
    char_end      int,
    token_count   int,
    locator_ref   text,
    tsvector      tsvector,
    embedding     vector(:dim),
    UNIQUE (workspace_id, document_id, index_version, attempt_id, chunk_ordinal),
    -- chunk 必须挂在一个真实文档、一个真实构建上；否则 v_active_chunks 会 join 出幽灵行。
    FOREIGN KEY (workspace_id, document_id)
        REFERENCES documents (workspace_id, document_id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, build_id)
        REFERENCES index_builds (workspace_id, build_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsvector_idx
    ON chunks USING gin (tsvector);
CREATE INDEX IF NOT EXISTS chunks_content_hash_idx
    ON chunks (workspace_id, content_hash);
CREATE INDEX IF NOT EXISTS chunks_build_idx
    ON chunks (workspace_id, build_id);

-- ---------- 向量缓存（同一段正文在同一模型下不重复调后端） ----------
-- 键必须含 embedding_model_key：否则换模型后会命中旧向量空间的数，
-- 等于把 A 模型的输出当成 B 模型的（PRD 明令禁止混用 embedding 空间）。
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash        text        NOT NULL,
    embedding_model_key text        NOT NULL,
    embedding_dim       int         NOT NULL,
    embedding           vector(:dim) NOT NULL,
    hit_count           int         NOT NULL DEFAULT 0,
    created_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (content_hash, embedding_model_key)
);

CREATE INDEX IF NOT EXISTS embedding_cache_model_idx
    ON embedding_cache (embedding_model_key);

-- ---------- 作业与尝试 ----------
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id           uuid PRIMARY KEY,
    workspace_id     uuid        NOT NULL,
    document_id      uuid        NOT NULL,
    job_type         text        NOT NULL,
    status           text        NOT NULL,
    idempotency_key  text        NOT NULL,
    payload          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    attempt          int         NOT NULL DEFAULT 0,
    lease_epoch      bigint      NOT NULL DEFAULT 0,
    lease_expires_at timestamptz,
    worker_id        text,
    error_code       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, job_id),
    UNIQUE (workspace_id, idempotency_key),
    -- 作业必须指向真实文档：否则 Worker claim 到一条指向空气的 job，只能靠应用层发现。
    FOREIGN KEY (workspace_id, document_id)
        REFERENCES documents (workspace_id, document_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ingestion_jobs_claim_idx
    ON ingestion_jobs (status, created_at)
    WHERE status IN ('pending', 'processing');

CREATE TABLE IF NOT EXISTS job_attempts (
    attempt_id  uuid PRIMARY KEY,
    workspace_id uuid NOT NULL,
    job_id      uuid NOT NULL,
    lease_epoch bigint NOT NULL,
    worker_id   text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    outcome     text,
    UNIQUE (workspace_id, attempt_id),
    FOREIGN KEY (workspace_id, job_id)
        REFERENCES ingestion_jobs (workspace_id, job_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS job_attempts_job_idx
    ON job_attempts (workspace_id, job_id, lease_epoch);

-- ---------- 正文暂存（对象存储的替身） ----------
-- 真实部署下正文在对象存储，DB 只存 object_key。本仓库尚未接对象存储，
-- 用一个单表替身保证链路可测。生产替换点只有 workers/ingest_worker._load_bytes。
CREATE TABLE IF NOT EXISTS document_blobs (
    workspace_id        uuid NOT NULL,
    document_version_id uuid NOT NULL,
    content             bytea NOT NULL,
    PRIMARY KEY (workspace_id, document_version_id),
    FOREIGN KEY (workspace_id, document_version_id)
        REFERENCES document_versions (workspace_id, document_version_id) ON DELETE RESTRICT
);

-- ---------- 发件箱：与业务同事务登记，dispatcher 至少一次派发 ----------
-- 为什么不用"直接发 Redis"：DB 提交成功而唤醒丢失时，事件就永久消失了。
-- 同事务写一行，再由轮询补漏，代价是可能重复投递（幂等键由消费方兜住）。
CREATE TABLE IF NOT EXISTS outbox (
    event_id       uuid PRIMARY KEY,
    workspace_id   uuid        NOT NULL,
    aggregate_type text        NOT NULL,
    aggregate_id   uuid        NOT NULL,
    event_type     text        NOT NULL,
    payload        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    dispatched_at  timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS outbox_pending_idx
    ON outbox (created_at) WHERE dispatched_at IS NULL;

-- ---------- 活动版本视图：C2 的物理承载 ----------
-- 全部安全性来自 WHERE d.active_build_id = b.build_id 这一行。
-- 调用方写错也不会读到旧 build —— 把"记得加条件"变成"结构上不可能"。
--
-- 为什么把 d.kb_id 也放进来：检索按整库过滤（成员3 的主路径）时，
-- 没有 kb_id 就得自己再 join 一次 documents。少一次 join 就少一次
-- 忘写 workspace_id 的机会。
--
-- 注意视图**不校验** embedding_dim，只是把它投影出来。用 512 维查询向量
-- 去搜 768 维索引会在 `<=>` 处抛 PG 维度错误（不是返回空）。校验在
-- SqlChunkSearchRepo.search_similar 里显式做。
DROP VIEW IF EXISTS v_active_chunks;
CREATE VIEW v_active_chunks AS
SELECT
    c.chunk_id,
    c.workspace_id,
    d.kb_id,
    c.document_id,
    c.build_id,
    c.index_version,
    c.attempt_id,
    c.chunk_ordinal,
    c.content,
    c.page_no,
    c.heading_path,
    c.char_start,
    c.char_end,
    c.token_count,
    c.locator_ref,
    c.tsvector,
    c.embedding,
    b.embedding_model_key,
    b.embedding_dim,
    b.chunk_size,
    d.active_document_version_id
FROM chunks c
JOIN index_builds b
  ON b.build_id = c.build_id
 AND b.workspace_id = c.workspace_id
JOIN documents d
  ON d.document_id = c.document_id
 AND d.workspace_id = c.workspace_id
WHERE d.active_build_id = b.build_id
  AND b.state = 'active'
  AND d.deleted_at IS NULL
  AND c.embedding IS NOT NULL;
