-- 0009 · 给 ingestion_jobs 补「按文档查作业」的索引
--
-- 问题：`ingestion_jobs` 上有复合外键
-- `ingestion_jobs_workspace_id_document_id_fkey (workspace_id, document_id)`，
-- 但该列序上没有索引。PG 不会为被引用的转出端（referencing side）自动建索引，
-- 只保证被引用端唯一。
--
-- 代价落在两个真实路径上：
--   1. 删文档：`documents` 的级联检查要按 (workspace_id, document_id) 反查作业，
--      无索引退化成全表扫；
--   2. 文档状态查询：`GET /documents/{id}/jobs` 这类"看这份文档有哪些作业"
--      的读也是同一列序。
--
-- 列序说明：`(workspace_id, document_id, created_at DESC)` —— 前缀与上述外键
-- 完全一致（外键检查需要前缀匹配），尾部再带 `created_at DESC` 让"最新一次作业"
-- 这条查询直接从索引拿到顺序，省一次 sort。方向 DESC 与读侧 `ORDER BY ... DESC`
-- 一致，正反混用会让索引退化成普通列。
--
-- 幂等：`IF NOT EXISTS`，重复应用不报错；不写 DROP（I4 迁移只追加）。
--
-- 注意：这里**不用** `CREATE INDEX CONCURRENTLY`。本仓库迁移是单事务手写 SQL
-- （`scripts/migrate.py` 逐文件执行），CONCURRENTLY 不允许出现在事务块里。
-- 当前数据量（开发/演示库）普通建索引即可；生产大表要另走在线建索引流程。

CREATE INDEX IF NOT EXISTS ingestion_jobs_ws_doc_idx
    ON ingestion_jobs (workspace_id, document_id, created_at DESC);

COMMENT ON INDEX ingestion_jobs_ws_doc_idx IS
    '对齐外键 ingestion_jobs_workspace_id_document_id_fkey，并服务"此文档的作业列表"读';
