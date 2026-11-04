import type { KbSummary } from "@/features/kbs/api";

/**
 * KB 用例的 fixture 与错误信封工厂。
 *
 * 字段必须与后端 `src/pyknora/schemas/kb.py::KnowledgeBase` 对齐（profile 是嵌套对象，
 * 不是扁平的 embedding_model_id）——这里刻意用真实形态，避免用例替后端"想象"字段。
 */

const DEFAULT_KB_ID = "11111111-1111-4111-8111-111111111111";

/** 用例统一使用的工作区 id：进 queryKey 与 `X-Workspace-ID`，避免两次实现各写一个。 */
export const TEST_WORKSPACE_ID = "22222222-2222-4222-8222-222222222222";

export function makeKb(overrides: Partial<KbSummary> = {}): KbSummary {
  return {
    kb_id: DEFAULT_KB_ID,
    workspace_id: TEST_WORKSPACE_ID,
    name: "产品手册",
    profile: {
      embedding_model_key: "BAAI/bge-small-zh-v1.5",
      embedding_dim: 512,
      chunk_size: 512,
      chunk_overlap: 64,
      normalize: true,
    },
    active_index_build_id: null,
    row_version: 1,
    generation: 1,
    deleted_at: null,
    created_at: "2026-11-01T10:00:00Z",
    updated_at: "2026-11-01T10:00:00Z",
    ...overrides,
  };
}

/** 后端统一错误信封：`{"error":{code,message,retryable,details},"request_id":...}`。 */
export function errorEnvelope(
  code: string,
  message = "test error",
  requestId = "req-test-0001",
): { error: { code: string; message: string; retryable: boolean; details: null }; request_id: string } {
  return {
    error: { code, message, retryable: false, details: null },
    request_id: requestId,
  };
}
