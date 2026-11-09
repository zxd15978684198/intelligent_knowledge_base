import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * Badge —— 状态/标签用的小色块。
 *
 * 每个 tone 都是「弱底 + 同色文字 + 同色描边」，正文对比度 ≥ 4.5:1；
 * 但颜色只是辅助：任何状态都必须同时给出文字（见 `StatusBadge`）。
 */
const badgeVariants = cva(
  [
    "inline-flex items-center gap-1 rounded-sm border",
    "px-2.5 py-0.5 text-xs font-medium leading-5 tracking-wide",
    "whitespace-nowrap",
  ],
  {
    variants: {
      tone: {
        neutral: "border-border bg-surface-muted text-fg-muted",
        info: "border-info/30 bg-info-weak text-info",
        success: "border-success/30 bg-success-weak text-success",
        warning: "border-warning/30 bg-warning-weak text-warning",
        danger: "border-danger/30 bg-danger-weak text-danger",
      },
    },
    defaultVariants: {
      tone: "neutral",
    },
  },
);

export type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(function Badge(
  { className, tone, ...props },
  ref,
) {
  return <span ref={ref} className={cn(badgeVariants({ tone }), className)} {...props} />;
});

Badge.displayName = "Badge";

export { badgeVariants };
