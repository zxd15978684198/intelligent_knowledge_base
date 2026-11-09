"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { segmentAnswer } from "./citations";

/**
 * 逐字渲染回答正文。
 *
 * 三条刻意的取舍：
 *
 * 1. **不做假打字机**。正文按收到的 token 原样拼接、原样渲染；前端再加一层定时器
 *    逐字吐字，只会让"已经收到的字"变慢，还会在断流时掩盖"这里断掉了"的事实。
 * 2. **`[资料 N]` 是按钮，不是纯文本**。正文里的标记与右侧证据栏是同一份状态的
 *    两个视图，点正文能定位证据、点证据能定位正文；用 `<button>` 还顺带拿到了
 *    键盘可达与读屏语义（纯 `<span onClick>` 两者都没有）。
 * 3. **高亮靠 ref + native focus 定位**。父组件把"当前选中的序号"传下来，这里用
 *    `markerRefs` 找到对应节点并 `focus()`；滚动交给浏览器的焦点行为，不做
 *    `scrollTop`/`getBoundingClientRect` 那套手算——后者在流式重渲染时必然算错。
 */
export interface StreamingAnswerProps {
  readonly answer: string;
  /** 本次检索返回的引用条数：用它判断正文里的 `[资料 N]` 是否越界。 */
  readonly citationCount: number;
  /** 越界序号（1 起），用于把标记标红。父组件用 `invalidCitedIndices` 算。 */
  readonly invalidIndices: readonly number[];
  /** 流还开着：显示光标与占位文案。 */
  readonly streaming: boolean;
  /** 首字未到时的占位文案（"正在检索资料…" / "正在生成回答…"）。 */
  readonly placeholder: string;
  /** 当前高亮的序号；`null` 表示没有选中项。 */
  readonly activeCitation: number | null;
  /** 断流：保留半截内容，并明确告诉用户这不是完整回答。 */
  readonly incomplete: boolean;
  readonly onPickCitation: (index: number) => void;
  readonly className?: string;
}

export function StreamingAnswer({
  answer,
  citationCount,
  invalidIndices,
  streaming,
  placeholder,
  activeCitation,
  incomplete,
  onPickCitation,
  className,
}: StreamingAnswerProps): React.ReactElement {
  const markerRefs = React.useRef(new Map<number, HTMLButtonElement>());
  const invalidSet = React.useMemo(() => new Set(invalidIndices), [invalidIndices]);
  const segments = React.useMemo(() => segmentAnswer(answer), [answer]);

  React.useEffect(() => {
    if (activeCitation === null) {
      return;
    }
    const node = markerRefs.current.get(activeCitation);
    // 越界的标记在当前半截文本里可能还没出现——静默跳过，等它出现。
    node?.focus();
    // 只在"选中项变化"时移动焦点：若把 answer 也算进依赖，流式输出期间每个 token
    // 都会把焦点抢回标记上，正在输入的用户会突然失去输入焦点。
  }, [activeCitation]);

  return (
    <div className={cn("space-y-2", className)}>
      {answer === "" && streaming ? (
        <p className="flex items-center gap-2 text-[13px] leading-relaxed text-fg-muted" role="status">
          <span>{placeholder}</span>
          <Cursor />
        </p>
      ) : null}

      <div
        className="max-w-[65ch] whitespace-pre-wrap break-words text-[13px] leading-relaxed text-fg"
        aria-busy={streaming || undefined}
        aria-live={streaming ? "polite" : undefined}
        data-testid="answer-body"
        data-citation-count={citationCount}
      >
        {segments.map((segment, offset) => {
          if (segment.kind === "text") {
            return <React.Fragment key={`text-${String(offset)}`}>{segment.text}</React.Fragment>;
          }
          const invalid = invalidSet.has(segment.index);
          const active = activeCitation === segment.index;
          return (
            <button
              key={`cite-${String(segment.index)}-${String(offset)}`}
              type="button"
              ref={(node) => {
                if (node === null) {
                  markerRefs.current.delete(segment.index);
                } else {
                  markerRefs.current.set(segment.index, node);
                }
              }}
              onClick={() => {
                onPickCitation(segment.index);
              }}
              aria-pressed={active}
              title={citationTitle(segment.index, invalid)}
              data-citation-index={segment.index}
              data-active={active || undefined}
              data-invalid={invalid || undefined}
              className={cn(
                "mx-0.5 inline rounded-sm px-1 align-baseline font-mono text-[11px] leading-snug tabular",
                "transition-colors duration-base ease-out",
                "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary-ring",
                invalid
                  ? "bg-danger-weak text-danger"
                  : active
                    ? "bg-primary text-primary-fg"
                    : "bg-primary-weak text-primary hover:bg-primary-hover/20",
              )}
            >
              {segment.raw}
            </button>
          );
        })}
        {streaming && answer !== "" ? <Cursor /> : null}
      </div>

      {incomplete ? (
        <p
          role="status"
          className="max-w-[65ch] rounded-lg border border-warning bg-warning-weak px-3 py-2 text-[13px] leading-relaxed text-fg"
        >
          <span className="font-semibold text-warning">回答不完整（连接中断）</span>
          <span className="ml-1 text-fg-muted">以上内容在收到结束帧之前就中断了，可能缺少结论；请重试本轮提问。</span>
        </p>
      ) : null}
    </div>
  );
}

function Cursor(): React.ReactElement {
  return (
    <span
      aria-hidden="true"
      data-testid="stream-cursor"
      className="ml-0.5 inline-block h-[1em] w-[2px] translate-y-[0.15em] animate-pulse bg-primary"
    />
  );
}

function citationTitle(index: number, invalid: boolean): string {
  if (invalid) {
    return `[资料 ${String(index)}] 该引用未被系统校验通过：本次检索没有返回第 ${String(index)} 条资料`;
  }
  return `查看第 ${String(index)} 条证据`;
}
