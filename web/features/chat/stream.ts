import {
  INVALID_RESPONSE_CODE,
  NETWORK_ERROR_CODE,
  NETWORK_ERROR_STATUS,
  resolveApiPath,
} from "@/lib/api/client";
import { readCsrfToken } from "@/lib/auth/session";
import { errorEnvelopeSchema } from "@/lib/contracts";
import { SseParseError, parseSseStream } from "@/lib/contracts/sse";

import { chatEventFromSse, type ChatEvent, type ChatStreamError } from "./events";

/**
 * 流式问答的传输层：`fetch` + `ReadableStream` + 契约层的 `parseSseStream`。
 *
 * 这里**不走** `apiRequest()`：它统一按 JSON 解析响应体，而本接口返回
 * `text/event-stream`，用它会得到 `INVALID_RESPONSE`。所以只复用它导出的
 * `resolveApiPath` / 错误码常量，fetch 自己做。凭据与 CSRF 头按会话模型自己补：
 * `credentials: "include"` + 写方法 `X-CSRF-Token`（值来自可读 Cookie）。
 *
 * 三条必须先判、不能拖进流里的失败（建流前就能知道，后端也是这么做的）：
 * 1. `!response.ok` → 解析统一错误信封（后端 503 `DEPENDENCY_UNAVAILABLE`：
 *    没配模型密钥时 message 含 "chat" 或 "model"，直接透出原文给用户）；
 * 2. `content-type` 不是 `text/event-stream` → 反代/网关可能回 200 的 HTML，
 *    当成 SSE 去读只会得到一堆半截垃圾；
 * 3. `response.body === null`。
 *
 * 这三种都用 `closed: "failed"` 收尾——一个字都没收到，不该标"回答不完整"。
 *
 * 其余失败发生在流里，只能走事件：
 * - `signal.aborted` → `closed: "aborted"`（用户按了停止，不是错误）；
 * - 读到流尾却没等到 `done` → 先补一帧合成的 `error`（`STREAM_INCOMPLETE`），
 *   再 `closed: "connection"`，让界面能说清"半截 + 连接中断"；
 * - `parseSseStream` 抛 `SseParseError`（帧体不合契约）→ 同样补 `error` +
 *   `closed: "connection"`；此时解析器已经没法继续，尾部的 `done` 也一并丢了。
 *
 * ## 为什么 `signal` 不交给 `fetch`
 *
 * 中止在这里的语义是"让读取停下来"，而 `fetch({ signal })` 只保证"请求被中止"：
 * 请求中止之后挂起的 `reader.read()` 是拒绝还是返回，属于 fetch 实现的自由。自己持
 * reader 再 `cancel()`，语义就只有一个——**挂起的 read 立刻以 `done: true` 结束**，
 * 循环自然退出，无论底层是浏览器 fetch、Node fetch 还是测试里的拦截实现。
 *
 * 还有一个现实原因：`AbortSignal` 与 realm 绑定。vitest 的 jsdom 环境提供的
 * `new AbortController()` 属于 jsdom realm，而 msw 的拦截层（undici）用 Node realm 的
 * `AbortSignal` 做 `instanceof` 校验；把 signal 直接传进 fetch 会抛
 * `TypeError: Expected signal to be an instance of AbortSignal`，整条链路在测试里
 * 永远走成"网络错误"。自己 cancel 就没有这条跨 realm 通道，行为在浏览器与测试里一致。
 */

export interface StreamChatInput {
  /** 知识库 id（路由参数，不是请求体字段）。 */
  readonly kbId: string;
  readonly query: string;
  /** 取回并引用多少条片段；省略时用后端默认值。 */
  readonly topK?: number;
  /** Bearer 兼容通道（命令行 / 换账号）；省略时走会话 Cookie。 */
  readonly token?: string | null;
  /** 显式工作区；省略时后端按 token 里的默认工作区解析。 */
  readonly workspaceId?: string | null;
}

/** 流关闭时消费方要拿到的"没收到 done"标记，供测试与 UI 共用。 */
export const STREAM_INCOMPLETE_CODE = "STREAM_INCOMPLETE";

/** 帧体不合契约（`parseSseStream` 抛错）时用的合成错误码。 */
export const SSE_PARSE_ERROR_CODE = "SSE_PARSE_ERROR";

/**
 * 消费一整次问答。
 *
 * 约定：**任何路径最终都会发出 `closed`**（成功时收到 `done` 之后紧接一条
 * `closed`，状态机看到 `done` 已关流会把迟到的 `closed` 忽略）。这样调用方
 * 只需在自己的清理逻辑里 abort，不必判断"结束了吗"。
 */
export async function streamChat(
  input: StreamChatInput,
  emit: (event: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  if (signal.aborted) {
    emit({ kind: "closed", reason: "aborted" });
    return;
  }

  const body: { query: string; top_k?: number } = { query: input.query };
  if (input.topK !== undefined) {
    body.top_k = input.topK;
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    // SSE 要的是不缓冲、不分块的逐帧透传；显式声明比依赖默认值稳。
    Accept: "text/event-stream",
  };
  if (input.token !== undefined && input.token !== null) {
    headers.Authorization = `Bearer ${input.token}`;
  } else {
    // Cookie 会话：POST 是写方法，必须补双提交 CSRF 头（后端对会话 Cookie 校验）。
    const csrf = readCsrfToken();
    if (csrf !== "") {
      headers["X-CSRF-Token"] = csrf;
    }
  }
  if (input.workspaceId !== undefined && input.workspaceId !== null && input.workspaceId !== "") {
    headers["X-Workspace-Id"] = input.workspaceId;
  }

  let response: Response;
  try {
    response = await fetch(resolveApiPath(`/knowledge-bases/${encodeURIComponent(input.kbId)}/chat`), {
      method: "POST",
      credentials: "include",
      headers,
      body: JSON.stringify(body),
    });
  } catch {
    emit({ kind: "error", error: networkError() });
    emit({ kind: "closed", reason: "failed" });
    return;
  }

  if (signal.aborted) {
    // 建流期间用户已经按了停止：丢掉响应体，不进入解析。
    await cancelQuietly(response.body);
    emit({ kind: "closed", reason: "aborted" });
    return;
  }

  if (!response.ok) {
    emit({ kind: "error", error: await readErrorEnvelope(response) });
    emit({ kind: "closed", reason: "failed" });
    return;
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("text/event-stream")) {
    await cancelQuietly(response.body);
    emit({ kind: "error", error: notSseError(contentType) });
    emit({ kind: "closed", reason: "failed" });
    return;
  }

  if (response.body === null) {
    emit({ kind: "error", error: emptyBodyError() });
    emit({ kind: "closed", reason: "failed" });
    return;
  }

  const readable = abortable(response.body, signal);

  let sawDone = false;
  try {
    for await (const frame of parseSseStream(readable.stream)) {
      const event = chatEventFromSse(frame);
      if (event.kind === "done") {
        sawDone = true;
        emit(event);
        break;
      }
      emit(event);
      if (signal.aborted) {
        break;
      }
    }
  } catch (error) {
    readable.dispose();
    if (signal.aborted) {
      emit({ kind: "closed", reason: "aborted" });
      return;
    }
    emit({ kind: "error", error: parseError(error) });
    emit({ kind: "closed", reason: "connection" });
    return;
  }
  readable.dispose();

  if (signal.aborted) {
    emit({ kind: "closed", reason: "aborted" });
    return;
  }
  if (!sawDone) {
    emit({ kind: "error", error: incompleteError() });
    emit({ kind: "closed", reason: "connection" });
    return;
  }
  emit({ kind: "closed", reason: "connection" });
}

/**
 * 把"允许被中止读取"的视图套在响应体上。
 *
 * 中止时调用 `reader.cancel()`：按流规范，挂起的 `read()` 会立刻以 `done: true`
 * 结束（而不是抛错），`parseSseStream` 于是正常收尾，我们也就能走到
 * `signal.aborted` 那支去发 `closed: aborted`。`dispose` 负责摘掉监听，
 * 否则每次问答都会在 signal 上留一个闭包。
 */
function abortable(
  source: ReadableStream<Uint8Array>,
  signal: AbortSignal,
): { stream: ReadableStream<Uint8Array>; dispose: () => void } {
  const reader = source.getReader();
  const abort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal.addEventListener("abort", abort, { once: true });

  const stream = new ReadableStream<Uint8Array>({
    async pull(controller) {
      const { done, value } = await reader.read();
      if (done) {
        controller.close();
        return;
      }
      controller.enqueue(value);
    },
    cancel() {
      void reader.cancel().catch(() => undefined);
    },
  });

  return {
    stream,
    dispose: () => {
      signal.removeEventListener("abort", abort);
    },
  };
}

async function cancelQuietly(body: ReadableStream<Uint8Array> | null): Promise<void> {
  if (body === null) {
    return;
  }
  try {
    await body.cancel();
  } catch {
    // 已经关闭或已锁定的流：无需处理，这里只是尽量早点放下连接。
  }
}

async function readErrorEnvelope(response: Response): Promise<ChatStreamError> {
  const requestId = response.headers.get("X-Request-ID");
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  const parsed = errorEnvelopeSchema.safeParse(payload);
  if (parsed.success) {
    return {
      code: parsed.data.error.code,
      message: parsed.data.error.message,
      retryable: parsed.data.error.retryable,
      status: response.status,
      requestId: requestId ?? null,
    };
  }
  return {
    code: INVALID_RESPONSE_CODE,
    message: `服务端返回了无法识别的错误响应（HTTP ${response.status}）。`,
    retryable: response.status >= 500,
    status: response.status,
    requestId: requestId ?? null,
  };
}

/**
 * 建流前失败的载体形状：错误帧（`ChatStreamError`）之外多带 `status`/`requestId`，
 * 页面据此区分"没配模型（503）"与"网络断了（0）"，文案与处置完全不同。
 */
function networkError(): ChatStreamError {
  return {
    code: NETWORK_ERROR_CODE,
    message: "网络请求失败，没能建立连接。",
    retryable: true,
    status: NETWORK_ERROR_STATUS,
    requestId: null,
  };
}

function notSseError(contentType: string): ChatStreamError {
  const shown = contentType === "" ? "未声明" : contentType;
  return {
    code: INVALID_RESPONSE_CODE,
    message: `服务端没有返回事件流（content-type: ${shown}），本次问答未开始。`,
    retryable: false,
    status: 0,
    requestId: null,
  };
}

function emptyBodyError(): ChatStreamError {
  return {
    code: INVALID_RESPONSE_CODE,
    message: "服务端返回了空的事件流，本次问答未开始。",
    retryable: true,
    status: 0,
    requestId: null,
  };
}

function parseError(cause: unknown): ChatStreamError {
  const detail = cause instanceof SseParseError ? cause.message : "事件流解析失败。";
  return {
    code: SSE_PARSE_ERROR_CODE,
    message: `事件流格式异常，已保留收到的部分内容：${detail}`,
    retryable: false,
    status: 0,
    requestId: null,
  };
}

function incompleteError(): ChatStreamError {
  return {
    code: STREAM_INCOMPLETE_CODE,
    message: "连接在收到结束帧之前中断，本次回答可能不完整。",
    retryable: true,
    status: 0,
    requestId: null,
  };
}
