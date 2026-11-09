"use client";

import * as React from "react";

import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status-badge";

import { buildCitationRows, formatPageRange } from "./citations";
import type { ChatCitation } from "./events";

/**
 * 证据侧栏（或窄屏下的"证据"页签）。
 *
 * 两件事必须做对：
 * 1. **点击即高亮正文里的 `[资料 N]`**——父组件持有"当前选中序号"，本组件只负责
 *    把点击换成回调，正文那边用 ref 定位并 focus，不做滚动条手算。
 * 2. **越界引用要显形**。后端只发"有 N 条引用未通过校验"这个计数，正文里写了
 *    `[资料 7]` 而实际只有 3 条资料时，这一行必须作为红色幽灵行出现在列表里，
 *    并说清"该引用未被系统校验通过"——否则用户点不到它，只会以为界面坏了。
 *
 * 还要区分"检索到的资料"和"被正文用到的资料"：没被引用的资料只是候选证据，
 * 灰一点，不要让它看起来像论据。
 *
 * 视觉层级（§1.3 / C1）：外壳统一用 `<Card>`（自带 border/bg/shadow-sm），不再叠一层
 * 白底 + 1px 边框；序号 / 页码走 tabular 数字；序号与页码在首行，snippet 是正文，
 * 来源文档与切片 ID 收在次级元信息行。
 */
export interface CitationRailProps {
  readonly citations: readonly ChatCitation[];
  /** 越界序号（1 起，前端反算）。 */
  readonly invalidIndices: readonly number[];
  /** 后端 `done.invalid_citations` 的条数；用于提示"还有落不到条目上的越界"。 */
  readonly invalidCount?: number;
  /** 正文里出现过的序号。 */
  readonly citedIndices: readonly number[];
  readonly activeIndex: number | null;
  readonly onSelect: (index: number) => void;
  /** 正在流式中：用一句话说明列表还可能变长。 */
  readonly streaming?: boolean;
  readonly className?: string;
}

export function CitationRail({
  citations,
  invalidIndices,
  invalidCount = 0,
  citedIndices,
  activeIndex,
  onSelect,
  streaming = false,
  className,
}: CitationRailProps): React.ReactElement {
  const rows = React.useMemo(
    () => buildCitationRows(citations, invalidIndices, citedIndices),
    [citations, invalidIndices, citedIndices],
  );
  const ghostRows = rows.filter((row) => row.citation === null).length;
  const unlocated = Math.max(0, invalidCount - ghostRows);

  return (
    <section className={cn("space-y-2", className)} aria-labelledby="evidence-heading">
      <header className="flex items-baseline justify-between gap-2">
        <h2 id="evidence-heading" className="text-[13px] font-semibold tracking-tight text-fg">
          证据
        </h2>
        <span className="tabular text-[11px] text-fg-muted">共 {citations.length} 条</span>
      </header>

      {streaming && citations.length === 0 ? (
        <p className="text-[13px] leading-relaxed text-fg-subtle" role="status">
          正在检索资料…
        </p>
      ) : null}

      {rows.length === 0 && !streaming ? (
        <p className="rounded-lg border border-dashed border-border px-3 py-4 text-[13px] leading-relaxed text-fg-muted">
          本次回答没有返回引用。若回答被判定为「证据不足」或检索通道降级，引用为空是预期结果；想看引用，可换个更具体的问题再问一次。
        </p>
      ) : null}

      {rows.length === 0 ? null : (
        <ol className="space-y-2">
          {rows.map((row) => {
            const active = activeIndex === row.index;
            const invalid = row.invalid;
            const citation = row.citation;
            return (
              <li key={row.index}>
                <Card
                  className={cn(
                    invalid
                      ? "border-danger bg-danger-weak"
                      : active
                        ? "border-primary bg-primary-weak shadow-md"
                        : "hover:border-border-strong hover:shadow-md",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => {
                      onSelect(row.index);
                    }}
                    aria-pressed={active}
                    aria-current={active ? "true" : undefined}
                    data-citation-index={row.index}
                    data-active={active || undefined}
                    data-invalid={invalid || undefined}
                    className={cn(
                      "w-full px-3 py-2.5 text-left",
                      "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-ring",
                      !invalid && !row.citedInAnswer ? "opacity-70" : "",
                    )}
                  >
                    <span className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
                      <span className={cn("tabular text-[12px] font-semibold", invalid ? "text-danger" : "text-primary")}>
                        [资料 {row.index}]
                      </span>
                      {citation === null ? (
                        <StatusBadge label="未通过校验" tone="danger" srLabel="该引用未被系统校验通过" />
                      ) : (
                        <span className="tabular text-[11px] font-medium text-fg-muted">
                          {formatPageRange(citation.page_no, citation.page_end_no)}
                        </span>
                      )}
                    </span>

                    {citation === null ? (
                      <span className="mt-1.5 block text-[13px] leading-relaxed text-danger">
                        该引用未被系统校验通过：本次检索没有返回第 {row.index} 条资料，请勿采信该结论。
                      </span>
                    ) : (
                      <>
                        <span className="mt-1.5 block text-[13px] leading-relaxed text-fg">
                          {truncate(citation.snippet, 160)}
                        </span>
                        <span className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-fg-subtle">
                          <span className="font-medium text-fg-muted">来源</span>
                          <span className="truncate font-mono tabular" title={citation.document_id}>
                            {citation.document_id}
                          </span>
                          <span aria-hidden="true">·</span>
                          <span className="truncate font-mono tabular" title={citation.chunk_id}>
                            {citation.chunk_id}
                          </span>
                          {row.citedInAnswer ? null : <span>· 未被正文引用</span>}
                        </span>
                      </>
                    )}
                  </button>
                </Card>
              </li>
            );
          })}
        </ol>
      )}

      {unlocated > 0 ? (
        <p className="tabular text-[11px] text-danger">
          另有 {unlocated} 处越界引用无法对应到具体条目（后端只提供了条数）。
        </p>
      ) : null}
    </section>
  );
}

/** 片段可能很长（切片上限 512 字），侧栏只显示前 160 字，完整内容留在 title 里。 */
function truncate(text: string, limit: number): string {
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, limit)}…`;
}
