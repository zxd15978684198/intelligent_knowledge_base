"use client";

import * as React from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";

import { cn } from "@/lib/utils";

/**
 * Tabs —— Radix tabs（roving tabindex + 方向键 + `role="tablist"` 语义齐全）。
 * 触发器保持 44px 触控高度；激活态靠底色与文字色区分，不只靠下划线。
 */
export const Tabs = TabsPrimitive.Root;

export const TabsList = React.forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(function TabsList({ className, ...props }, ref) {
  return (
    <TabsPrimitive.List
      ref={ref}
      className={cn(
        "inline-flex min-h-hit items-center gap-1 rounded-lg border border-border/60 bg-surface-muted p-1",
        className,
      )}
      {...props}
    />
  );
});

TabsList.displayName = "TabsList";

export const TabsTrigger = React.forwardRef<
  HTMLButtonElement,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(function TabsTrigger({ className, ...props }, ref) {
  return (
    <TabsPrimitive.Trigger
      ref={ref}
      className={cn(
        "inline-flex min-h-hit items-center justify-center rounded-md px-3 text-sm font-medium",
        "text-fg-muted transition-[transform,background-color,color,box-shadow] duration-fast ease-out",
        "hover:text-fg",
        "data-[state=active]:bg-surface data-[state=active]:text-fg data-[state=active]:font-semibold data-[state=active]:shadow-sm",
        "disabled:cursor-not-allowed disabled:opacity-60",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-ring",
        className,
      )}
      {...props}
    />
  );
});

TabsTrigger.displayName = "TabsTrigger";

export const TabsContent = React.forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(function TabsContent({ className, ...props }, ref) {
  return (
    <TabsPrimitive.Content
      ref={ref}
      className={cn(
        "mt-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-ring",
        className,
      )}
      {...props}
    />
  );
});

TabsContent.displayName = "TabsContent";
