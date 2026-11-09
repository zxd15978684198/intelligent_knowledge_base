import * as React from "react";

import { cn } from "@/lib/utils";

import styles from "./ui-motion.module.css";

/**
 * Skeleton —— 加载占位块（升级计划 §1.4 I4）。
 *
 * 默认 `aria-hidden`：占位条对读屏没有意义，真正的"正在加载"由外层容器用
 * `aria-busy` 表达。`animate-pulse` 保留（`primitives.test.tsx` 断言它），
 * 另叠一层 `skeletonShimmer` 高光扫过；两者都只动 opacity/transform，
 * 由 `globals.css` 全局降级 + CSS Module 内媒体查询双保险关掉。
 */
export const Skeleton = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  function Skeleton({ className, ...props }, ref) {
    return (
      <div
        ref={ref}
        aria-hidden="true"
        className={cn(
          "animate-pulse rounded-md bg-surface-muted",
          styles.skeletonShimmer ?? "",
          className,
        )}
        {...props}
      />
    );
  },
);

Skeleton.displayName = "Skeleton";
