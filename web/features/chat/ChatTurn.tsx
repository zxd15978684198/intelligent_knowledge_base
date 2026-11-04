"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { AnswerStatus } from "./AnswerStatus";
import { DegradedNote } from "./DegradedNote";
import { StreamingAnswer } from "./StreamingAnswer";
import { citedIndices } from "./citations";
import { isStreaming, placeholderFor, turnInvalidIndices, type ConversationTurn } from "./conversation";

/**
 * 一轮问答：提问 → 回答 → 收尾状态。
 *
 * 顺序有意为之：正文在下、状态在最后。用户先读答案，再决定要不要看 run_id 与
 * 引用校验结果；而"证据不足/预算中止/失败"这类终态信息必须紧跟在正文之后，
 * 不能塞进页脚——否则一段被截断的回答看起来仍然像一个完整结论。
 */
export interface ChatTurnProps {
  readonly turn: ConversationTurn;
  /** 本轮被选中的引用序号（父组件按"选中的是哪一轮"给）。 */
  readonly activeCitation: number | null;
  readonly onPickCitation: (index: number) => void;
  readonly className?: string;
}

export function ChatTurn({ turn, activeCitation, onPickCitation, className }: ChatTurnProps): React.ReactElement {
  const { state } = turn;
  const invalidIndices = React.useMemo(() => turnInvalidIndices(turn), [turn]);
  const cited = React.useMemo(() => citedIndices(state.answer), [state.answer]);

  return (
    <article className={cn("space-y-3", className)} data-turn-id={turn.id} data-phase={state.phase}>
      <div className="flex justify-end">
        <p className="max-w-[92%] whitespace-pre-wrap break-words rounded-lg bg-primary-weak px-3 py-2 text-sm text-fg">
          {turn.query}
        </p>
      </div>

      <div className="space-y-2 rounded-lg border border-border bg-surface px-3 py-3">
        <StreamingAnswer
          answer={state.answer}
          citationCount={state.citations.length}
          invalidIndices={invalidIndices}
          streaming={isStreaming(state)}
          placeholder={placeholderFor(state)}
          activeCitation={activeCitation}
          incomplete={state.incomplete}
          onPickCitation={onPickCitation}
        />

        <DegradedNote degraded={state.degraded} />

        <AnswerStatus
          phase={state.phase}
          done={state.done}
          error={state.streamError}
          incomplete={state.incomplete}
          invalidIndices={invalidIndices}
        />

        {state.citations.length > 0 ? (
          <p className="text-[11px] text-fg-subtle">
            本轮引用 {state.citations.length} 条{cited.length > 0 ? `，正文标注了 ${cited.length} 处` : ""}
          </p>
        ) : null}
      </div>
    </article>
  );
}
