import { invalidCitedIndices } from "./citations";
import type { ChatEvent } from "./events";
import { chatReducer, initialChatState, type ChatState } from "./machine";

/**
 * 前端会话模型 + 会话级 reducer。
 *
 * 后端**没有会话 API**（`POST /knowledge-bases/{kb_id}/chat` 每次都是独立的一次问答），
 * 所以"多轮"只存在于前端：每一轮是一个 `ConversationTurn`，各自带一份完整状态
 * （`machine.ts` 的 `ChatState`，一轮一个状态机实例）。刷新页面即丢失——这是接口事实，
 * 不是偷懒；界面不承诺"对话会被保存"，也不做断线重放。
 *
 * 为什么是"会话 reducer 套状态机 reducer"而不是"每轮一个 useReducer"：轮数是运行时
 * 才确定的，动态数量的 hook 写不出来；而把 `chatReducer` 纯粹地套在 `turns[i].state` 上
 * 既保持了状态机的封闭性（它只看到事件，不知道自己是第几轮），又让整段会话只有一次
 * `useReducer`——状态更新天然原子。
 */
export interface ConversationTurn {
  /** 前端本地 id，仅用于 React key 与"当前选中证据属于哪一轮"。 */
  readonly id: string;
  readonly query: string;
  readonly state: ChatState;
}

/** 当前选中的引用：属于哪一轮、1 起序号。 */
export interface CitationSelection {
  readonly turnId: string;
  readonly index: number;
}

export interface ChatSession {
  readonly turns: readonly ConversationTurn[];
  /** 正在跑（或最后跑过）的那一轮。 */
  readonly activeTurnId: string | null;
  readonly selectedCitation: CitationSelection | null;
}

export const initialChatSession: ChatSession = {
  turns: [],
  activeTurnId: null,
  selectedCitation: null,
};

export type ChatSessionAction =
  | { readonly type: "SUBMIT"; readonly id: string; readonly query: string }
  | { readonly type: "EVENT"; readonly id: string; readonly event: ChatEvent }
  | { readonly type: "ABORT"; readonly id: string }
  | { readonly type: "SELECT_CITATION"; readonly turnId: string; readonly index: number }
  /** 清空整段会话（不做"保留上文的续聊"：后端没有上下文，续聊只是视觉错觉）。 */
  | { readonly type: "CLEAR" };

function updateTurn(
  session: ChatSession,
  id: string,
  update: (state: ChatState) => ChatState,
): ChatSession {
  let changed = false;
  const turns = session.turns.map((turn) => {
    if (turn.id !== id) {
      return turn;
    }
    const nextState = update(turn.state);
    if (nextState === turn.state) {
      return turn;
    }
    changed = true;
    return { ...turn, state: nextState };
  });
  return changed ? { ...session, turns } : session;
}

export function chatSessionReducer(session: ChatSession, action: ChatSessionAction): ChatSession {
  switch (action.type) {
    case "SUBMIT": {
      const turn: ConversationTurn = {
        id: action.id,
        query: action.query,
        state: chatReducer(initialChatState, { type: "START" }),
      };
      return {
        turns: [...session.turns, turn],
        activeTurnId: action.id,
        selectedCitation: null,
      };
    }
    case "EVENT":
      return updateTurn(session, action.id, (state) => chatReducer(state, { type: "EVENT", event: action.event }));
    case "ABORT":
      return updateTurn(session, action.id, (state) => chatReducer(state, { type: "ABORT" }));
    case "SELECT_CITATION": {
      const exists = session.turns.some((turn) => turn.id === action.turnId);
      if (!exists) {
        return session;
      }
      return { ...session, selectedCitation: { turnId: action.turnId, index: action.index } };
    }
    case "CLEAR":
      return initialChatSession;
  }
}

/** 流还开着（能收事件、能中止）。 */
export function isStreaming(state: ChatState): boolean {
  return state.phase === "retrieving" || state.phase === "streaming";
}

/** 首字未到时的占位文案：先检索、后生成，两句话说的不是同一件事。 */
export function placeholderFor(state: ChatState): string {
  return state.phase === "retrieving" ? "正在检索资料…" : "正在生成回答…";
}

/**
 * 本轮"越界引用"的序号（1 起）。
 *
 * 后端只发条数（`done.invalid_citations`），逐条定位是前端按同一套正则反算的
 * ——见 `citations.ts` 的口径说明。返回空数组时不要据此断言"后端说 0 条"，
 * 两者可能不一致（后端统计的是它自己解析出的标记）。
 */
export function turnInvalidIndices(turn: ConversationTurn): number[] {
  return invalidCitedIndices(turn.state.answer, turn.state.citations.length);
}

/** 最近一轮有引用的问答（右侧证据栏的默认对象）。 */
export function latestTurnWithCitations(turns: readonly ConversationTurn[]): ConversationTurn | null {
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index];
    if (turn !== undefined && turn.state.citations.length > 0) {
      return turn;
    }
  }
  return null;
}

/** 当前选中的那一轮；没选中时用"最近一轮有引用的"。 */
export function turnForSelection(session: ChatSession): ConversationTurn | null {
  const selection = session.selectedCitation;
  if (selection !== null) {
    const matched = session.turns.find((turn) => turn.id === selection.turnId);
    if (matched !== undefined) {
      return matched;
    }
  }
  return latestTurnWithCitations(session.turns);
}
