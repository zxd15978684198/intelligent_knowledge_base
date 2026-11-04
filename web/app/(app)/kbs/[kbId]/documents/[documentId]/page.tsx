"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui";
import { useAuth } from "@/features/auth";
import { ChunkList } from "@/features/documents/ChunkList";
import { LineageTimeline } from "@/features/documents/LineageTimeline";
import { getDocument } from "@/features/documents/api";
import { documentKeys } from "@/features/documents/queryKeys";
import { describeApiError } from "@/features/jobs/errors";
import { shortId } from "@/lib/utils";
import { useKbId } from "../../layout";

/**
 * 文档详情页（N7）：元信息 + Tabs（切片 / 血缘）。
 *
 * 元信息只展示详情接口真实返回的字段；这里不显示「切片数」——后端没有该字段，
 * 而 ChunkPage.total 是页内条数，拿它当总数就是伪造。
 */
export default function DocumentDetailPage() {
  const kbId = useKbId();
  const { identity } = useAuth();
  const workspaceId = identity?.default_workspace_id ?? null;
  const params = useParams<{ documentId: string }>();
  const documentId = params.documentId;

  const detailQuery = useQuery({
    // workspaceId 为 null 时用空串占位，查询本身是 disabled，不会发请求。
    queryKey: documentKeys.detail(workspaceId ?? "", documentId),
    queryFn: () => getDocument(documentId, { workspaceId: workspaceId ?? undefined }),
    enabled: workspaceId !== null && documentId.length > 0,
  });

  if (workspaceId === null) {
    return (
      <p
        className="rounded-md border border-warning bg-warning-weak px-4 py-3 text-sm text-fg"
        role="status"
      >
        还没有拿到工作空间身份，稍后再试；详情、切片与血缘都需要 workspace 作用域。
      </p>
    );
  }

  const document = detailQuery.data;
  const described = detailQuery.isError ? describeApiError(detailQuery.error) : null;

  return (
    <div className="flex flex-col gap-6">
      <Link href={`/kbs/${kbId}/documents`} className="text-sm text-fg-muted hover:text-fg">
        ← 返回文档列表
      </Link>

      {detailQuery.isPending ? (
        <p className="text-sm text-fg-muted" role="status">
          正在读取文档信息…
        </p>
      ) : detailQuery.isError || document === undefined ? (
        <div className="flex flex-col gap-2 rounded-md border border-danger bg-danger-weak p-4" role="alert">
          <p className="text-sm font-medium text-fg">读不到这份文档</p>
          <p className="text-sm text-fg">{described?.message}</p>
          {described?.code ? (
            <p className="text-xs text-fg-muted">
              错误码 <code className="font-mono">{described.code}</code>
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => void detailQuery.refetch()}
            className="self-start rounded-md border border-border-strong bg-surface px-3 py-1.5 text-sm text-fg hover:bg-surface-muted"
          >
            重试
          </button>
        </div>
      ) : (
        <>
          <header className="flex flex-col gap-1">
            <h1 className="text-xl font-semibold text-fg" title={document.filename ?? undefined}>
              {document.filename ?? `文档 ${shortId(document.document_id)}`}
            </h1>
            <p className="text-sm text-fg-muted">
              {document.searchable
                ? "已发布：切片来自当前活动版本。"
                : "还没有活动索引，暂时不可检索；作业完成后会自动刷新。"}
            </p>
          </header>

          <dl className="grid gap-3 rounded-lg border border-border bg-surface p-4 text-sm sm:grid-cols-2">
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-fg-subtle">文档 ID</dt>
              <dd className="font-mono text-xs text-fg" title={document.document_id}>
                {shortId(document.document_id, 12)}
              </dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-fg-subtle">活动版本</dt>
              <dd className="font-mono text-xs text-fg">
                {document.active_document_version_id === null ||
                document.active_document_version_id === undefined
                  ? "没有活动版本"
                  : shortId(document.active_document_version_id, 12)}
              </dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-fg-subtle">索引版本</dt>
              <dd className="text-fg">
                {document.active_index_version === null || document.active_index_version === undefined
                  ? "未发布"
                  : `v${document.active_index_version}`}
              </dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-fg-subtle">行版本（row_version）</dt>
              <dd className="tabular text-fg">{document.row_version}</dd>
            </div>
          </dl>

          <Tabs defaultValue="chunks">
            <TabsList>
              <TabsTrigger value="chunks">切片</TabsTrigger>
              <TabsTrigger value="lineage">血缘</TabsTrigger>
            </TabsList>
            <TabsContent value="chunks">
              <ChunkList documentId={documentId} workspaceId={workspaceId} />
            </TabsContent>
            <TabsContent value="lineage">
              <LineageTimeline documentId={documentId} workspaceId={workspaceId} />
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}
