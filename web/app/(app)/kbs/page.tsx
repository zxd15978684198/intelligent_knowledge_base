"use client";

import { Button, Card, CardContent, EmptyState, Skeleton } from "@/components/ui";
import { useAuth } from "@/features/auth";
import { KbCreateDialog } from "@/features/kbs/KbCreateDialog";
import { KbTable } from "@/features/kbs/KbTable";
import { describeKbError } from "@/features/kbs/errors";
import { useKbListController } from "@/features/kbs/hooks";

/**
 * 知识库列表页。
 *
 * 页面只做三件事：把控制器给的 `items` 画成表、把"空 / 加载 / 出错"三种非正常态画清楚、
 * 管住"加载更多"。410 `CURSOR_EXPIRED` 的重置逻辑在 `useKbListController` 里
 * （清空分页 → 重载第一页 → 给出提示），页面只要把 `notice` 显示出来。
 *
 * 工作区从当前身份取（壳层只展示、不注入）：取到就进 queryKey 与 `X-Workspace-ID`，
 * 取不到传 `null` 让后端回落 JWT 默认空间，不硬编码工作区 ID。
 */

const SKELETON_ROWS = ["skeleton-row-1", "skeleton-row-2", "skeleton-row-3"];

export default function KbsPage() {
  const { identity } = useAuth();
  const workspaceId = identity?.default_workspace_id ?? null;
  const list = useKbListController({ workspaceId });
  const isReloading = list.isFetching && !list.isFetchingNextPage && !list.isLoading;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="text-[28px]/[32px] font-semibold tracking-tight leading-tight text-fg">知识库</h1>
          <p className="max-w-[65ch] text-[13px] leading-relaxed text-fg-muted">
            每个知识库固化一套嵌入与切片参数。上传文档后按此建索引，检索与问答都限定在知识库内。
          </p>
          <p className="max-w-[65ch] text-[13px] leading-relaxed text-fg-muted">
            每一行右侧的「文档 / 检索 / 问答」是进入该知识库的三个入口：
            <span className="text-fg">文档</span> 里上传文件，
            <span className="text-fg">检索</span> 看切片命中，
            <span className="text-fg">问答</span> 走完整的流式回答。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={() => void list.reload()} loading={isReloading}>
            刷新
          </Button>
          <KbCreateDialog workspaceId={workspaceId} />
        </div>
      </header>

      {list.notice ? (
        <div
          role="status"
          className="flex items-start justify-between gap-3 rounded-md border border-border bg-surface-muted px-3 py-2 text-sm text-fg"
        >
          <span>{list.notice}</span>
          <Button variant="ghost" size="sm" onClick={list.clearNotice} aria-label="关闭提示">
            关闭
          </Button>
        </div>
      ) : null}

      {list.isLoading ? <KbListSkeleton /> : null}

      {!list.isLoading && list.isError ? (
        <Card>
          <CardContent className="flex flex-col gap-3">
            <p role="alert" className="text-sm leading-relaxed text-danger">
              {describeKbError(list.error, "load")}
            </p>
            <div className="flex">
              <Button variant="secondary" onClick={() => void list.reload()}>
                重试
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {!list.isLoading && !list.isError && list.items.length === 0 ? (
        <EmptyState
          title="还没有知识库"
          description="先点下面的「新建第一个知识库」把嵌入与切片参数定下来，再上传文档；之后的检索与问答都会限定在这个库里。"
          action={
            <KbCreateDialog workspaceId={workspaceId} triggerLabel="新建第一个知识库" />
          }
        />
      ) : null}

      {!list.isLoading && !list.isError && list.items.length > 0 ? (
        <section className="flex flex-col gap-3" aria-label="知识库列表">
          <KbTable
            items={list.items}
            workspaceId={workspaceId}
            containerClassName="rounded-lg border border-border/60 bg-surface shadow-sm"
          />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="tabular text-xs text-fg-muted">已加载 {list.items.length} 个知识库</p>
            {list.hasNextPage ? (
              <Button
                variant="secondary"
                onClick={() => void list.loadMore()}
                loading={list.isFetchingNextPage}
              >
                加载更多
              </Button>
            ) : (
              <p className="text-xs text-fg-muted">已全部加载</p>
            )}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function KbListSkeleton() {
  return (
    <Card aria-busy="true" aria-live="polite" className="flex flex-col gap-2 p-4">
      <span className="sr-only">正在加载知识库…</span>
      {SKELETON_ROWS.map((row) => (
        <Skeleton key={row} className="h-12 w-full" />
      ))}
    </Card>
  );
}
