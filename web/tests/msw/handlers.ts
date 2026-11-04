import { http, HttpResponse, type HttpHandler } from "msw";

import type { ErrorEnvelope, HealthResponse } from "@/lib/contracts";

/**
 * 全局默认 handlers —— 只放"所有用例都需要"的基线（健康检查）。
 * 业务分支由各 feature 的用例用 `server.use(...)` 局部覆盖（见 tests/msw/server.ts）。
 *
 * 这里额外导出的是**造桩工具**（错误信封、SSE 流），不是 handler：
 * 每个 feature 用例都要伪造 401/409/422 和流式回答，各写一份必然漂移——
 * 尤其是错误信封，少一层 `{error: ...}` 就会让 `apiRequest` 走成 `INVALID_RESPONSE`
 * 分支，用例于是"测了个假问题"。
 */

/** 统一注入的请求 ID。断言里要带上它，才能证明客户端把 `X-Request-ID` 传给了 `ApiError`。 */
export const TEST_REQUEST_ID = "req-test-0001";

/** `/health` 的基线回执。`kb_repo` 与 `embedder` 取后端真实类名/模型键（`main.py:300`）。 */
export const healthFixture: HealthResponse = {
  status: "ok",
  kb_repo: "SqlKnowledgeBaseRepo",
  embedder: "test/hashing",
};

/** 按后端 `core/errors.py` 的形状拼信封，字段缺省值与后端一致（`retryable=False`、`details=None`）。 */
export function errorEnvelopeBody(input: {
  code: string;
  message: string;
  retryable?: boolean;
  details?: unknown;
}): ErrorEnvelope {
  return {
    error: {
      code: input.code,
      message: input.message,
      retryable: input.retryable ?? false,
      details: input.details ?? null,
    },
    request_id: TEST_REQUEST_ID,
  };
}

/**
 * 伪造一次错误响应（带 `X-Request-ID`，与 `main.py` 的中间件一致）。
 *
 * ```ts
 * server.use(http.get("/api/v1/knowledge-bases", () =>
 *   errorResponse(403, { code: "FORBIDDEN", message: "没有该工作区权限" })));
 * ```
 */
export function errorResponse(
  status: number,
  input: { code: string; message: string; retryable?: boolean; details?: unknown },
  requestId: string = TEST_REQUEST_ID,
): Response {
  return HttpResponse.json(errorEnvelopeBody(input), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": requestId },
  });
}

const SSE_HEADERS = {
  "Content-Type": "text/event-stream",
  "Cache-Control": "no-cache",
  // 后端显式关掉 nginx 缓冲（`api/v1/chat.py`），桩里带上，免得测试与线上行为不一致。
  "X-Accel-Buffering": "no",
} as const;

/** 一次性给出整段 SSE 文本（最常用；不模拟网络分片）。 */
export function sseResponse(frames: readonly string[]): Response {
  return new HttpResponse(frames.join(""), { status: 200, headers: SSE_HEADERS });
}

/**
 * 把任意切片按顺序灌进一个 `ReadableStream`，用于**真的**跨越 chunk 边界。
 *
 * 只给了字符串拼接的桩是测不出解析器 bug 的：半帧、`\r\n` 被切开、UTF-8 多字节
 * 被切开这三种情况，只有在字节层面分开才会出现。
 */
export function chunkedResponse(chunks: readonly string[], headers: Record<string, string> = SSE_HEADERS): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new HttpResponse(stream, { status: 200, headers });
}

export const handlers: HttpHandler[] = [http.get("/health", () => HttpResponse.json(healthFixture))];
