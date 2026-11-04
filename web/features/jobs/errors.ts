/**
 * 把 `ApiError` / 普通 `Error` / 未知抛出物统一成可展示的结构。
 *
 * 只认 `ApiError` 声明过的四个字段（DAG §5.2）：
 * `code` / `status` / `details` / `requestId`。
 * 不假设存在 `retryable`——后端响应体里有，但客户端类没有声明，猜它存在就是编。
 */

import { ApiError } from "@/lib/api/client";

export interface ErrorDescription {
  /** 后端 17 个 `ErrorCode` 之一；网络错误 / 非信封错误为 null。 */
  code: string | null;
  /** 可直接展示的中文/英文说明。 */
  message: string;
  /** HTTP 状态码；非 ApiError 为 null。 */
  status: number | null;
  /** 请求标识，便于查日志（计划 §5「500 展示请求标识」）。 */
  requestId: string | null;
}

export function describeApiError(error: unknown): ErrorDescription {
  if (error instanceof ApiError) {
    return {
      code: error.code,
      message: error.message || "请求失败",
      status: error.status,
      requestId: error.requestId ?? null,
    };
  }
  if (error instanceof Error) {
    return { code: null, message: error.message || "请求失败", status: null, requestId: null };
  }
  return { code: null, message: "请求失败（未知错误）", status: null, requestId: null };
}

/** 展示用的一行文案：有错误码就带上，方便用户回报与查日志。 */
export function formatErrorLabel(description: ErrorDescription): string {
  return description.code ? `${description.code}：${description.message}` : description.message;
}
