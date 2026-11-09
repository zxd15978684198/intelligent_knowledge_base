"use client";

/**
 * 切片列表：不引虚拟化依赖，直接"加载更多"。
 *
 * 为什么只能"加载更多"、不能做分页器：`GET /documents/{id}/chunks` 的
 * `total` 是**页内条数**（`documents.py:324 total=len(chunks)`），不是全量总数。
 * 拿它算页数会算出 1 页；想显示"共 N 条"只能等后端改口径。
 *
 * "还有下一页"的唯一可用判据：**本页是否满 limit**。满页 ⇒ 可能还有；不满 ⇒ 到底了。
 * 这个判据在"恰好 limit 条"时会多给一次空请求，代价是一次 200 空数组，可接受；
 * 反过来（用 total 判断）会直接把"加载更多"永远藏起来。
 *
 * 缓存键：`qk.chunks` 要求完整的 `{limit, offset}`，而 infinite query 的 offset 由
 * `pageParam` 管，所以键里固定 `offset: 0`（键只标识"这是哪个文档的切片流"）。
 *
 * 每条的元信息按后端真实字段展示，缺值就如实说"无"，不拼假页码：
 * `chunk_ordinal`、`page_no`–`page_end_no`、`char_start`–`char_end`、`content`。
 */

import { useInfiniteQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

import { describeApiError, formatErrorLabel } from "../jobs/errors";
import { CHUNKS_PAGE_SIZE, listChunks } from "./api";
import { documentKeys } from "./queryKeys";
import type { ChunkItem } from "./types";

function pageLabel(chunk: ChunkItem): string {
  const start = chunk.page_no ?? null;
  const end = chunk.page_end_no ?? null;
  if (start === null && end === null) return "页码：无";
  if (start !== null && end !== null) {
    return start === end ? `第 ${start} 页` : `第 ${start}–${end} 页`;
  }
  // 只有一端：如实标出缺哪端，不拿另一端顶替。
  return start !== null ? `第 ${start} 页（结束页未知）` : `结束页 ${end ?? "?"}（起始页未知）`;
}

function charLabel(chunk: ChunkItem): string {
  const start = chunk.char_start ?? null;
  const end = chunk.char_end ?? null;
  if (start === null && end === null) return "字符区间：无";
  if (start !== null && end !== null) return `字符 ${start}–${end}`;
  return "字符区间不完整";
}

export interface ChunkListProps {
  documentId: string;
  /** 缓存键第二段（`qk` 的硬要求）。 */
  workspaceId: string;
  className?: string;
}

export function ChunkList({ documentId, workspaceId, className }: ChunkListProps) {
  const limit = CHUNKS_PAGE_SIZE;

  const query = useInfiniteQuery({
    // offset 由 pageParam 管，键里固定 0（见文件头注释）。
    queryKey: documentKeys.chunkList(workspaceId, documentId, { limit, offset: 0 }),
    queryFn: ({ pageParam }) => listChunks({ documentId, workspaceId, limit, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, _allPages, lastPageParam) =>
      lastPage.items.length >= lastPage.limit ? lastPageParam + lastPage.limit : undefined,
  });

  const chunks = query.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <section className={cn("flex flex-col gap-3", className)} aria-labelledby="chunk-list-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="chunk-list-title" className="text-sm font-medium text-fg">
          切片
        </h3>
        <p className="tabular text-xs text-fg-muted" role="status">
          {query.isPending ? "正在加载…" : `已加载 ${chunks.length} 条`}
        </p>
      </div>

      <p className="max-w-[65ch] text-xs leading-relaxed text-fg-subtle">
        切片接口的 <code className="font-mono">total</code> 是页内条数而不是总数，所以这里不显示总条数、
        也没有分页器，只能用「加载更多」一页页取。
      </p>

      {query.isError ? (
        <div
          role="alert"
          className="rounded-md border border-danger bg-danger-weak px-3 py-2 text-sm text-danger"
        >
          <p className="max-w-[65ch] leading-relaxed">我们没能读取切片：{formatErrorLabel(describeApiError(query.error))}</p>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="mt-2 w-fit border-danger text-danger"
            onClick={() => void query.refetch()}
          >
            重试
          </Button>
        </div>
      ) : null}

      {query.isSuccess && chunks.length === 0 ? (
        <p className="max-w-[65ch] text-sm leading-relaxed text-fg-muted">
          这篇文档还没有可检索的切片，可能尚未发布（没有活动构建），或解析出的正文为空。
          下一步：切到「版本链」确认已有活动版本，或回到文档列表看摄取作业是否完成；发布成功后切片会出现在这里。
        </p>
      ) : null}

      {chunks.length > 0 ? (
        <ol className="flex flex-col gap-2">
          {chunks.map((chunk) => (
            <li key={chunk.chunk_id} className="rounded-md border border-border/60 bg-surface px-3 py-2 shadow-sm transition-[box-shadow,transform] duration-fast ease-out hover:shadow-md">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted">
                <span className="tabular font-medium text-fg">#{chunk.chunk_ordinal}</span>
                <span>{pageLabel(chunk)}</span>
                <span className="tabular">{charLabel(chunk)}</span>
                {(chunk.heading_path ?? null) !== null ? <span>标题：{chunk.heading_path}</span> : null}
                {(chunk.token_count ?? null) !== null ? (
                  <span className="tabular">{chunk.token_count} tokens</span>
                ) : null}
                <span className="tabular text-fg-subtle">索引 v{chunk.index_version}</span>
              </div>
              <p className="mt-1.5 max-w-[65ch] whitespace-pre-wrap text-sm leading-relaxed text-fg">{chunk.content}</p>
            </li>
          ))}
        </ol>
      ) : null}

      {chunks.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          {query.hasNextPage ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="w-fit"
              onClick={() => void query.fetchNextPage()}
              loading={query.isFetchingNextPage}
            >
              {query.isFetchingNextPage ? "加载中…" : "加载更多"}
            </Button>
          ) : (
            <p className="max-w-[65ch] text-xs leading-relaxed text-fg-muted">没有更多切片了（最后一页未满 {limit} 条）。</p>
          )}
          {query.isFetchNextPageError ? (
            <p role="alert" className="max-w-[65ch] text-xs leading-relaxed text-danger">
              我们没能加载下一页：{formatErrorLabel(describeApiError(query.error))}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
