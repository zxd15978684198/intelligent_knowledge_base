"use client";

import { Warning } from "@phosphor-icons/react";

import { Badge } from "@/components/ui";
import { cn } from "@/lib/utils";

import { degradedEntries, reasonLabel } from "./format";

/**
 * 降级检索警告条。
 *
 * `degraded` 非空 = **这一轮检索少了一路/几路召回，但接口仍然返回 200 和结果**。
 * 这正是最容易骗人的状态：不提示的话，用户会拿一份「少了一半候选」的排名去调参，
 * 还会以为候选池就这么大。
 *
 * 因此这里：
 * - `role="status"`，读屏会播报（不打断当前朗读）；
 * - 逐键列出通道 + 原因码，并说明「不能当作完整检索结果」；
 * - 用警告色 + 文字双通道，不只靠颜色。
 */
export interface DegradedBannerProps {
  degraded: Record<string, string> | undefined;
  className?: string;
}

export function DegradedBanner({ degraded, className }: DegradedBannerProps) {
  const entries = degradedEntries(degraded);
  if (entries.length === 0) return null;

  return (
    <div
      role="status"
      data-testid="degraded-banner"
      className={cn("rounded-md border border-warning bg-warning-weak p-3", className)}
    >
      <p className="flex items-center gap-2 text-sm font-semibold text-fg">
        <Warning aria-hidden="true" size={18} weight="fill" className="text-warning" />
        降级检索：结果不完整
      </p>
      <p className="mt-1 text-sm text-fg-muted">
        以下通道本次没有完成召回，返回结果只来自其余通道，不能当作完整检索结果使用。
      </p>
      <ul className="mt-2 flex flex-col gap-1.5">
        {entries.map((entry) => (
          <li key={entry.channel} className="flex flex-wrap items-center gap-2 text-sm">
            <Badge tone="warning">{entry.label}</Badge>
            <span className="text-fg">{reasonLabel(entry.reason)}</span>
            <code className="rounded bg-surface px-1 py-0.5 font-mono text-xs text-fg-muted">
              {entry.reason}
            </code>
          </li>
        ))}
      </ul>
    </div>
  );
}
