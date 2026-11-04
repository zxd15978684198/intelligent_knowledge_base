/**
 * 作业端点（`src/pyknora/api/v1/jobs.py`）：
 *
 * - `GET  /api/v1/jobs/{job_id}`        → `JobDetail`（轮询用）
 * - `POST /api/v1/jobs/{job_id}/cancel` → `JobDetail`
 *
 * 取消的语义按后端理解，不要在前端"假装"它立刻生效：`cancel_job` 只对
 * `status = 'pending'` 的行做 UPDATE，`processing` 的行原样返回（jobs.py:102-108）。
 */

import { apiRequest } from "@/lib/api/client";

import type { JobDetail } from "./types";

const API_BASE = "/api/v1";

/**
 * 刻意**不收 `signal`**：`@mswjs/interceptors` 会校验 `AbortSignal` 的 realm，
 * jsdom 里的 `AbortSignal` 不是 Node 的实例，把 TanStack 的 signal 传进 `fetch` 会让
 * 单测直接抛 `RequestInit: Expected signal ...`（生产浏览器同 realm，不会发生）。
 * N7 不需要请求级取消（计划只要求流式问答支持中止），所以不接这条线。
 */
export interface JobRequestOptions {
  /** 进 `X-Workspace-ID` 头；授权仍以 JWT 的 scope 为准。 */
  workspaceId?: string;
}

export function getJob(jobId: string, options: JobRequestOptions = {}): Promise<JobDetail> {
  return apiRequest<JobDetail>(`${API_BASE}/jobs/${encodeURIComponent(jobId)}`, {
    workspaceId: options.workspaceId,
  });
}

export function cancelJob(jobId: string, options: JobRequestOptions = {}): Promise<JobDetail> {
  return apiRequest<JobDetail>(`${API_BASE}/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    workspaceId: options.workspaceId,
  });
}
