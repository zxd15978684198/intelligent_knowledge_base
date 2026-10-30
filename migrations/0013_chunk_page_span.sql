-- P2-D2：切片跨页时只记了起始页，引用回指会指错范围。
--
-- 缺陷（实测）：一份 4 页 PDF 切成 22 块，其中一块的正文**横跨第 3、4 页**，
-- 但 `chunks.page_no` 只能存一个值，于是它记的是 `3`。用户从引用点回原文时，
-- 后半段内容在第 4 页，界面上却只提示第 3 页——引用"看起来对，翻过去找不到"。
--
-- 为什么加列而不是"按页切断切片"：
--   按页切断确实能让 `page_no` 变成精确值，但代价是**一句话会被页边界劈成两块**。
--   PDF 里跨页的句子极常见（排版换页不看语义），劈开之后两块各自都不完整，
--   检索时谁也答不上"这份文件怎么说"，等于用引用精度换召回质量。
--   记录的方案是主流 RAG 的做法：块照旧完整，元数据里存 `page_start`/`page_end`。
--
-- 命名沿用本表已有风格：`page_no`（起始页）保留不动——它是既有契约的一部分，
-- 改列名要动 4 个读点与成员3/5 的代码；新列的语义是"结束页"，缺省等于起始页。
--
-- 幂等：`ADD COLUMN IF NOT EXISTS` + `CREATE OR REPLACE VIEW`，重复执行无副作用。

\set ON_ERROR_STOP on

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page_end_no integer;

-- 回填：没有结束页信息的历史行，按"单页"处理最保守——范围收窄不会指错，
-- 放宽才会。生产库此列必为空（新写入路径会带上真实值）。
UPDATE chunks
   SET page_end_no = page_no
 WHERE page_end_no IS NULL
   AND page_no IS NOT NULL;

COMMENT ON COLUMN chunks.page_end_no IS
    '切片末字符所在页码；与 page_no 相同表示单页切片。跨页切片 page_no < page_end_no。';

-- 视图必须带上新列：读 chunk 的唯一合法入口是 `v_active_chunks`（AGENTS §4 I1），
-- 只在 `chunks` 上加列而不同步视图，读路径就永远看不到这个值。
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
    -- 新列只能**追加在末尾**：`CREATE OR REPLACE VIEW` 不允许在中途插入列
    -- （psql 报 `cannot change name of view column "heading_path" to "page_end_no"`），
    -- 它按位置比对列名。想改顺序只能 DROP VIEW，那会打断正在跑的查询。
    c.page_end_no
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
