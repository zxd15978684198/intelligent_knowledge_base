-- N6a-1：`locator` 落库——格式无关的「位置」进 chunks（ADR 0008 D2 / D2a）。
--
-- 为什么加新列而不复用 `locator_ref`：
--   `locator_ref` 是**切分层**的文本偏移句柄（`p1:c0-12` 这种形态），它只对切分器有意义，
--   前端拿它渲染不出「第 3 页」或「Sheet1!B2:C4」。而 PDF 要页码、XLSX 要工作表 + 单元格、
--   PPTX 要幻灯片序号——同一份契约要容纳 6 种来源形态。ADR 0008 D2a 明确两者**分工并存**：
--   `locator` 面向引用渲染（结构化 JSON，被 `schemas/locator.py` 的 discriminated union
--   校验），`locator_ref` 继续当切分层的偏移句柄。谁都取代不了谁——塞进一列必然有一边
--   丢信息。
--
-- 为什么是 jsonb 而不是 text：
--   值要按 `kind` 分支渲染，`{"kind":"sheet","sheet":"Sheet1","cells":"B2:C4"}` 直接喂给
--   Pydantic 的 discriminated union 就能校验成对应的子模型；存 text 的话每次读都要先
--   解一次 JSON，还多一层「是不是合法 JSON」的不确定性。
--
-- 为什么可空、且回填成 `para` 而不是硬编造一个位置：
--   历史上写入的切片没有任何位置信息。硬填一个具体页码/段落号等于**编造引用**——
--   用户点过去会看到错的内容，比"不知道"更糟。所以回填一个诚实的兜底：
--   `{"kind":"para","index":null,"inferred":true}`。`index=null` 表示"来源没给序号"，
--   `inferred=true` 表示"这不是解析器真实读到的"。下游见到 `inferred` 就不该把它当
--   精确回指使用（ADR 0008 D9 补充 4）。
--
-- 幂等：`ADD COLUMN IF NOT EXISTS` + `UPDATE ... WHERE locator IS NULL` +
--   `CREATE OR REPLACE VIEW`，重复执行无副作用（第二次回填只会命中真正还是 NULL 的新行）。

\set ON_ERROR_STOP on

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS locator jsonb;

-- 存量回填：诚实兜底，不编造位置。
UPDATE chunks
   SET locator = '{"kind":"para","index":null,"inferred":true}'::jsonb
 WHERE locator IS NULL;

COMMENT ON COLUMN chunks.locator IS
    '格式无关的定位（ADR 0008 D2）：kind ∈ page/para/sheet/slide/table/region。'
    'inferred=true 表示位置是推断/回填出来的，不是解析器真实读到的。'
    '与 locator_ref 分工并存：locator 面向引用渲染，locator_ref 是切分层的文本偏移句柄。';

-- 视图必须带上新列：读 chunk 的唯一合法入口是 `v_active_chunks`（AGENTS §4 I1），
-- 只在 `chunks` 上加列而不同步视图，读路径就永远看不到这个值。
--
-- 列序照抄 `0013_chunk_page_span.sql:34-76` 的那一版（22 列，末尾是 `c.page_end_no`），
-- 本文件**只把 `c.locator` 追加到最后一列**：`CREATE OR REPLACE VIEW` 按位置比对列名，
-- 中途插入会报 `cannot change name of view column "page_end_no" to "locator"`。
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
    -- 新列只能追加在末尾（理由见上）。顺序即 0013 的 22 列 + 本列的 locator（共 23 列）。
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
  AND c.embedding IS NOT NULL;

COMMENT ON VIEW v_active_chunks IS
    '唯一活动切片视图（I1）：活动 build + 未删文档 + 未删知识库。读 chunk 必须走这里。';
