"use client";

/**
 * 文档列表：offset 分页 + 全量 `total` + 本地文件名过滤 + 行内删除确认。
 *
 * 三个和直觉不一样、写错就会骗人的点：
 *
 * 1. **`total` 是全量计数**（`doc_repo.count_for_kb`，`documents.py:259`），所以分页器可以
 *    直接用它算页数；切片接口的 `total` 是页内条数，两者不能互相套用。
 * 2. **列表接口没有"状态"字段**，也没有 job_id。状态列只能按**活动指针**如实推导
 *    （无版本 / 待发布 / 已发布），并在表脚注明"待发布"可能是正在摄取、也可能是摄取失败
 *    ——列表接口不提供作业状态，硬猜就是编。
 * 3. **搜索是本地过滤**，只作用于当前页的 20 行；它不能替代服务端检索（后端也没有
 *    按文件名过滤的查询参数）。界面必须把这件事写出来，否则用户会以为搜的是全库。
 *
 * 键形状由 `@/lib/api/hooks` 的 `qk` 规定：第二段必须是 workspaceId。
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";

import {
  Button,
  EmptyState,
  Input,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { cn, formatDateTime, shortId } from "@/lib/utils";

import { describeApiError, formatErrorLabel } from "../jobs/errors";
import { JobPoller } from "../jobs/JobPoller";
import { deleteDocument, DOCUMENTS_PAGE_SIZE, listDocuments } from "./api";
import { documentKeys } from "./queryKeys";
import type { DocumentSummary } from "./types";

/** 状态列：只按活动指针推导，不做任何超出数据的推断。 */
function pointerState(document: DocumentSummary): { label: string; className: string } {
  if ((document.active_document_version_id ?? null) === null) {
    return { label: "无版本", className: "text-fg-muted" };
  }
  if ((document.active_build_id ?? null) === null) {
    return { label: "待发布", className: "text-warning" };
  }
  return { label: "已发布", className: "text-success" };
}

function displayName(document: DocumentSummary): string {
  return document.filename ?? shortId(document.document_id);
}

export interface DocumentTableProps {
  kbId: string;
  /** 缓存键第二段（`qk` 的硬要求）。 */
  workspaceId: string;
  className?: string;
}

export function DocumentTable({ kbId, workspaceId, className }: DocumentTableProps) {
  const queryClient = useQueryClient();
  const limit = DOCUMENTS_PAGE_SIZE;

  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [purge, setPurge] = useState<{ documentId: string; jobId: string | null } | null>(null);

  const query = useQuery({
    queryKey: documentKeys.list(workspaceId, kbId, { limit, offset }),
    queryFn: () => listDocuments({ kbId, workspaceId, limit, offset }),
    // 翻页时保留上一页数据，避免表格闪成空白（不是缓存伪造，只是过渡显示）。
    placeholderData: keepPreviousData,
  });

  const remove = useMutation({
    mutationFn: (documentId: string) => deleteDocument(documentId, { workspaceId }),
    onSuccess: (accepted, documentId) => {
      setPendingDeleteId(null);
      setPurge({ documentId, jobId: accepted.job_id ?? null });
      void queryClient.invalidateQueries({ queryKey: documentKeys.listScope(workspaceId, kbId) });
      void queryClient.invalidateQueries({ queryKey: documentKeys.detail(workspaceId, documentId) });
      // 删掉的是本页最后一行且不在第一页：退回上一页，别让用户停在一个空页上。
      const remaining = (query.data?.items.length ?? 0) - 1;
      if (remaining <= 0 && offset > 0) {
        setOffset(Math.max(0, offset - limit));
      }
    },
  });

  const items = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const trimmedSearch = search.trim().toLowerCase();
  const visible = useMemo(
    () =>
      trimmedSearch === ""
        ? items
        : items.filter((document) => displayName(document).toLowerCase().includes(trimmedSearch)),
    [items, trimmedSearch],
  );

  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = offset + items.length;
  const hasPrevious = offset > 0;
  const hasNext = offset + limit < total;

  return (
    <section
      className={cn("rounded-lg border border-border bg-surface p-4", className)}
      aria-labelledby="document-table-title"
    >
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="document-table-title" className="text-sm font-medium text-fg">
            文档列表
          </h2>
          <p className="mt-1 text-xs text-fg-muted">
            共 <span className="tabular">{total}</span> 篇（服务端全量计数）。
          </p>
        </div>
        <div className="flex w-full max-w-xs flex-col gap-1">
          <label htmlFor="document-search" className="text-xs text-fg-muted">
            按文件名过滤（本地过滤：只筛当前页，不是服务端搜索）
          </label>
          <Input
            id="document-search"
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="输入文件名片段"
            className="h-9 w-full"
          />
        </div>
      </div>

      {query.isError ? (
        <div
          role="alert"
          className="mt-3 rounded-md border border-danger bg-danger-weak px-3 py-2 text-sm text-danger"
        >
          <p>文档列表读取失败：{formatErrorLabel(describeApiError(query.error))}</p>
          <Button type="button" variant="secondary" size="sm" className="mt-2 w-fit" onClick={() => void query.refetch()}>
            重试
          </Button>
        </div>
      ) : null}

      {query.isPending ? (
        <p className="mt-3 text-sm text-fg-muted" aria-busy="true">
          正在加载文档…
        </p>
      ) : null}

      {query.isSuccess && total === 0 ? (
        <EmptyState
          className="mt-3"
          title="这个知识库还没有文档"
          description="用上面的面板上传第一篇；支持 PDF / DOCX / TXT / Markdown / HTML，单个文件不超过 50 MiB。"
        />
      ) : null}

      {query.isSuccess && total > 0 ? (
        <>
          <Table className="mt-3 w-full text-sm">
            <TableHeader>
              <TableRow>
                <TableHead className="px-2 py-2 text-left text-xs font-medium text-fg-muted">文件名</TableHead>
                <TableHead className="px-2 py-2 text-left text-xs font-medium text-fg-muted">状态</TableHead>
                <TableHead className="px-2 py-2 text-left text-xs font-medium text-fg-muted">可检索</TableHead>
                <TableHead className="px-2 py-2 text-left text-xs font-medium text-fg-muted">创建时间</TableHead>
                <TableHead className="px-2 py-2 text-right text-xs font-medium text-fg-muted">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="px-2 py-4 text-sm text-fg-muted">
                    当前页没有文件名匹配「{search.trim()}」的文档。
                  </TableCell>
                </TableRow>
              ) : null}
              {visible.map((document) => {
                const state = pointerState(document);
                const confirming = pendingDeleteId === document.document_id;
                const detailHref = `/kbs/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(document.document_id)}`;
                return (
                  <TableRow key={document.document_id} className="border-t border-border">
                    <TableCell className="px-2 py-2 align-top">
                      <Link className="text-fg underline-offset-2 hover:underline" href={detailHref}>
                        {displayName(document)}
                      </Link>
                      {document.filename === null ? (
                        <span className="ml-1 text-xs text-fg-subtle">（无文件名，显示文档 id 前 8 位）</span>
                      ) : null}
                    </TableCell>
                    <TableCell className={cn("px-2 py-2 align-top", state.className)}>
                      {state.label}
                      {(document.active_index_version ?? null) !== null ? (
                        <span className="tabular ml-1 text-xs text-fg-muted">
                          v{document.active_index_version}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell className="px-2 py-2 align-top">
                      {document.searchable ? "是" : <span className="text-fg-muted">否</span>}
                    </TableCell>
                    <TableCell className="tabular px-2 py-2 align-top text-fg-muted">
                      {formatDateTime(document.created_at)}
                    </TableCell>
                    <TableCell className="px-2 py-2 align-top text-right">
                      {confirming ? (
                        <span className="inline-flex flex-wrap items-center justify-end gap-2">
                          <span className="text-xs text-danger">确认删除？</span>
                          <Button
                            type="button"
                            variant="danger"
                            size="sm"
                            className="w-fit"
                            onClick={() => remove.mutate(document.document_id)}
                            loading={remove.isPending}
                          >
                            {remove.isPending ? "删除中…" : "确认"}
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="w-fit text-xs"
                            onClick={() => setPendingDeleteId(null)}
                            disabled={remove.isPending}
                          >
                            取消
                          </Button>
                        </span>
                      ) : (
                        <span className="inline-flex flex-wrap items-center justify-end gap-2">
                          <Link className="text-xs text-fg underline-offset-2 hover:underline" href={detailHref}>
                            查看
                          </Link>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="w-fit text-xs text-danger"
                            onClick={() => setPendingDeleteId(document.document_id)}
                          >
                            删除
                          </Button>
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

          <p className="mt-2 text-xs text-fg-subtle">
            「状态」按活动指针推导：无版本（还没有任何版本）/ 待发布（有版本但没有活动构建，可能正在摄取，也可能摄取失败）/
            已发布（存在活动构建）。列表接口不返回作业状态，具体原因看文档详情。切片数在列表接口里没有，故不显示。
          </p>

          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <p className="tabular text-xs text-fg-muted">
              第 {rangeStart}–{rangeEnd} 篇 / 共 {total} 篇
              {trimmedSearch === "" ? null : <> · 当前页本地过滤后 {visible.length} 篇</>}
            </p>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="w-fit"
                onClick={() => setOffset(Math.max(0, offset - limit))}
                disabled={!hasPrevious}
              >
                上一页
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="w-fit"
                onClick={() => setOffset(offset + limit)}
                disabled={!hasNext}
              >
                下一页
              </Button>
            </div>
          </div>
        </>
      ) : null}

      {remove.isError ? (
        <p
          role="alert"
          className="mt-3 rounded-md border border-danger bg-danger-weak px-3 py-2 text-sm text-danger"
        >
          删除失败：{formatErrorLabel(describeApiError(remove.error))}
        </p>
      ) : null}

      {purge !== null ? (
        <div className="mt-3 rounded-md border border-border bg-surface-muted px-3 py-2">
          <p className="text-sm text-fg">
            已提交删除（202）：文档 <span className="font-mono">{shortId(purge.documentId)}</span>{" "}
            已从列表移除，清理是异步的。
          </p>
          {purge.jobId !== null ? (
            <JobPoller jobId={purge.jobId} workspaceId={workspaceId} showCancel={false} className="mt-2" />
          ) : (
            <p className="mt-1 text-xs text-fg-muted">服务端没有返回清理作业 id，无法跟踪清理进度。</p>
          )}
        </div>
      ) : null}
    </section>
  );
}
