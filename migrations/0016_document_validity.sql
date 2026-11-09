\set ON_ERROR_STOP on

-- 0016 · 文档有效期与权威度
--
-- 要解决的问题（评测里的 `poison-02`）：同一个知识库并存《员工手册 v1》与 v2，
-- v1 的「探望父母两年一次、每次 45 天」早已作废，v2 是「每年一次 30 天」。
-- 检索只看「活动 build」，两篇都是活动的，于是 v1 的旧口径会被当成现行规定复述。
-- 表里没有任何字段能表达「这份文档已经不作数了」，所以这不是调参能修的。
--
-- 三个列：
--   effective_from   —— 生效时刻；NULL = 一直有效（历史数据与绝大多数上传都是这种）
--   effective_until  —— 失效时刻；NULL = 现行不失效。到点之后自动退出检索，
--                       不需要有人去删文档（删除是软删 + 异步 purge，语义不同）
--   authority        —— 权威度，数值越小越权威；同分排序用。默认 100 = 普通上传
--
-- 读路径的过滤写在视图里（`v_active_chunks`），不写在每个查询上：
-- I1 规定读 chunk 必须走这个视图，把「哪些切片算当前有效」定义在视图里，
-- 才能保证所有读者（检索、引用二次授权、重建索引）看到同一套规则。
-- 只加 WHERE 谓词、**列保持不变**（仍是 23 列）—— `CREATE OR REPLACE VIEW`
-- 按位置比对列名，加减列会报 `cannot change name of view column ...`。

ALTER TABLE documents ADD COLUMN IF NOT EXISTS effective_from timestamptz;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS effective_until timestamptz;
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS authority smallint NOT NULL DEFAULT 100;

COMMENT ON COLUMN documents.effective_from IS
    '文档生效时刻；NULL = 一直有效（历史数据默认）。';
COMMENT ON COLUMN documents.effective_until IS
    '文档失效时刻；NULL = 现行不失效。到点后切片自动退出 v_active_chunks，不参与检索。';
COMMENT ON COLUMN documents.authority IS
    '权威度，数值越小越权威；检索同分排序用。默认 100。';

-- 只给「设了失效时刻」的行建索引：绝大多数行是 NULL，全表索引白占空间。
CREATE INDEX IF NOT EXISTS documents_effective_until_idx
    ON documents (workspace_id, effective_until)
    WHERE effective_until IS NOT NULL;

CREATE OR REPLACE VIEW v_active_chunks
WITH (security_invoker = true) AS
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
    d.active_document_version_id,
    c.page_end_no,
    -- 新列只能追加在末尾（理由见 0014）。顺序即 0013 的 22 列 + locator（共 23 列）。
    c.locator
FROM chunks c
JOIN index_builds b
  ON b.build_id = c.build_id
 AND b.workspace_id = c.workspace_id
JOIN documents d
  ON d.document_id = c.document_id
 AND d.workspace_id = c.workspace_id
JOIN knowledge_bases k
  ON k.kb_id = d.kb_id
 AND k.workspace_id = d.workspace_id
WHERE d.active_build_id = b.build_id
  AND b.state = 'active'
  AND d.deleted_at IS NULL
  AND k.deleted_at IS NULL
  AND c.embedding IS NOT NULL
  -- 有效期窗口：未生效的与已失效的都不算活动切片。
  AND (d.effective_from IS NULL OR d.effective_from <= now())
  AND (d.effective_until IS NULL OR d.effective_until > now());

COMMENT ON VIEW v_active_chunks IS
    '唯一活动切片视图（I1）：活动 build + 未删文档 + 未删知识库 + 在有效期内。读 chunk 必须走这里。';
