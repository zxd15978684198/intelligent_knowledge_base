/**
 * `@/features/chat` —— N9 流式问答的唯一出口。
 *
 * 分层（自下而上，全部可单独测）：
 * - `machine.ts`：纯状态机（`idle|retrieving|streaming|done|aborted|errored`）；
 * - `stream.ts`：`fetch + ReadableStream` 传输层，把 SSE 帧变成状态机事件；
 * - `events.ts`：契约层帧 → 状态机事件的适配；
 * - `citations.ts`：`[资料 N]` 解析、越界判定、证据行构造（纯函数）；
 * - `conversation.ts`：多轮会话模型（前端会话，后端无会话接口）；
 * - 组件：`StreamingAnswer` / `CitationRail` / `AnswerStatus` / `DegradedNote` / `ChatComposer` / `ChatTurn`；
 * - `ChatScreen`：把上面这些接起来（页面 `app/(app)/kbs/[kbId]/chat/page.tsx` 只取路由参数）。
 */

export { AnswerStatus, type AnswerStatusProps } from "./AnswerStatus";
export { ChatComposer, type ChatComposerProps } from "./ChatComposer";
export { ChatScreen, type ChatScreenProps } from "./ChatScreen";
export { ChatTurn, type ChatTurnProps } from "./ChatTurn";
export { CitationRail, type CitationRailProps } from "./CitationRail";
export { DegradedNote, type DegradedNoteProps } from "./DegradedNote";
export { StreamingAnswer, type StreamingAnswerProps } from "./StreamingAnswer";

export {
  buildCitationRows,
  citedIndices,
  citationForIndex,
  formatPageRange,
  invalidCitedIndices,
  isCitationIndexInRange,
  segmentAnswer,
  type AnswerSegment,
  type CitationRow,
} from "./citations";

export {
  chatSessionReducer,
  initialChatSession,
  isStreaming,
  latestTurnWithCitations,
  placeholderFor,
  turnForSelection,
  turnInvalidIndices,
  type ChatSession,
  type ChatSessionAction,
  type CitationSelection,
  type ConversationTurn,
} from "./conversation";

export { chatEventFromSse, type ChatCitation, type ChatDone, type ChatEvent, type ChatStreamError } from "./events";

export {
  canStop,
  chatReducer,
  initialChatState,
  isAwaitingDoneAfterError,
  isRunActive,
  type ChatAction,
  type ChatPhase,
  type ChatState,
} from "./machine";

export { SSE_PARSE_ERROR_CODE, STREAM_INCOMPLETE_CODE, streamChat, type StreamChatInput } from "./stream";

export { useWideLayout } from "./use-wide-layout";
