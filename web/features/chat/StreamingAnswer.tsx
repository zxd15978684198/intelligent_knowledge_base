"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { AnswerMarkup, stripDanglingMarkup } from "./answer-markup";
import { useTypewriter } from "./use-typewriter";

/**
 * 渲染回答正文。
 *
 * 四条取舍（第 1 条是 2026-11-09 按用户要求改的，原文写的是"不做假打字机"）：
 *
 * 1. **做匀速放出（打字机）**。改成这样的原因是实测：一个 602 字的回答首 token
 *    要 4.8s，之后 2.1s 内全部到达（≈287 字/秒，单帧最多 163 字），屏幕上看起来
 *    就是"空白 5 秒然后瞬间出现"。现在把收到的字先缓冲、再按约 45 字/秒放出，
 *    流结束立即补齐；**只减速、不增删**，收到的字符与顺序一个都不改。代价是正文
 *    会比服务端慢一两秒，这是为了让"正在生成"这件事可见而付的。
 * 2. **Markdown 轻量渲染**（粗体/行内代码/标题/列表）。模型确实在用 `**` 和 `##`
 *    排版，此前按纯文本渲染导致标记符原样显示，见 `answer-markup.tsx`。
 * 3. **`[资料 N]` 是按钮，不是纯文本**。正文里的标记与右侧证据栏是同一份状态的
 *    两个视图，点正文能定位证据、点证据能定位正文；用 `<button>` 还顺带拿到了
 *    键盘可达与读屏语义（纯 `<span onClick>` 两者都没有）。
 * 4. **高亮靠 ref + native focus 定位**。父组件把"当前选中的序号"传下来，这里用
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
  // 收到的字先缓冲，再匀速放出；流一结束（streaming=false）立刻补齐。
  const revealed = useTypewriter(answer, { enabled: streaming });
  const display = React.useMemo(
    () => stripDanglingMarkup(revealed, streaming),
    [revealed, streaming],
  );

  const registerMarker = React.useCallback((index: number, node: HTMLButtonElement | null) => {
    if (node === null) {
      markerRefs.current.delete(index);
    } else {
      markerRefs.current.set(index, node);
    }
  }, []);

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
        className="max-w-[65ch] break-words text-[13px] leading-relaxed text-fg"
        aria-busy={streaming || undefined}
        aria-live={streaming ? "polite" : undefined}
        data-testid="answer-body"
        data-citation-count={citationCount}
      >
        <AnswerMarkup
          text={display}
          invalidIndices={invalidSet}
          activeCitation={activeCitation}
          onPickCitation={onPickCitation}
          onRegisterMarker={registerMarker}
        />
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
