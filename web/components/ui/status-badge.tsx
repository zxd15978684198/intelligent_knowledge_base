import * as React from "react";

import { cn } from "@/lib/utils";

import { badgeVariants, type BadgeTone } from "./badge";

/**
 * StatusBadge —— **双通道**状态徽标：文字（永远可见）+ 颜色（圆点，装饰）。
 *
 * 只靠颜色区分状态是验收红线（计划 D10 / §6）：色盲、灰度打印、高对比模式下
 * 颜色会丢，文字不会。`srLabel` 给读屏补一句完整语义（例如「作业状态：处理中」），
 * 避免只念出一个短词。
 */
export interface StatusBadgeProps {
  /** 可见文字，必填——它是文字通道，不允许只传颜色。 */
  label: string;
  tone: BadgeTone;
  /** 读屏补充说明（视觉隐藏），例如「作业状态：处理中」。 */
  srLabel?: string;
  className?: string;
}

const dotTone: Record<BadgeTone, string> = {
  neutral: "bg-fg-subtle",
  info: "bg-info",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
};

export function StatusBadge({ label, tone, srLabel, className }: StatusBadgeProps) {
  return (
    <span
      data-tone={tone}
      className={cn(badgeVariants({ tone }), "gap-1.5", className)}
    >
      <span aria-hidden="true" className={cn("size-2 shrink-0 rounded-full", dotTone[tone])} />
      <span className="tabular-nums">{label}</span>
      {srLabel ? <span className="sr-only">{srLabel}</span> : null}
    </span>
  );
}
