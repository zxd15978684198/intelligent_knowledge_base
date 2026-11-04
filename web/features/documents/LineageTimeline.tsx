"use client";

/**
 * 版本链时间线。
 *
 * **后端真实形态**（`src/pyknora/api/v1/lineage.py` → `schemas/lineage.py:44-57`）：
 * `DocumentLineage{document_id, active_version_id, contributors, versions[]}`，
 * 每条 `LineageEntry` = `DocumentVersion` 的 8 个字段 + 读时派生的 `ordinal` / `is_active`。
 *
 * 计划书里写的"版本链 + 发布事件"**与实现不符**：后端没有发布事件表/字段，
 * 只有版本快照 + 谁是当前活动版本（`active_version_id` 直接透传 `documents` 指针）。
 * 所以这里只画版本链并标出活动版本，不编造"发布时间"之类的节点。
 *
 * 两个边界必须如实处理：
 * - `active_version_id` 可能**不在** `versions` 里（后端如实返回、不造条目）→ 提示一句；
 * - `contributors` 是按首次出现顺序去重的 principal id，不是人名 → 原样显示短 id。
 */

import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui";
import { cn, formatBytes, formatDateTime, shortId } from "@/lib/utils";

import { describeApiError, formatErrorLabel } from "../jobs/errors";
import { getLineage } from "./api";
import { documentKeys } from "./queryKeys";
import type { LineageEntry } from "./types";

export interface LineageTimelineProps {
  documentId: string;
  /** 缓存键第二段（`qk` 的硬要求）。 */
  workspaceId: string;
  className?: string;
}

function hasActiveEntry(versions: LineageEntry[], activeVersionId: string | null): boolean {
  return versions.some(
    (version) => version.is_active || version.document_version_id === activeVersionId,
  );
}

export function LineageTimeline({ documentId, workspaceId, className }: LineageTimelineProps) {
  const query = useQuery({
    queryKey: documentKeys.lineage(workspaceId, documentId),
    queryFn: () => getLineage(documentId, { workspaceId }),
  });

  if (query.isPending) {
    return (
      <p className={cn("text-sm text-fg-muted", className)} aria-busy="true">
        正在加载版本链…
      </p>
    );
  }

  if (query.isError) {
    return (
      <div
        role="alert"
        className={cn("rounded-md border border-danger bg-danger-weak px-3 py-2 text-sm text-danger", className)}
      >
        <p>版本链读取失败：{formatErrorLabel(describeApiError(query.error))}</p>
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
    );
  }

  const lineage = query.data;
  const activeMissing = !hasActiveEntry(lineage.versions, lineage.active_version_id);

  return (
    <section className={cn("flex flex-col gap-3", className)} aria-labelledby="lineage-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="lineage-title" className="text-sm font-medium text-fg">
          版本链
        </h3>
        <p className="tabular text-xs text-fg-muted">
          共 {lineage.versions.length} 个版本 · {lineage.contributors.length} 位贡献者
        </p>
      </div>

      <p className="text-xs text-fg-subtle">
        顺序由版本创建时间（<code className="font-mono">created_at</code>）决定，
        <code className="font-mono">ordinal</code> 是读时派生的展示序号（1 起），不是库里的事实。
        后端没有「发布事件」记录，所以这里只有版本快照与活动版本标记。
      </p>

      {lineage.versions.length === 0 ? (
        <p className="text-sm text-fg-muted">这篇文档还没有任何版本快照。</p>
      ) : (
        <ol className="flex flex-col gap-0 border-l border-border pl-4">
          {lineage.versions.map((version) => {
            const active =
              version.is_active || version.document_version_id === lineage.active_version_id;
            return (
              <li key={version.document_version_id} className="relative pb-4 last:pb-0">
                <span
                  aria-hidden="true"
                  className={cn(
                    "absolute -left-[1.3rem] top-1.5 h-2 w-2 rounded-full",
                    active ? "bg-success" : "bg-fg-subtle",
                  )}
                />
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="tabular text-sm font-medium text-fg">v{version.ordinal}</span>
                  {active ? (
                    <span className="rounded-sm bg-success-weak px-1.5 py-0.5 text-xs font-medium text-success">
                      当前活动版本
                    </span>
                  ) : null}
                  <span className="tabular text-xs text-fg-muted">
                    {formatDateTime(version.created_at)}
                  </span>
                </div>
                <dl className="mt-1 grid grid-cols-1 gap-x-4 gap-y-0.5 text-xs text-fg-muted sm:grid-cols-2">
                  <div className="flex gap-1">
                    <dt>版本 id</dt>
                    <dd className="font-mono text-fg">{shortId(version.document_version_id)}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt>大小</dt>
                    <dd className="tabular text-fg">{formatBytes(version.byte_size)}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt>内容哈希</dt>
                    <dd className="font-mono text-fg">{shortId(version.content_hash, 12)}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt>解析状态</dt>
                    <dd className="font-mono text-fg">{version.parse_status}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt>来源</dt>
                    <dd className="font-mono text-fg">{version.ingestion_source}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt>创建者</dt>
                    <dd className="font-mono text-fg">{shortId(version.created_by_principal_id)}</dd>
                  </div>
                </dl>
              </li>
            );
          })}
        </ol>
      )}

      {activeMissing ? (
        <p className="rounded-md border border-warning bg-warning-weak px-3 py-2 text-xs text-warning">
          文档的 <code className="font-mono">active_version_id</code>{" "}
          {lineage.active_version_id === null ? "为空" : "指向的版本不在这次返回的版本链里"}
          （后端如实返回，不补造条目）。界面上因此没有标出活动版本。
        </p>
      ) : null}

      {lineage.contributors.length > 0 ? (
        <div className="text-xs text-fg-muted">
          <p className="font-medium text-fg">贡献者（按首次出现顺序去重）</p>
          <ul className="mt-1 flex flex-wrap gap-2">
            {lineage.contributors.map((principalId) => (
              <li key={principalId} className="rounded-sm bg-surface-muted px-1.5 py-0.5 font-mono text-fg">
                {shortId(principalId)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
