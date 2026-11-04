"use client";

import * as React from "react";
import { useMutation } from "@tanstack/react-query";

import { Button, EmptyState, Skeleton, buttonVariants } from "@/components/ui";
import { cn } from "@/lib/utils";

import { DegradedBanner } from "./DegradedBanner";
import { QueryComposer } from "./QueryComposer";
import { RetrievalTable } from "./RetrievalTable";
import { StrategyBadge } from "./StrategyBadge";
import { QUERY_DEFAULT_TOP_K, runQuery, type NormalizedRunQueryInput } from "./api";
import { describeQueryError } from "./errors";
import { describeResultCount } from "./format";

/**
 * 检索调试台：左侧输入、右侧结果。
 *
 * 为什么不把逻辑塞进 `page.tsx`：页面要 `useParams()`（依赖 Next 运行时），而这里全是
 * 可测的普通 React —— 传输层（`runQuery`）、状态机（`useMutation`）、渲染分支都在这一层，
 * 用例用 MSW 打假接口就能覆盖「有结果 / 降级 / 403 / 加载中 / 空输入」五条路径。
 *
 * 空态口径：本组件不知道知识库有没有文档（那要额外拉文档列表，属于 N5 的读模型），
 * 所以文案只做引导、不断言「你的知识库是空的」——检索为空也可能是问法或索引没完成。
 */
export interface QueryConsoleProps {
  kbId: string;
  /** 省略时由后端按 JWT 里的 default_workspace_id 解析。 */
  workspaceId: string | null;
  className?: string;
}

export function QueryConsole({ kbId, workspaceId, className }: QueryConsoleProps) {
  const mutation = useMutation({
    mutationFn: (input: NormalizedRunQueryInput) => runQuery(kbId, input, workspaceId),
  });

  const documentsHref = `/kbs/${encodeURIComponent(kbId)}/documents`;
  const lastInput = mutation.variables;
  const errorView = mutation.isError ? describeQueryError(mutation.error) : null;

  return (
    <div
      className={cn(
        "grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]",
        className,
      )}
    >
      <section aria-labelledby="query-composer-heading" className="flex min-w-0 flex-col gap-3">
        <h2 id="query-composer-heading" className="text-sm font-semibold text-fg">
          检索输入
        </h2>
        <QueryComposer pending={mutation.isPending} onSubmit={(input) => mutation.mutate(input)} />
      </section>

      <section aria-labelledby="query-results-heading" className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="query-results-heading" className="text-sm font-semibold text-fg">
            检索结果
          </h2>
          {mutation.data ? <StrategyBadge strategy={mutation.data.retrieval_strategy} /> : null}
        </div>

        <div aria-busy={mutation.isPending} className="min-w-0">
          {mutation.isPending ? (
            <div className="flex flex-col gap-2" data-testid="query-loading">
              <span className="sr-only">正在检索…</span>
              {[0, 1, 2, 3].map((row) => (
                <Skeleton key={row} className="h-12 w-full" />
              ))}
            </div>
          ) : null}

          {errorView ? (
            <div
              role="alert"
              data-testid="query-error"
              className="flex flex-col gap-2 rounded-md border border-danger bg-danger-weak p-3"
            >
              <h3 className="text-sm font-semibold text-danger">{errorView.title}</h3>
              <p className="text-sm text-fg">{errorView.detail}</p>
              {errorView.retryable && lastInput ? (
                <div>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => mutation.mutate(lastInput)}
                  >
                    重试本次检索
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}

          {mutation.data ? (
            <div className="flex flex-col gap-3">
              {/* 降级要放在结果**上方**：先看到"结果不完整"，再看排名。 */}
              <DegradedBanner degraded={mutation.data.degraded} />

              <p className="text-sm text-fg-muted" data-testid="query-summary">
                问题「{mutation.data.query}」{describeResultCount(mutation.data.results.length)}
                （top_k {lastInput?.top_k ?? QUERY_DEFAULT_TOP_K}）。
              </p>

              {mutation.data.results.length === 0 ? (
                <p className="rounded-md border border-border bg-surface-muted p-3 text-sm text-fg-muted">
                  本次检索没有命中任何切片。可以换一种问法、把 top_k 调大；若知识库还没有文档或索引仍在进行，
                  请先<a className="underline" href={documentsHref}>上传文档</a>并等待索引完成。
                </p>
              ) : (
                <RetrievalTable results={mutation.data.results} />
              )}
            </div>
          ) : null}

          {!mutation.isPending && !errorView && !mutation.data ? (
            <EmptyState
              title="先上传文档，再调试检索"
              description="检索只能命中已完成索引的切片：知识库为空时必然查不到东西。请先到文档页上传文件，等索引任务完成后再回来。"
              action={
                <a href={documentsHref} className={buttonVariants({ variant: "secondary", size: "sm" })}>
                  去上传文档
                </a>
              }
            />
          ) : null}
        </div>
      </section>
    </div>
  );
}
