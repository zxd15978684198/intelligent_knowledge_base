import type { ChatCitation, ChatDone, ChatEvent, ChatStreamError } from "./events";

/**
 * 流式问答状态机 —— 纯 `useReducer`，无 XState、无副作用、无 DOM。
 *
 * 事件顺序（后端 `chat.py` 固定）：`retrieval` → `token`* → `done`；出错时先来一帧
 * `error`，**之后仍会补一帧 `done`**（流已开始就改不了状态码，AGENTS.md 坑 9）。
 *
 * 状态与迁移：
 *
 * ```text
 *            START
 *   idle ──────────────▶ retrieving
 *                          │  EVENT:retrieval（拿到引用与降级）
 *                          ▼
 *                        streaming ◀──── EVENT:token（逐块追加正文）
 *                          │
 *        ┌─────────────────┼─────────────────────┬───────────────────┐
 *        │ EVENT:error     │ EVENT:done          │ ABORT / closed:aborted
 *        ▼                 ▼                     ▼
 *      errored ── EVENT:done ──▶ done          aborted
 *   (awaitingDone 仍为 true)                  (incomplete=true)
 *        │                                          ▲
 *        │ closed:connection                        │
 *        ▼                                          │
 *      errored（incomplete=true）                    │
 *                                                    │
 *   任何 awaitingDone=true 的状态 ── closed:failed ──▶ errored（incomplete=false）
 *
 *   RESET（或 START）从任何状态回到 idle / retrieving
 * ```
 *
 * 两个不变量值得单独说明：
 *
 * 1. **`awaitingDone` 才是"流还开着"的唯一判据**，不是 `phase`。收到 `error` 后
 *    `phase` 已经是 `errored`，但流并没有结束——必须继续等 `done`，否则终态、
 *    `run_id`、`invalid_citations` 全都拿不到，界面会永远停在"失败"。
 *    `isAwaitingDoneAfterError()` 把这个组合显式暴露出去。
 * 2. **`incomplete` 只表示"回答被截断"**。建流前就失败（503）时一个字都没收到，
 *    标成"不完整"会诱导用户以为答案丢了一半；那种情况只置 `errored`。
 */

export type ChatPhase = "idle" | "retrieving" | "streaming" | "done" | "aborted" | "errored";

export interface ChatState {
  readonly phase: ChatPhase;
  /** 已收到的正文（逐块拼接，**不做任何打字机模拟**）。 */
  readonly answer: string;
  readonly citations: readonly ChatCitation[];
  readonly degraded: Readonly<Record<string, string>>;
  /** 线上 `error` 帧（或传输层合成的错误）。收到 `done` 后仍保留，供终态一并展示。 */
  readonly streamError: ChatStreamError | null;
  readonly done: ChatDone | null;
  /** 流已关闭但没收到 `done`：半截回答要显式标注。 */
  readonly incomplete: boolean;
  /** 流还开着 = 还能收事件、还可以中止。 */
  readonly awaitingDone: boolean;
  /** 收到过几块 token。用来区分"检索已回但首字未到"和"已经开始输出"。 */
  readonly tokenEvents: number;
}

export const initialChatState: ChatState = {
  phase: "idle",
  answer: "",
  citations: [],
  degraded: {},
  streamError: null,
  done: null,
  incomplete: false,
  awaitingDone: false,
  tokenEvents: 0,
};

export type ChatAction =
  | { readonly type: "START" }
  | { readonly type: "EVENT"; readonly event: ChatEvent }
  | { readonly type: "ABORT" }
  | { readonly type: "RESET" };

/** 流还开着：能收事件、能中止。 */
export function isRunActive(state: ChatState): boolean {
  return state.awaitingDone;
}

/** "已收到 error 但还在等 done" —— 这个组合必须能被表达和断言。 */
export function isAwaitingDoneAfterError(state: ChatState): boolean {
  return state.awaitingDone && state.streamError !== null;
}

/** 是否能点"停止"：只在流还开着时成立。 */
export function canStop(state: ChatState): boolean {
  return state.awaitingDone;
}

function close(state: ChatState, phase: ChatPhase, incomplete: boolean): ChatState {
  if (!state.awaitingDone) {
    // 迟到的收尾事件（比如 done 之后又来一条 closed）：保持已定的终态，不许翻案。
    return state;
  }
  return { ...state, phase, awaitingDone: false, incomplete };
}

function reduceEvent(state: ChatState, event: ChatEvent): ChatState {
  switch (event.kind) {
    case "retrieval": {
      if (!state.awaitingDone) {
        return state;
      }
      return {
        ...state,
        phase: "streaming",
        citations: event.citations,
        degraded: event.degraded,
      };
    }
    case "token": {
      if (!state.awaitingDone) {
        return state;
      }
      return {
        ...state,
        phase: "streaming",
        answer: state.answer + event.text,
        tokenEvents: state.tokenEvents + 1,
      };
    }
    case "error": {
      if (!state.awaitingDone) {
        return state;
      }
      // `phase` 立刻变成 errored，但 awaitingDone 保持 true：后端在 error 之后还会
      // 补一帧 done（终态 FAILED），那是唯一能拿到 run_id 的地方。
      return { ...state, phase: "errored", streamError: event.error };
    }
    case "done": {
      if (!state.awaitingDone) {
        return state;
      }
      return {
        ...state,
        phase: "done",
        done: event.done,
        awaitingDone: false,
        incomplete: false,
      };
    }
    case "closed": {
      if (!state.awaitingDone) {
        return state;
      }
      switch (event.reason) {
        case "aborted":
          return close(state, "aborted", true);
        case "connection":
          return close(state, "errored", true);
        case "failed":
          // 建流前就失败：一个字都没收到，标"不完整"会误导。
          return close(state, "errored", false);
      }
    }
  }
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "START":
      return { ...initialChatState, phase: "retrieving", awaitingDone: true };
    case "EVENT":
      return reduceEvent(state, action.event);
    case "ABORT":
      return close(state, "aborted", true);
    case "RESET":
      return initialChatState;
    default: {
      // 联合类型已穷尽；运行时兜底只可能来自历史 dispatch 混用，原样返回。
      return state;
    }
  }
}
