/**
 * SSE 夹具（N2）。
 *
 * 帧格式与后端 `api/v1/chat.py:53` 的 `_sse()` 逐字对齐：
 * `event: <name>\ndata: <json>\n\n`（json 用 `ensure_ascii=False`，所以中文原样出现）。
 *
 * 这里既提供"合法帧"（`sseFrame`），也提供"能造坏帧"的原语（`sseRawFrame`）——
 * 解析器的容错口径必须靠坏帧来验证，否则测的只是幸福路径。
 */

import type { Citation, DoneEvent, RetrievalEvent, StreamErrorEvent } from "@/lib/contracts";

/** 拼一帧合法 SSE。`data` 走 `JSON.stringify`，与后端 `json.dumps(..., ensure_ascii=False)` 等价。 */
export function sseFrame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** 拼一帧"data 原文由你决定"的 SSE：用来造非法 JSON、非对象 JSON、多余字段等。 */
export function sseRawFrame(event: string, rawData: string): string {
  return `event: ${event}\ndata: ${rawData}\n\n`;
}

/** 把多帧拼成一段可整段灌进流的文本。 */
export function sseText(frames: readonly string[]): string {
  return frames.join("");
}

export const sampleCitation: Citation = {
  chunk_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  document_id: "33333333-3333-4333-8333-333333333333",
  page_no: 2,
  page_end_no: 2,
  snippet: "把电源接上。",
  active_document_version_id: "44444444-4444-4444-8444-444444444444",
};

/** 两路都成功时 `degraded` 是空对象（非空表示有一路挂了）。 */
export const sampleRetrieval: RetrievalEvent = {
  citations: [sampleCitation],
  degraded: {},
};

/** 向量路挂了的降级状态：键是分支名，值是错误码。 */
export const sampleRetrievalDegraded: RetrievalEvent = {
  citations: [sampleCitation],
  degraded: { vector: "DEPENDENCY_UNAVAILABLE" },
};

export const sampleDone: DoneEvent = {
  status: "COMPLETED",
  run_id: "run-20261101-0001",
  prompt_version: "rag-answer-v3",
  verdict: "SUPPORTED",
  invalid_citations: 0,
  prompt_tokens: 1234,
  completion_tokens: 256,
};

/** 门控判 `INSUFFICIENT` 的收尾帧：状态是 ABSTAINED，不是 FAILED。 */
export const sampleDoneAbstained: DoneEvent = {
  status: "ABSTAINED",
  run_id: "run-20261101-0002",
  prompt_version: "rag-answer-v3",
  verdict: "INSUFFICIENT",
  invalid_citations: 0,
  prompt_tokens: 980,
  completion_tokens: 0,
};

/** 出错后的收尾帧：后端即使在 error 之后也照常发一帧 done（`chat.py:293-294`）。 */
export const sampleDoneAfterError: DoneEvent = {
  status: "FAILED",
  run_id: "run-20261101-0003",
  prompt_version: null,
  verdict: null,
  invalid_citations: 0,
  prompt_tokens: null,
  completion_tokens: null,
};

export const sampleStreamError: StreamErrorEvent = {
  code: "DEPENDENCY_UNAVAILABLE",
  message: "向量检索不可用，本次只走了关键词路",
  retryable: true,
};

export const RETRIEVAL_FRAME = sseFrame("retrieval", sampleRetrieval);
export const TOKEN_FRAME_ONE = sseFrame("token", { text: "先" });
export const TOKEN_FRAME_TWO = sseFrame("token", { text: "接电源。" });
export const DONE_FRAME = sseFrame("done", sampleDone);
export const ERROR_FRAME = sseFrame("error", sampleStreamError);
export const DONE_AFTER_ERROR_FRAME = sseFrame("done", sampleDoneAfterError);

/** 幸福路径：retrieval → token* → done。 */
export function chatHappyPathFrames(): string[] {
  return [RETRIEVAL_FRAME, TOKEN_FRAME_ONE, TOKEN_FRAME_TWO, DONE_FRAME];
}

/**
 * 失败路径：**error 之后还有 done**。
 * 消费方若用"收到 error 就关流"的写法，这一帧就丢了。
 */
export function chatErrorThenDoneFrames(): string[] {
  return [ERROR_FRAME, DONE_AFTER_ERROR_FRAME];
}
