"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { ListIcon, XIcon } from "@phosphor-icons/react";
import { useState } from "react";

import { AppNav } from "@/components/app-shell/app-nav";
import { Brand } from "@/components/app-shell/brand";
import type { IdentityView } from "@/components/app-shell/identity-view";
import { shellEnter } from "@/components/app-shell/shell-motion";
import { UserBlock } from "@/components/app-shell/user-block";
import { cn } from "@/lib/utils";

/** 图标按钮（菜单/关闭）：hover 换底色，按下微缩放——动效只动 transform。 */
const ICON_BUTTON =
  "flex size-11 shrink-0 items-center justify-center rounded-md text-fg-muted transition-[background-color,color,transform] duration-fast hover:bg-surface-muted hover:text-fg active:scale-[0.98]";

/**
 * 窄屏导航：顶部条 + 左侧抽屉（`lg` 以下显示，桌面由侧栏接管）。
 *
 * 每件事都交给 Radix Dialog，而不是手写：
 * - **Esc 关闭**、焦点陷阱、`aria-modal`、关闭后焦点回到触发按钮，都由 Dialog 保证；
 *   手写 keydown 的那份实现几乎都会漏掉"焦点归还"。
 * - 抽屉是 `modal`：背景内容对读屏隐藏，Tab 不会跑到抽屉外面。
 * - 点击导航项后 `onNavigate` 关闭抽屉，避免"点了链接抽屉还盖着页面"。
 *
 * 窄屏宽度用 `w-72` + `max-w-[calc(100vw-3rem)]`：390px 视口下仍留出 48px 空白，
 * 保证"抽屉不是整屏"且不产生横向滚动。
 */
export function MobileNav({ identity, onLogout }: { identity: IdentityView; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const headerEnter = shellEnter(0);

  const close = () => {
    setOpen(false);
  };

  return (
    <header
      className={cn(
        // 层次感（任务 N3-1）：顶栏 shadow-sm 起浮 + 底边弱化（border-border/60），
        // 白色顶栏浮在 canvas 上，不再靠一条硬边框分界。
        "sticky top-0 z-30 flex min-h-14 items-center gap-2 border-b border-border/60 bg-surface px-3 shadow-sm lg:hidden",
        headerEnter.className,
      )}
      style={headerEnter.style}
    >
      <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
        <DialogPrimitive.Trigger asChild>
          <button type="button" aria-label="打开导航菜单" className={ICON_BUTTON}>
            <ListIcon aria-hidden="true" className="size-5" />
          </button>
        </DialogPrimitive.Trigger>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-fg/40" />
          <DialogPrimitive.Content className="fixed inset-y-0 left-0 z-50 flex w-72 max-w-[calc(100vw-3rem)] flex-col overflow-y-auto border-r border-border bg-surface pb-4 shadow-lg">
            <div className="flex items-center justify-between gap-2 px-3 pt-3">
              <Brand className="min-w-0" />
              <DialogPrimitive.Close asChild>
                <button type="button" aria-label="关闭导航菜单" className={ICON_BUTTON}>
                  <XIcon aria-hidden="true" className="size-5" />
                </button>
              </DialogPrimitive.Close>
            </div>
            <DialogPrimitive.Title className="sr-only">导航菜单</DialogPrimitive.Title>
            <DialogPrimitive.Description className="sr-only">
              在 PyKnora 的知识库、成员与接口文档之间切换
            </DialogPrimitive.Description>
            <AppNav onNavigate={close} />
            <UserBlock identity={identity} onLogout={onLogout} onNavigate={close} />
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
      <Brand className="min-w-0" />
    </header>
  );
}
