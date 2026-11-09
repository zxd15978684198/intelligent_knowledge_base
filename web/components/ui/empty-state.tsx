import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * EmptyState —— 空列表 / 无可展示数据的统一占位。
 *
 * 三件事必须说清：发生了什么（title）、为什么（description）、下一步怎么走（action）。
 * 不做插画、不做自动动画（计划 §6）；用 `shadow-sm` + 状态点给主操作一个视觉锚点
 * （升级计划 §1.4 I4），但不改变三件事的文字语义。
 */
export interface EmptyStateProps {
  title: string;
  description?: string;
  /** 主操作，通常是 `<Button>` 或指向下一步的链接。 */
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ title, description, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-surface-raised px-6 py-10 text-center shadow-sm",
        className,
      )}
    >
      <span aria-hidden="true" className="mb-1 size-2 rounded-full bg-primary-weak" />
      <h3 className="text-base font-semibold tracking-tight text-fg">{title}</h3>
      {description ? (
        <p className="max-w-[65ch] text-sm leading-relaxed text-fg-muted">{description}</p>
      ) : null}
      {action ? (
        <div className="mt-2 flex flex-wrap items-center justify-center gap-2">{action}</div>
      ) : null}
    </div>
  );
}
