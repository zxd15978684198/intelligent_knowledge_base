"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { segmentAnswer } from "./citations";

/**
 * 回答正文的渲染：把 Markdown 的"轻量版"渲染成元素，同时保住 `[资料 N]` 按钮。
 *
 * 为什么必须渲染：模型输出的正文里带 `**小标题**`、`## 一、适用条件`、`- 条目`、
 * `1. 步骤`。此前这里是 `<pre>` 式的纯文本（`whitespace-pre-wrap` + 直接塞字符串），
 * 于是 `**` 和 `##` 原样显示、该粗的地方不粗 —— 模型写对了，界面没读。
 *
 * 为什么是"轻量版"而不是引 markdown 库：
 * 1. 依赖只加不能用（`web/package.json` 这份清单是评审过的）；
 * 2. 正文里混着 `[资料 N]` 标记，必须由我们把标记换成 `<button>`，任何第三方
 *    渲染器都要再写一层 AST 遍历，代码量反而更多；
 * 3. 只支持实测出现的语法（粗体、行内代码、1–6 级标题、无序/有序列表），
 *    未识别的语法原样显示 —— 宁可不渲染，也不能把用户该看到的内容吃掉。
 *
 * 与纯文本方案的等价性：`textContent` 里除了 `**`/`#`/`-` 这些标记符之外，
 * 文字顺序与内容完全不变（`web/tests/unit/**` 按 textContent 断言的用例不受影响）。
 */

export interface AnswerMarkupProps {
  /** 已经按打字机节奏放出、可以显示的那一段正文。 */
  readonly text: string;
  /** 越界引用的序号（标红）。 */
  readonly invalidIndices: ReadonlySet<number>;
  /** 当前高亮的序号。 */
  readonly activeCitation: number | null;
  readonly onPickCitation: (index: number) => void;
  readonly onRegisterMarker: (index: number, node: HTMLButtonElement | null) => void;
}

type Block =
  | { readonly kind: "paragraph"; readonly lines: readonly string[] }
  | { readonly kind: "heading"; readonly level: number; readonly text: string }
  | { readonly kind: "list"; readonly ordered: boolean; readonly items: readonly string[] };

const HEADING_PATTERN = /^(#{1,6})\s+(.+?)\s*#*\s*$/u;
const BULLET_PATTERN = /^\s*[-*+]\s+(.+)$/u;
const ORDERED_PATTERN = /^\s*\d+[.)]\s+(.+)$/u;

/** 把正文切成块：段落（保留内部换行）、标题、列表。 */
export function parseAnswerBlocks(text: string): readonly Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = (): void => {
    if (paragraph.length > 0) {
      blocks.push({ kind: "paragraph", lines: paragraph });
      paragraph = [];
    }
  };
  const flushList = (): void => {
    if (list !== null) {
      blocks.push({ kind: "list", ordered: list.ordered, items: list.items });
      list = null;
    }
  };

  for (const rawLine of text.split("\n")) {
    const line = rawLine.replace(/\s+$/u, "");
    if (line.trim() === "") {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = HEADING_PATTERN.exec(line);
    if (heading !== null) {
      flushParagraph();
      flushList();
      blocks.push({ kind: "heading", level: heading[1]?.length ?? 2, text: heading[2] ?? "" });
      continue;
    }

    const bullet = BULLET_PATTERN.exec(line);
    if (bullet !== null) {
      flushParagraph();
      if (list === null || list.ordered) {
        flushList();
        list = { ordered: false, items: [] };
      }
      list.items.push(bullet[1] ?? "");
      continue;
    }

    const ordered = ORDERED_PATTERN.exec(line);
    if (ordered !== null) {
      flushParagraph();
      if (list === null || !list.ordered) {
        flushList();
        list = { ordered: true, items: [] };
      }
      list.items.push(ordered[1] ?? "");
      continue;
    }

    flushList();
    paragraph.push(line);
  }

  flushParagraph();
  flushList();
  return blocks;
}

type InlinePart =
  | { readonly kind: "text"; readonly text: string }
  | { readonly kind: "strong"; readonly text: string }
  | { readonly kind: "code"; readonly text: string };

const INLINE_PATTERN = /\*\*([^*]+?)\*\*|`([^`]+?)`/gu;

/** 行内语法：`**粗体**`、`` `代码` ``。其余原样保留。 */
export function splitInline(text: string): readonly InlinePart[] {
  const parts: InlinePart[] = [];
  let cursor = 0;
  for (const match of text.matchAll(INLINE_PATTERN)) {
    const start = match.index;
    const raw = match[0];
    if (raw === undefined) {
      continue;
    }
    if (start > cursor) {
      parts.push({ kind: "text", text: text.slice(cursor, start) });
    }
    const strong = match[1];
    const code = match[2];
    if (strong !== undefined) {
      parts.push({ kind: "strong", text: strong });
    } else if (code !== undefined) {
      parts.push({ kind: "code", text: code });
    }
    cursor = start + raw.length;
  }
  if (cursor < text.length) {
    parts.push({ kind: "text", text: text.slice(cursor) });
  }
  return parts;
}

/**
 * 流式中把未闭合的 `**` / 反引号先藏起来。
 *
 * 否则用户会看到一瞬间的 `**办理探亲假` 再突然变粗；模型按句子成帧，这个空窗
 * 有几百毫秒，看起来像乱码。只在流未结束时生效——结束时即使真没闭合也原样显示，
 * 那是模型的问题，不该由界面掩盖。
 */
export function stripDanglingMarkup(text: string, streaming: boolean): string {
  if (!streaming) {
    return text;
  }
  let result = text;
  const doubleStars = (result.match(/\*\*/gu) ?? []).length;
  if (doubleStars % 2 === 1) {
    result = result.slice(0, result.lastIndexOf("**"));
  }
  const ticks = (result.match(/`/gu) ?? []).length;
  if (ticks % 2 === 1) {
    result = result.slice(0, result.lastIndexOf("`"));
  }
  return result;
}

export function AnswerMarkup({
  text,
  invalidIndices,
  activeCitation,
  onPickCitation,
  onRegisterMarker,
}: AnswerMarkupProps): React.ReactElement {
  const blocks = React.useMemo(() => parseAnswerBlocks(text), [text]);

  const renderText = (run: string, keyPrefix: string): React.ReactNode[] =>
    splitInline(run).map((part, offset) => {
      const key = `${keyPrefix}-inline-${String(offset)}`;
      if (part.kind === "strong") {
        return (
          <strong key={key} className="font-semibold text-fg">
            {part.text}
          </strong>
        );
      }
      if (part.kind === "code") {
        return (
          <code
            key={key}
            className="rounded-sm bg-surface-muted px-1 py-0.5 font-mono text-[12px] text-fg"
          >
            {part.text}
          </code>
        );
      }
      return <React.Fragment key={key}>{part.text}</React.Fragment>;
    });

  const renderRich = (run: string, keyPrefix: string): React.ReactNode[] =>
    segmentAnswer(run).flatMap((segment, offset) => {
      if (segment.kind === "text") {
        return renderText(segment.text, `${keyPrefix}-seg-${String(offset)}`);
      }
      const invalid = invalidIndices.has(segment.index);
      const active = activeCitation === segment.index;
      return [
        <button
          key={`${keyPrefix}-cite-${String(segment.index)}-${String(offset)}`}
          type="button"
          ref={(node) => {
            onRegisterMarker(segment.index, node);
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
        </button>,
      ];
    });

  return (
    <div className="space-y-1.5">
      {blocks.map((block, index) => {
        const key = `block-${String(index)}`;
        if (block.kind === "heading") {
          const size = block.level <= 2 ? "text-[15px]" : "text-[13.5px]";
          return (
            <p key={key} className={cn("mt-2 mb-0.5 font-semibold text-fg first:mt-0", size)}>
              {renderRich(block.text, key)}
            </p>
          );
        }
        if (block.kind === "list") {
          const items = block.items.map((item, offset) => (
            <li key={`${key}-item-${String(offset)}`} className="whitespace-pre-wrap">
              {renderRich(item, `${key}-item-${String(offset)}`)}
            </li>
          ));
          return block.ordered ? (
            <ol key={key} className="my-1 list-decimal space-y-0.5 pl-5">
              {items}
            </ol>
          ) : (
            <ul key={key} className="my-1 list-disc space-y-0.5 pl-5">
              {items}
            </ul>
          );
        }
        return (
          <p key={key} className="whitespace-pre-wrap">
            {renderRich(block.lines.join("\n"), key)}
          </p>
        );
      })}
    </div>
  );
}

function citationTitle(index: number, invalid: boolean): string {
  if (invalid) {
    return `[资料 ${String(index)}] 该引用未被系统校验通过：本次检索没有返回第 ${String(index)} 条资料`;
  }
  return `查看第 ${String(index)} 条证据`;
}
