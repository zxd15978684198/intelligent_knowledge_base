-- S1：知识库软删后，检索视图必须看不见它的切片。
--
-- 缺陷（实测可复现）：
--   `knowledge_bases` 的删除是软删（`db/repos/knowledge_bases.py:112-133` 只写
--   `deleted_at` / `generation` / `row_version`，不动 documents、index_builds、chunks）。
--   而 `v_active_chunks`（0001_init.sql:236-269）只过滤 `documents.deleted_at`，
--   **没有 join knowledge_bases**。于是删库之后：
--     - `GET /kb/{id}/documents` 走 repo 的 `deleted_at IS NULL` → 空（看起来删干净了）；
--     - 检索走视图 → 切片照旧返回，引用与原文都能被搜出来。
--   这不是"权限没配好"，是单一路径漏了谓词：同一个库在两处有不同答案。
--
-- 为什么改视图而不是在服务层加过滤：
--   1. 视图是 I1 的唯一真相。所有读路径（`list_active_chunks` / `get_chunks` /
--      `search_similar` / `search_keyword`）都打它，漏一处就是一处旁路；
--      在视图里改一次，四个入口同时收口。
--   2. `CREATE OR REPLACE VIEW` 只换定义、不搬数据、无窗口期，回滚也只换回定义。
--   3. 不改 `0001`（I4 迁移只追加）。
--
-- 为什么用 inner join 而不是 NOT EXISTS：
--   `documents_kb_fk`（0002_knowledge_bases.sql:40-44，复合外键 (workspace_id, kb_id)
--   ON DELETE RESTRICT）保证每个 document 都挂在一个**真实存在**的 KB 上，
--   inner join 不会因为"KB 行不存在"而误删可见行。真删 KB 已被 RESTRICT 拦住，
--   所以这里只可能出现 `deleted_at` 非空这一种不可见。
--
-- 顺带补 `security_invoker = true`：
--   `0003_identity_acl.sql:146-152` 是在视图存在后才 ALTER 上该选项的。本文件用
--   CREATE OR REPLACE 重写定义，显式带上这个选项，让"全新库按顺序重放"与
--   "开发库重复应用"两条路径得到同一个结果——否则一旦将来有人把本文件挪到
--   `0003` 之前，视图会退回 security definer 并绕过底下各表的 RLS。

\set ON_ERROR_STOP on

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
    d.active_document_version_id
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
  AND c.embedding IS NOT NULL;

COMMENT ON VIEW v_active_chunks IS
    '唯一活动切片视图（I1）：活动 build + 未删文档 + 未删知识库。读 chunk 必须走这里。';
