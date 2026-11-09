"use client";

import * as React from "react";

import {
  Button,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui";
import type { RetrievedChunk } from "@/lib/contracts";
import { cn } from "@/lib/utils";

import {
  chunkFacts,
  formatHeadingPath,
  formatPageRange,
  formatRank,
  formatScore,
  sortChunksByScore,
  summarizeContent,
} from "./format";

/**
 * 检索结果表：把「融合分数」与「两路原始排名」摆在一起。
 *
 * 这张表的用途是调检索，所以：
 * - 按 `rrf_score` 降序（这是后端的融合顺序；前端再排一次只为防止接口顺序变化）；
 * - 单路排名用 `null` 显示 `—`，因为「这一路没召回」和「排名第 0 位」是两件事，
 *   写成 `0` 会让人以为它排在最前面；
 * - 分数用等宽数字（`.tabular`），否则位数一变就左右跳。
 *
 * **没有做 `char_start–char_end` 高亮**：检索响应里根本没有这两个字段（只有切片列表
 * 接口 `GET /documents/{id}/chunks` 有）。任务书里"在摘要上标注偏移量"的退路也走不通
 * ——响应里没有任何偏移量可标。与其编一个看起来合理的数字，不如把真实定位字段列出来
 * （`chunk_id` / `chunk_ordinal` / `locator_ref` / `index_version`），需要偏移就去切片接口取。
 */
export interface RetrievalTableProps {
  results: readonly RetrievedChunk[];
  className?: string;
}

const COLUMN_COUNT = 7;

export function RetrievalTable({ results, className }: RetrievalTableProps) {
  const idPrefix = React.useId();
  const [expandedIds, setExpandedIds] = React.useState<ReadonlySet<string>>(() => new Set());
  const ordered = React.useMemo(() => sortChunksByScore(results), [results]);

  const toggle = React.useCallback((chunkId: string) => {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(chunkId)) {
        next.delete(chunkId);
      } else {
        next.add(chunkId);
      }
      return next;
    });
  }, []);

  return (
    <Table aria-label="检索结果" className={cn("min-w-[48rem]", className)}>
      <TableHeader>
        <TableRow>
          <TableHead className="w-12 text-right tabular">#</TableHead>
          <TableHead className="w-28 text-right">融合分</TableHead>
          <TableHead className="w-24 text-right">向量排名</TableHead>
          <TableHead className="w-24 text-right">关键词排名</TableHead>
          <TableHead className="w-28">页码</TableHead>
          <TableHead className="w-48">标题路径</TableHead>
          <TableHead>内容</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {ordered.map((chunk, index) => {
          const expanded = expandedIds.has(chunk.chunk_id);
          const detailId = `${idPrefix}-detail-${index}`;
          const heading = formatHeadingPath(chunk.heading_path);
          const summary = summarizeContent(chunk.content);

          return (
            <React.Fragment key={chunk.chunk_id}>
              <TableRow data-testid="retrieval-row">
                <TableCell className="text-right tabular text-fg-muted">{index + 1}</TableCell>
                <TableCell className="text-right">
                  <span className="tabular font-medium">{formatScore(chunk.rrf_score)}</span>
                </TableCell>
                <TableCell className="text-right">
                  <span
                    className={cn(
                      "tabular",
                      chunk.vector_rank === null || chunk.vector_rank === undefined
                        ? "text-fg-subtle"
                        : "text-fg",
                    )}
                    title={
                      chunk.vector_rank === null || chunk.vector_rank === undefined
                        ? "向量通道未召回这一条"
                        : `向量通道排名第 ${chunk.vector_rank}`
                    }
                  >
                    {formatRank(chunk.vector_rank)}
                  </span>
                </TableCell>
                <TableCell className="text-right">
                  <span
                    className={cn(
                      "tabular",
                      chunk.keyword_rank === null || chunk.keyword_rank === undefined
                        ? "text-fg-subtle"
                        : "text-fg",
                    )}
                    title={
                      chunk.keyword_rank === null || chunk.keyword_rank === undefined
                        ? "关键词通道未召回这一条"
                        : `关键词通道排名第 ${chunk.keyword_rank}`
                    }
                  >
                    {formatRank(chunk.keyword_rank)}
                  </span>
                </TableCell>
                <TableCell className="whitespace-nowrap text-fg-muted">
                  {formatPageRange(chunk.page_no, chunk.page_end_no)}
                </TableCell>
                <TableCell>
                  <span className="line-clamp-2 block max-w-56 break-words" title={heading}>
                    {heading}
                  </span>
                </TableCell>
                <TableCell>
                  <div className="flex flex-col items-start gap-1">
                    <p className="max-w-xl break-words text-fg-muted">{summary}</p>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-expanded={expanded}
                      aria-controls={detailId}
                      onClick={() => toggle(chunk.chunk_id)}
                    >
                      {expanded ? "收起全文" : "展开全文"}
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
              {expanded ? (
                <TableRow className="bg-surface-muted hover:bg-surface-muted">
                  <TableCell colSpan={COLUMN_COUNT} className="px-4 py-3">
                    <div id={detailId} className="flex flex-col gap-3">
                      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-surface p-3 font-sans text-[13px] leading-relaxed text-fg">
                        {chunk.content}
                      </pre>
                      <div className="flex flex-col gap-1">
                        <p className="text-[12px] font-semibold text-fg-muted">定位信息</p>
                        <p className="text-[12px] leading-relaxed text-fg-subtle">
                          检索响应不返回字符偏移（char_start/char_end），因此这里只展示原文与真实定位字段，
                          不做高亮。需要偏移请到切片列表接口按 chunk_id 查询。
                        </p>
                        <dl className="mt-1 grid grid-cols-1 gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
                          {chunkFacts(chunk).map((fact) => (
                            <div key={fact.label} className="flex min-w-0 gap-2">
                              <dt className="shrink-0 font-mono text-fg-subtle">{fact.label}</dt>
                              <dd className="min-w-0 truncate font-mono text-fg" title={fact.value}>
                                {fact.value}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      </div>
                    </div>
                  </TableCell>
                </TableRow>
              ) : null}
            </React.Fragment>
          );
        })}
      </TableBody>
    </Table>
  );
}
