import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Skeleton —— 加载占位块。
 *
 * 默认 `aria-hidden`：占位条对读屏没有意义，真正的"正在加载"由外层容器用
 * `aria-busy` 表达。`animate-pulse` 的动效由 `globals.css` 的
 * `prefers-reduced-motion` 全局关掉，组件内不另写动画。
 */
export const Skeleton = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  function Skeleton({ className, ...props }, ref) {
    return (
      <div
        ref={ref}
        aria-hidden="true"
        className={cn("animate-pulse rounded-md bg-surface-muted", className)}
        {...props}
      />
    );
  },
);

Skeleton.displayName = "Skeleton";
