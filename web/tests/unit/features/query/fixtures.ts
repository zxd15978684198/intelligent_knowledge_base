import type { QueryResponse, RetrievedChunk } from "@/lib/contracts";

/** N8 用例的造数工具：字段与后端 `schemas/query.py::RetrievedChunk` 一一对应。 */

export const TEST_KB_ID = "11111111-1111-4111-8111-111111111111";
export const TEST_WORKSPACE_ID = "22222222-2222-4222-8222-222222222222";

const BASE_CHUNK: RetrievedChunk = {
  chunk_id: "chunk-0001",
  workspace_id: TEST_WORKSPACE_ID,
  kb_id: TEST_KB_ID,
  document_id: "33333333-3333-4333-8333-333333333333",
  index_version: 3,
  chunk_ordinal: 0,
  content: "默认切片内容。",
  page_no: 1,
  page_end_no: 1,
  heading_path: "概述 > 检索",
  locator_ref: "p1",
  rrf_score: 0.032_522,
  vector_rank: 1,
  keyword_rank: 2,
  active_document_version_id: "44444444-4444-4444-8444-444444444444",
};

export function makeChunk(overrides: Partial<RetrievedChunk> = {}): RetrievedChunk {
  return { ...BASE_CHUNK, ...overrides };
}

export function makeQueryResponse(overrides: Partial<QueryResponse> = {}): QueryResponse {
  return {
    query: "检索是怎么实现的？",
    results: [makeChunk()],
    degraded: {},
    retrieval_strategy: "baseline",
    ...overrides,
  };
}
