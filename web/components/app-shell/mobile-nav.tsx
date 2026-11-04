"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { ListIcon, XIcon } from "@phosphor-icons/react";
import { useState } from "react";

import { AppNav } from "@/components/app-shell/app-nav";
import { Brand } from "@/components/app-shell/brand";
import type { IdentityView } from "@/components/app-shell/identity-view";
import { UserBlock } from "@/components/app-shell/user-block";

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

  const close = () => {
    setOpen(false);
  };

  return (
    <header className="sticky top-0 z-30 flex min-h-14 items-center gap-2 border-b border-border bg-surface px-3 lg:hidden">
      <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
        <DialogPrimitive.Trigger asChild>
          <button
            type="button"
            aria-label="打开导航菜单"
            className="flex size-11 shrink-0 items-center justify-center rounded-md text-fg-muted transition-colors duration-150 hover:bg-surface-muted hover:text-fg"
          >
            <ListIcon aria-hidden="true" className="size-5" />
          </button>
        </DialogPrimitive.Trigger>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-fg/40" />
          <DialogPrimitive.Content className="fixed inset-y-0 left-0 z-50 flex w-72 max-w-[calc(100vw-3rem)] flex-col overflow-y-auto border-r border-border bg-surface pb-4 shadow-lg">
            <div className="flex items-center justify-between gap-2 px-3 pt-3">
              <Brand className="min-w-0" />
              <DialogPrimitive.Close asChild>
                <button
                  type="button"
                  aria-label="关闭导航菜单"
                  className="flex size-11 shrink-0 items-center justify-center rounded-md text-fg-muted transition-colors duration-150 hover:bg-surface-muted hover:text-fg"
                >
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
