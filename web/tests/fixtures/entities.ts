/**
 * 实体夹具（N2）。
 *
 * 这些对象**显式标注类型**，所以它们本身就是一次编译期断言：字段改名、可选性变化、
 * 类型写错都会在这里报错。比"照着 JSON 手抄一份 mock"多一层保障。
 *
 * 取值说明：`filename` 故意给中文名（后端从对象键末段解析，可能是任意 UTF-8）；
 * 指针字段故意留 `null`（真实里"还没发布"就是这个状态）。
 */

import type {
  ChunkItem,
  DocumentDetail,
  DocumentSummary,
  HealthResponse,
  JobDetail,
  KnowledgeBase,
  MembershipDetail,
  RetrievedChunk,
} from "@/lib/contracts";

/** 稳定的假 UUID。cheap 且一眼能看出是夹具。 */
export const FIXTURE_WS_ID = "11111111-1111-4111-8111-111111111111";
export const FIXTURE_KB_ID = "22222222-2222-4222-8222-222222222222";
export const FIXTURE_DOC_ID = "33333333-3333-4333-8333-333333333333";
export const FIXTURE_VERSION_ID = "44444444-4444-4444-8444-444444444444";
export const FIXTURE_JOB_ID = "55555555-5555-4555-8555-555555555555";

export const sampleKnowledgeBase: KnowledgeBase = {
  kb_id: FIXTURE_KB_ID,
  workspace_id: FIXTURE_WS_ID,
  name: "产品手册",
  profile: {
    embedding_model_key: "test/hashing",
    embedding_dim: 512,
    chunk_size: 800,
    chunk_overlap: 120,
    normalize: true,
  },
  // 还没建过索引：真实里新建 KB 就是这个形状。
  active_index_build_id: null,
  row_version: 3,
  generation: 1,
  deleted_at: null,
  created_at: "2026-10-30T02:00:00Z",
  updated_at: "2026-10-31T08:30:00Z",
};

/** 已发布的文档：三个指针都有值、`searchable=true`。 */
export const sampleDocumentSummary: DocumentSummary = {
  document_id: FIXTURE_DOC_ID,
  kb_id: FIXTURE_KB_ID,
  filename: "手册 v2.pdf",
  active_document_version_id: FIXTURE_VERSION_ID,
  active_build_id: "66666666-6666-4666-8666-666666666666",
  active_index_version: 1,
  generation: 2,
  row_version: 5,
  searchable: true,
  created_at: "2026-10-31T09:00:00Z",
};

/** 刚上传、还没发布的文档：指针全 null、`searchable=false`，UI 要显示"解析中"。 */
export const sampleDocumentPending: DocumentSummary = {
  document_id: "77777777-7777-4777-8777-777777777777",
  kb_id: FIXTURE_KB_ID,
  // 对象键里解析不出文件名时后端回落成短 document_id，用户看到的就是这串 id。
  filename: "77777777",
  active_document_version_id: null,
  active_build_id: null,
  active_index_version: null,
  generation: 1,
  row_version: 1,
  searchable: false,
  created_at: "2026-10-31T10:00:00Z",
};

export const sampleDocumentDetail: DocumentDetail = {
  document_id: FIXTURE_DOC_ID,
  workspace_id: FIXTURE_WS_ID,
  kb_id: FIXTURE_KB_ID,
  filename: "手册 v2.pdf",
  active_document_version_id: FIXTURE_VERSION_ID,
  active_build_id: "66666666-6666-4666-8666-666666666666",
  active_index_version: 1,
  generation: 2,
  row_version: 5,
  searchable: true,
};

/** 正在跑的作业：`terminal=false`。 */
export const sampleJobProcessing: JobDetail = {
  job_id: FIXTURE_JOB_ID,
  workspace_id: FIXTURE_WS_ID,
  document_id: FIXTURE_DOC_ID,
  job_type: "ingest",
  status: "processing",
  attempt: 1,
  error_code: null,
  terminal: false,
};

/**
 * 被新构建取代的作业：**`terminal` 仍是 `false`**（后端 `TERMINAL_STATUSES` 漏了
 * superseded）。这个夹具就是用来钉住 `isJobTerminal` 兜底的。
 */
export const sampleJobSuperseded: JobDetail = {
  job_id: FIXTURE_JOB_ID,
  workspace_id: FIXTURE_WS_ID,
  document_id: FIXTURE_DOC_ID,
  job_type: "ingest",
  status: "superseded",
  attempt: 1,
  error_code: null,
  terminal: false,
};

export const sampleMemberOwner: MembershipDetail = {
  membership_id: "88888888-8888-4888-8888-888888888888",
  workspace_id: FIXTURE_WS_ID,
  user_id: "99999999-9999-4999-8999-999999999999",
  role: "owner",
};

export const sampleChunk: ChunkItem = {
  chunk_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  workspace_id: FIXTURE_WS_ID,
  kb_id: FIXTURE_KB_ID,
  document_id: FIXTURE_DOC_ID,
  build_id: "66666666-6666-4666-8666-666666666666",
  index_version: 1,
  attempt_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  chunk_ordinal: 0,
  content: "第一节 安装说明\n\n把电源接上。",
  page_no: 1,
  page_end_no: 1,
  heading_path: "第一章/安装",
  char_start: 0,
  char_end: 21,
  token_count: 18,
  locator_ref: "p1",
  embedding_model_key: "test/hashing",
  embedding_dim: 512,
  chunk_size: 800,
  active_document_version_id: FIXTURE_VERSION_ID,
};

/** 检索片段：关键词路命中、向量路没命中（`vector_rank` 为 null 是合法状态）。 */
export const sampleRetrievedChunk: RetrievedChunk = {
  chunk_id: sampleChunk.chunk_id,
  workspace_id: FIXTURE_WS_ID,
  kb_id: FIXTURE_KB_ID,
  document_id: FIXTURE_DOC_ID,
  index_version: 1,
  chunk_ordinal: 0,
  content: sampleChunk.content,
  page_no: 1,
  page_end_no: 1,
  heading_path: "第一章/安装",
  locator_ref: "p1",
  rrf_score: 0.016393,
  vector_rank: null,
  keyword_rank: 1,
  active_document_version_id: FIXTURE_VERSION_ID,
};

export const sampleHealth: HealthResponse = {
  status: "ok",
  kb_repo: "SqlKnowledgeBaseRepo",
  embedder: "test/hashing",
};
