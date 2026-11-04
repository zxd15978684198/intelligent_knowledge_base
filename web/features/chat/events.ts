import type {
  AnswerVerdict,
  ChatSseEvent,
  Citation,
  DoneEvent,
  RunStatus,
  StreamErrorEvent,
} from "@/lib/contracts";

/**
 * 流式问答的事件适配层：把 N2 契约层（`@/lib/contracts`）解析好的 SSE 帧，
 * 翻译成状态机（`machine.ts`）消费的形状。
 *
 * 为什么还要这一层：
 * 1. 契约层是**线上形状**（`{event, data}` 判别联合，字段 snake_case）；
 *    状态机要的是**语义事件**（`{kind, ...}`），两者混用会让 reducer 里到处 `data.`。
 * 2. 契约层的 `error.retryable` 是 `?: boolean`（镜像 pydantic 默认值），
 *    状态机内部统一归一成 `boolean`，省得每个消费点各写一次 `?? false`。
 * 3. 传输层独有的 `closed` 事件（见下）不在契约里，只能在这里加。
 *
 * 已校正的两处任务书偏差（以 `src/pyknora/api/v1/chat.py` 为准）：
 * - `done.invalid_citations` 是**条数**（`len(invalid)` 的 int），不是 id 列表；
 * - 端点带 kb 段：`POST /api/v1/knowledge-bases/{kb_id}/chat`。
 */

export type { AnswerVerdict, Citation, DoneEvent, RunStatus, StreamErrorEvent };

export type ChatCitation = Citation;
export type ChatDone = DoneEvent;

/** 归一后的错误帧：`retryable` 一定存在。 */
export interface ChatStreamError {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
  /** 建流前失败时是真实 HTTP 状态码；流内合成的错误为 `0`。 */
  readonly status?: number;
  /** 后端响应头/信封里的 request id，报错文案带上便于在日志里定位。 */
  readonly requestId?: string | null;
}

/** 状态机消费的事件：线上四帧 + 一条**只存在于传输层**的 `closed`。 */
export type ChatEvent =
  | {
      readonly kind: "retrieval";
      readonly citations: readonly ChatCitation[];
      readonly degraded: Readonly<Record<string, string>>;
    }
  | { readonly kind: "token"; readonly text: string }
  | { readonly kind: "done"; readonly done: ChatDone }
  | { readonly kind: "error"; readonly error: ChatStreamError }
  /**
   * 流已经关了，且**不会再有任何事件**。
   *
   * 为什么必须有它：线上 `error` 帧后面按契约必跟一帧 `done`（AGENTS.md 坑 9：
   * 流式端点里不能抛 AppError，失败要降级成 error 事件再补 done 收尾），所以收到
   * `error` **不能**当作终态。真正的终态只有两种：收到 `done`，或者连接关掉。
   * 把"连接关掉"建模成独立事件，状态机才不必去猜 `error` 之后还有没有下文。
   *
   * - `aborted`：用户主动中止（AbortController）；
   * - `connection`：读到流尾却没等到 `done`（半截回答要标"不完整"）；
   * - `failed`：建流前就失败（非 2xx / 非 SSE / 网络错误），一个字都没收到。
   */
  | { readonly kind: "closed"; readonly reason: "aborted" | "connection" | "failed" };

/** 契约层帧 → 状态机事件。纯函数，`stream.ts` 与单测都用它。 */
export function chatEventFromSse(event: ChatSseEvent): ChatEvent {
  switch (event.event) {
    case "retrieval":
      return {
        kind: "retrieval",
        citations: event.data.citations,
        degraded: event.data.degraded,
      };
    case "token":
      return { kind: "token", text: event.data.text };
    case "done":
      return { kind: "done", done: event.data };
    case "error":
      return {
        kind: "error",
        error: {
          code: event.data.code,
          message: event.data.message,
          retryable: event.data.retryable ?? false,
        },
      };
  }
}
