import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Card —— 白色内容面 + 着色阴影（§2 `--shadow-sm`）：靠阴影浮起而非仅靠 1px 边框，
 * 去掉「通用白底卡片」观感（升级计划 C1；玻璃拟态仍排除）。
 * `CardTitle` 固定渲染 `<h3>`：页面标题是 `<h2>`，卡片是它下面的一级，标题层级不跳。
 */
export const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  function Card({ className, ...props }, ref) {
    return (
      <div
        ref={ref}
        className={cn(
          "rounded-lg border border-border/60 bg-surface shadow-sm",
          "transition-[box-shadow,transform] duration-base ease-out",
          className,
        )}
        {...props}
      />
    );
  },
);

Card.displayName = "Card";

export const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  function CardHeader({ className, ...props }, ref) {
    return <div ref={ref} className={cn("flex flex-col gap-1 px-4 pt-4", className)} {...props} />;
  },
);

CardHeader.displayName = "CardHeader";

export const CardTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(function CardTitle({ className, ...props }, ref) {
  return (
    <h3
      ref={ref}
      className={cn("text-base font-semibold text-fg", className)}
      {...props}
    />
  );
});

CardTitle.displayName = "CardTitle";

export const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  function CardContent({ className, ...props }, ref) {
    return <div ref={ref} className={cn("px-4 py-4 text-sm text-fg", className)} {...props} />;
  },
);

CardContent.displayName = "CardContent";
