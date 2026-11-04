/**
 * 文档域的读写在**一个文件**里收口，端点与后端一一对应
 * （`src/pyknora/api/v1/documents.py`、`src/pyknora/api/v1/lineage.py`）：
 *
 * - `POST   /api/v1/knowledge-bases/{kb_id}/documents`  multipart `file` + `Idempotency-Key`
 * - `GET    /api/v1/knowledge-bases/{kb_id}/documents`  ?limit&offset（total 是全量）
 * - `GET    /api/v1/documents/{document_id}`
 * - `GET    /api/v1/documents/{document_id}/chunks`     ?limit&offset（total 是页内条数）
 * - `GET    /api/v1/documents/{document_id}/lineage`    版本链 + 贡献者（**没有发布事件**）
 * - `DELETE /api/v1/documents/{document_id}`            202 + `status: "purging"`
 */

import { apiRequest, apiUpload } from "@/lib/api/client";

import type { ChunkPage, DocumentAccepted, DocumentDetail, DocumentLineage, DocumentPage } from "./types";

const API_BASE = "/api/v1";

/** 列表分页大小。后端 `limit` 默认 50、上限 500（`documents.py:224`）。 */
export const DOCUMENTS_PAGE_SIZE = 20;

/**
 * 切片每页条数。后端默认 100、上限 500（`documents.py:310`）。
 * 取 20 是为了让"加载更多"有意义：一页 100 条时第一屏就要滚很久。
 */
export const CHUNKS_PAGE_SIZE = 20;

/**
 * 刻意**不收 `signal`**：`@mswjs/interceptors` 校验 `AbortSignal` 的 realm，jsdom 的
 * `AbortSignal` 不是 Node 实例，把 TanStack 的 signal 传进 `fetch` 会让单测抛
 * `RequestInit: Expected signal ...`（生产浏览器同 realm 不会发生）。
 * N7 不需要请求级取消——计划只要求流式问答可中止。
 */
export interface RequestOptions {
  /** 进 `X-Workspace-ID` 头；授权仍以 JWT 的 scope 为准。 */
  workspaceId?: string;
}

export interface ListDocumentsParams extends RequestOptions {
  kbId: string;
  limit?: number;
  offset?: number;
}

export function listDocuments({
  kbId,
  workspaceId,
  limit = DOCUMENTS_PAGE_SIZE,
  offset = 0,
}: ListDocumentsParams): Promise<DocumentPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiRequest<DocumentPage>(
    `${API_BASE}/knowledge-bases/${encodeURIComponent(kbId)}/documents?${query.toString()}`,
    { workspaceId },
  );
}

export function getDocument(documentId: string, options: RequestOptions = {}): Promise<DocumentDetail> {
  return apiRequest<DocumentDetail>(`${API_BASE}/documents/${encodeURIComponent(documentId)}`, {
    workspaceId: options.workspaceId,
  });
}

export interface ListChunksParams extends RequestOptions {
  documentId: string;
  limit?: number;
  offset?: number;
}

export function listChunks({
  documentId,
  workspaceId,
  limit = CHUNKS_PAGE_SIZE,
  offset = 0,
}: ListChunksParams): Promise<ChunkPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiRequest<ChunkPage>(
    `${API_BASE}/documents/${encodeURIComponent(documentId)}/chunks?${query.toString()}`,
    { workspaceId },
  );
}

export function getLineage(documentId: string, options: RequestOptions = {}): Promise<DocumentLineage> {
  return apiRequest<DocumentLineage>(`${API_BASE}/documents/${encodeURIComponent(documentId)}/lineage`, {
    workspaceId: options.workspaceId,
  });
}

/** 删除：后端先阻断可见性、再异步清理，所以回 202 + `status: "purging"`。 */
export function deleteDocument(documentId: string, options: RequestOptions = {}): Promise<DocumentAccepted> {
  return apiRequest<DocumentAccepted>(`${API_BASE}/documents/${encodeURIComponent(documentId)}`, {
    method: "DELETE",
    workspaceId: options.workspaceId,
  });
}

export interface UploadDocumentParams extends RequestOptions {
  kbId: string;
  file: File;
  /**
   * 同一次上传的所有重试必须复用同一个键（见 `idempotency.ts`）。
   * 后端交给 `upload_document` 做"同一请求重复提交"的去重。
   */
  idempotencyKey: string;
}

export interface UploadDocumentResult {
  accepted: DocumentAccepted;
  /** 202 = 新建并已排队；200 = 内容重复（`deduped: true`，没有作业）。 */
  status: number;
}

/**
 * 上传。
 *
 * 必须用 `apiUpload` 而不是 `apiRequest`，两个理由都不是口味问题：
 * 1. 它**不设 `Content-Type`**——边界串由浏览器从 `FormData` 生成，手写必然缺 boundary，
 *    后端会解析不出 `file` 字段直接 422；
 * 2. 它**返回状态码**——后端新建回 202、命中内容去重回 200（`documents.py:199-202`）。
 *    只读 body 也能从 `deduped` 推断，但状态码是契约本身，不该靠字段反推。
 */
export function uploadDocument({
  kbId,
  file,
  idempotencyKey,
  workspaceId,
}: UploadDocumentParams): Promise<UploadDocumentResult> {
  const form = new FormData();
  // 第三参显式给 filename：不依赖 File 自带的 name，也让 MSW 里的断言稳定。
  form.append("file", file, file.name);
  return apiUpload<DocumentAccepted>(
    `${API_BASE}/knowledge-bases/${encodeURIComponent(kbId)}/documents`,
    form,
    { method: "POST", idempotencyKey, workspaceId },
  ).then(({ data, status }) => ({ accepted: data, status }));
}
