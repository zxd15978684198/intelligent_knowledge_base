"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";

import { cn } from "@/lib/utils";

import { CloseIcon } from "./icons";

/**
 * Dialog —— Radix `@radix-ui/react-dialog` 包一层。
 *
 * 焦点陷阱、Esc 关闭、`aria-modal`、滚动锁、关闭后焦点回到触发元素，全部由 Radix 负责，
 * 这里只定样式与「必须给标题」的约束：没有可访问名称的对话框在验收里是硬失败，
 * 所以 `title` 做成必填 prop，而不是让调用方自己记得塞一个 `DialogTitle`。
 *
 * 用法：
 * ```tsx
 * <Dialog>
 *   <DialogTrigger asChild><Button>新建</Button></DialogTrigger>
 *   <DialogContent title="创建知识库" description="名称 3–50 字">
 *     <form>…</form>
 *   </DialogContent>
 * </Dialog>
 * ```
 */
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;

const widthClass = {
  sm: "max-w-sm",
  md: "max-w-lg",
  lg: "max-w-2xl",
} as const;

export type DialogWidth = keyof typeof widthClass;

export interface DialogContentProps
  extends Omit<React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>, "title"> {
  /** 对话框标题（必填）：同时作为 `aria-labelledby` 指向的可访问名称。 */
  title: React.ReactNode;
  /** 副标题/说明，可选；不传就不会产生悬空的 `aria-describedby`。 */
  description?: React.ReactNode;
  /** 面板宽度档位，默认 `md`（max-w-lg）。 */
  width?: DialogWidth;
}

export const DialogContent = React.forwardRef<HTMLDivElement, DialogContentProps>(
  function DialogContent(
    { className, title, description, width = "md", children, ...props },
    ref,
  ) {
    return (
      <DialogPrimitive.Portal>
        {/* 轻遮罩：计划 §1.6 P3 写的是 bg-fg/[0.04]，实测 4% 几乎不可见、
            模态的「焦点隔离」缺视觉线索，这里取 20% + 2px 模糊折中（偏差见回报）。 */}
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-fg/20 backdrop-blur-[2px]" />
        <DialogPrimitive.Content
          ref={ref}
          className={cn(
            "fixed top-1/2 left-1/2 z-50 w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2",
            "max-h-[calc(100dvh-2rem)] overflow-y-auto",
            "rounded-lg border border-border/60 bg-surface p-6 shadow-lg",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-ring",
            widthClass[width],
            className,
          )}
          aria-modal="true"
          {...props}
        >
          <DialogPrimitive.Title className="pr-12 text-base font-semibold tracking-tight text-fg">
            {title}
          </DialogPrimitive.Title>
          {description ? (
            <DialogPrimitive.Description className="mt-1 text-sm text-fg-muted">
              {description}
            </DialogPrimitive.Description>
          ) : null}
          <div className="mt-4">{children}</div>
          <DialogPrimitive.Close
            aria-label="关闭对话框"
            className={cn(
              "absolute top-1 right-1 grid size-11 place-items-center rounded-md",
              "text-fg-muted transition-[transform,background-color,color] duration-fast ease-out",
              "hover:bg-surface-muted hover:text-fg",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-ring",
            )}
          >
            <CloseIcon />
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    );
  },
);

DialogContent.displayName = "DialogContent";
