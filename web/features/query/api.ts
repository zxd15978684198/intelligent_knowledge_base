import { apiRequest } from "@/lib/api/client";
import type { QueryRequest, QueryResponse } from "@/lib/contracts";

/**
 * 检索调试的接口层（N8）。
 *
 * ## 契约来源与三处「任务书 vs 后端源码」偏差
 *
 * 逐行核对过 `src/pyknora/api/v1/query.py`、`src/pyknora/schemas/query.py` 后按后端实现写，
 * 不按任务书里那句 `POST /api/v1/query` 写——仓库里没有那个路由：
 *
 * 1. **路径带知识库 ID**：真实路由是 `POST /api/v1/knowledge-bases/{kb_id}/query`
 *    （`query.py:29`；`api/main.py:322` 才挂上 `/api/v1` 前缀）。
 * 2. **没有 `score` / `char_start` / `char_end`**：`RetrievedChunk` 只给融合后的 `rrf_score`
 *    与两路排名。字符偏移只存在于切片列表接口（`GET /documents/{id}/chunks` 的
 *    `ActiveChunk.char_start/char_end`），检索响应拿不到原文偏移。所以结果表不做
 *    `char_start–char_end` 高亮，也不在摘要上标一个编出来的偏移——宁可不标，不能造假。
 * 3. **`retrieval_strategy` 是裸 `str`**：后端 `schemas/query.py:105` 明确注释「别收窄」，
 *    契约层（N2）因此也是 `string`。遇到未知取值要原样显示，不能当它不存在。
 *
 * ## 为什么校验要做两层
 *
 * 这里挡住「空问题 / 超长 / top_k 越界」只为省一次注定 422 的往返；服务端入口
 * （`QueryRequest`）与服务层（`search_hybrid`）各自还有一道，前端这一层**不是**安全边界。
 * 上限一律引用后端常量，不静默截断——`top_k=51` 应该报错，而不是悄悄改成 50 再发出去，
 * 否则调试界面会骗人。
 */

/** 后端 `MAX_TOP_K`（`schemas/query.py:20`）。 */
export const QUERY_MAX_TOP_K = 50;
/** 后端 `QueryRequest.top_k` 的默认值（`schemas/query.py:31`）。 */
export const QUERY_DEFAULT_TOP_K = 10;
/** 后端 `QueryRequest.top_k` 的下限（`ge=1`）。 */
export const QUERY_MIN_TOP_K = 1;
/** 后端 `QueryRequest.query` 的 `max_length`（`schemas/query.py:30`）。 */
export const QUERY_MAX_LENGTH = 1000;
/** 下拉框档位：任务书要求 5/10/20/50，其中 10 与后端默认值一致。 */
export const TOP_K_OPTIONS = [5, 10, 20, 50] as const;

const QUERY_PATH = "/api/v1/knowledge-bases";

export interface RunQueryInput {
  query: string;
  /** 省略时用后端默认值 10；给了就必须是 1–50 的整数。 */
  top_k?: number;
}

export interface NormalizedRunQueryInput {
  query: string;
  top_k: number;
}

/** 本地校验失败：没有发出请求，因此没有 HTTP 状态码可给。 */
export type QueryValidationField = "kbId" | "query" | "top_k";

export class QueryValidationError extends Error {
  readonly field: QueryValidationField;

  constructor(field: QueryValidationField, message: string) {
    super(message);
    this.name = "QueryValidationError";
    this.field = field;
  }
}

export function isQueryValidationError(value: unknown): value is QueryValidationError {
  return value instanceof QueryValidationError;
}

/**
 * 检索接口地址。
 *
 * `encodeURIComponent` 不是多余的：`kbId` 来自 URL 动态段，拼路径前必须编码，
 * 否则带 `%2F` / `?` 的病态输入能把请求打到别的路由上去。
 */
export function queryEndpoint(kbId: string): string {
  return `${QUERY_PATH}/${encodeURIComponent(kbId)}/query`;
}

/** 校验并归一化入参；失败抛 `QueryValidationError`（带字段名，表单可据此定位）。 */
export function normalizeQueryInput(input: RunQueryInput): NormalizedRunQueryInput {
  const query = input.query.trim();
  if (query === "") {
    throw new QueryValidationError("query", "请输入检索问题。");
  }
  if (query.length > QUERY_MAX_LENGTH) {
    throw new QueryValidationError(
      "query",
      `问题最多 ${QUERY_MAX_LENGTH} 个字符，当前 ${query.length} 个。`,
    );
  }

  const topK = input.top_k ?? QUERY_DEFAULT_TOP_K;
  if (!Number.isInteger(topK)) {
    throw new QueryValidationError("top_k", "top_k 必须是整数。");
  }
  if (topK < QUERY_MIN_TOP_K || topK > QUERY_MAX_TOP_K) {
    throw new QueryValidationError(
      "top_k",
      `top_k 必须在 ${QUERY_MIN_TOP_K}–${QUERY_MAX_TOP_K} 之间（后端 MAX_TOP_K=${QUERY_MAX_TOP_K}），当前 ${topK}。`,
    );
  }

  return { query, top_k: topK };
}

/**
 * 执行一次检索。
 *
 * `degraded` 为空的含义是「两路都成功」；非空表示**检索降级但仍有结果**
 * （例如 `{"vector": "DEPENDENCY_UNAVAILABLE"}`），调用方必须显式提示，不能当成功。
 * 这里把省略的 `degraded` 归一成 `{}`，组件就不必到处写 `?? {}`。
 */
export async function runQuery(
  kbId: string,
  input: RunQueryInput,
  workspaceId?: string | null,
): Promise<QueryResponse> {
  const id = kbId.trim();
  if (id === "") {
    throw new QueryValidationError("kbId", "缺少知识库 ID，无法发起检索。");
  }
  const body = normalizeQueryInput(input);
  const requestBody: QueryRequest = { query: body.query, top_k: body.top_k };

  const response = await apiRequest<QueryResponse>(queryEndpoint(id), {
    method: "POST",
    body: requestBody,
    // 省略时后端按 JWT 里的 default_workspace_id 解析（api/deps.py:76-84）；不用空串占位。
    ...(workspaceId ? { workspaceId } : {}),
  });

  return { ...response, degraded: response.degraded ?? {} };
}
