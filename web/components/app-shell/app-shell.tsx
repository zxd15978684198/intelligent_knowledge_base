"use client";

import type { ReactNode } from "react";

import { AppNav } from "@/components/app-shell/app-nav";
import { Brand } from "@/components/app-shell/brand";
import { readIdentityView } from "@/components/app-shell/identity-view";
import { MobileNav } from "@/components/app-shell/mobile-nav";
import { SkipLink } from "@/components/app-shell/skip-link";
import { UserBlock } from "@/components/app-shell/user-block";
import { useAuth } from "@/features/auth/auth-context";

/**
 * 登录后的应用壳（DAG §2 N4）。
 *
 * 布局口径：
 * - **≥1024px**：左侧 256px 带文字侧栏（品牌 / 导航 / 身份区），主内容区在右；
 * - **<1024px**：侧栏隐藏，换成顶部条 + 抽屉（`MobileNav`）；
 * - 主内容区从 `<main id="main-content">` 开始，与 `SkipLink` 的目标锚点对应。
 *
 * 防横向滚动的三个关键写法（1440 / 1024 / 390 三档都要成立）：
 * 1. 侧栏 `shrink-0` + 固定 `w-64`，主列 `min-w-0 flex-1`——flex 子项默认
 *    `min-width: auto`，长 ID、长 token 会把列撑破，`min-w-0` 是硬性条件；
 * 2. 所有会变长的文本（用户名、工作区 id）都 `truncate`，并带上完整值的 `title`；
 * 3. `main` 自己也 `min-w-0`，页面里的宽表格由各页面负责自身的 `overflow-x-auto`。
 *
 * 当前工作区 id 取自 `useAuth().identity.default_workspace_id`（经
 * `readIdentityView` 窄化）；壳层只**展示**它，请求头与 queryKey 由各业务节点拼。
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { identity, logout } = useAuth();
  const identityView = readIdentityView(identity);

  return (
    <div className="min-h-dvh bg-canvas">
      <SkipLink />
      <div className="lg:flex">
        <aside className="hidden border-r border-border bg-surface lg:sticky lg:top-0 lg:flex lg:h-dvh lg:w-64 lg:shrink-0 lg:flex-col lg:overflow-y-auto">
          <div className="px-3 pt-5">
            <Brand />
          </div>
          <AppNav />
          <UserBlock identity={identityView} onLogout={logout} />
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <MobileNav identity={identityView} onLogout={logout} />
          <main
            id="main-content"
            tabIndex={-1}
            className="min-w-0 flex-1 px-4 py-6 lg:px-8 lg:py-8"
          >
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
