import { apiRequest } from "@/lib/api/client";

/**
 * 知识库接口层。
 *
 * 契约来源：`src/pyknora/api/v1/knowledge_bases.py` 与 `src/pyknora/schemas/kb.py`。
 * 两个写操作 PATCH / DELETE **必须** 带 `If-Match`（后端 `_expected_version` 会 strip
 * `W/` 与引号，所以直接把 `row_version` 交给 `apiRequest` 的 `ifMatch` 选项）：
 * - 缺头 → 428 `REVISION_MISSING`
 * - 版本不符 → 409 `REVISION_CONFLICT`（调用方重新读取最新版本，不静默覆盖）
 * 别人的 KB 一律 404 `RESOURCE_NOT_FOUND`（后端不泄漏"存在但无权"）。
 */

const KB_PATH = "/api/v1/knowledge-bases";

/** 后端默认 20，上限 100（`Query(ge=1, le=100)`）。 */
export const DEFAULT_KB_PAGE_SIZE = 20;

/** 创建 KB 时固化、之后只读的向量化与切片参数（`EmbeddingProfile`）。 */
export interface KbEmbeddingProfile {
  embedding_model_key: string;
  embedding_dim: number;
  chunk_size: number;
  chunk_overlap: number;
  normalize: boolean;
}

/** `KnowledgeBase` 的读取形态；字段与后端 schema 一一对应。 */
export interface KbSummary {
  kb_id: string;
  workspace_id: string;
  name: string;
  profile: KbEmbeddingProfile;
  active_index_build_id: string | null;
  row_version: number;
  generation: number;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

/** 游标分页：`next_cursor === null` 表示到底，不能把页内条数当总数。 */
export interface KbPage {
  items: KbSummary[];
  next_cursor: string | null;
  limit: number;
}

/** `POST /knowledge-bases` 的请求体（后端 `extra="forbid"`，多一个字段都 422）。 */
export interface CreateKbInput {
  name: string;
  embedding_model_key: string;
  embedding_dim: number;
  chunk_size: number;
  chunk_overlap: number;
  normalize: boolean;
}

/** `DELETE /knowledge-bases/{kb_id}` 的 202 响应体。 */
export interface KbDeleted {
  kb_id: string;
  status: string;
  generation: number;
  row_version: number;
}

/**
 * `workspaceId` 可选：后端 `get_scope` 在没收到 `X-Workspace-ID` 时
 * 回落到 JWT 里的 `default_workspace_id`。前端传它只是为了"在多空间里选一个"。
 */
export interface KbScopeOptions {
  workspaceId?: string | null;
  signal?: AbortSignal;
}

export interface ListKbsParams extends KbScopeOptions {
  cursor?: string | null;
  limit?: number;
}

export interface RenameKbInput extends KbScopeOptions {
  kbId: string;
  name: string;
  rowVersion: number;
}

export interface DeleteKbInput extends KbScopeOptions {
  kbId: string;
  rowVersion: number;
}

function scopeInit(options: KbScopeOptions | undefined): {
  workspaceId?: string;
  signal?: AbortSignal;
} {
  const init: { workspaceId?: string; signal?: AbortSignal } = {};
  if (options?.workspaceId) init.workspaceId = options.workspaceId;
  if (options?.signal) init.signal = options.signal;
  return init;
}

/** 取一页知识库；`cursor` 为空取第一页。 */
export async function listKbs(params: ListKbsParams = {}): Promise<KbPage> {
  const limit = params.limit ?? DEFAULT_KB_PAGE_SIZE;
  const search = new URLSearchParams({ limit: String(limit) });
  if (params.cursor) search.set("cursor", params.cursor);
  return apiRequest<KbPage>(`${KB_PATH}?${search.toString()}`, scopeInit(params));
}

/** 单个知识库详情；404 表示不存在或不属于当前用户。 */
export async function getKb(kbId: string, options: KbScopeOptions = {}): Promise<KbSummary> {
  return apiRequest<KbSummary>(`${KB_PATH}/${encodeURIComponent(kbId)}`, scopeInit(options));
}

/** 新建知识库（201）。`chunk_overlap >= chunk_size` 会被后端以 422 拒绝。 */
export async function createKb(
  input: CreateKbInput,
  options: KbScopeOptions = {},
): Promise<KbSummary> {
  return apiRequest<KbSummary>(KB_PATH, {
    ...scopeInit(options),
    method: "POST",
    body: input,
  });
}

/** 改名：带 `If-Match`，冲突返回 409 `REVISION_CONFLICT`。 */
export async function renameKb(input: RenameKbInput): Promise<KbSummary> {
  return apiRequest<KbSummary>(`${KB_PATH}/${encodeURIComponent(input.kbId)}`, {
    ...scopeInit(input),
    method: "PATCH",
    ifMatch: String(input.rowVersion),
    body: { name: input.name },
  });
}

/** 删除：带 `If-Match`，202 表示已受理（`status: "purging"`）。 */
export async function deleteKb(input: DeleteKbInput): Promise<KbDeleted> {
  return apiRequest<KbDeleted>(`${KB_PATH}/${encodeURIComponent(input.kbId)}`, {
    ...scopeInit(input),
    method: "DELETE",
    ifMatch: String(input.rowVersion),
  });
}
