import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGuard } from "@/components/auth-guard";

/**
 * `(app)` 路由组的外壳：所有登录后页面共用。
 *
 * 顺序是 `AuthGuard`（外）→ `AppShell`（内）：未登录时连侧栏都不该出现，否则会闪出
 * 一个"能点但点不动"的导航。路由组本身不进 URL，用户看到的仍是 `/kbs`。
 *
 * 这里是服务端组件：`AuthGuard` / `AppShell` 各自是客户端边界，页面 `children` 由
 * 服务端渲染后以 props 传进去，因此壳层不拖累页面的 SSR。
 */
export default function AppGroupLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <AppShell>{children}</AppShell>
    </AuthGuard>
  );
}
