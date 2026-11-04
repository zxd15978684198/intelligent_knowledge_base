/**
 * 应用壳的公开出口：W3 的六个业务节点与 `(app)` 路由组都从这里 import。
 *
 * 只导出"别人确实要用"的东西；`Brand` / `UserBlock` / `MobileNav` 属于壳的内部结构，
 * 不对外，避免被当成通用组件在业务页里复用而绕开壳层的布局约束。
 */
export { AppShell } from "@/components/app-shell/app-shell";
export { ShellSkeleton } from "@/components/app-shell/shell-skeleton";
export { isNavItemActive, NAV_ITEMS, type NavItem } from "@/components/app-shell/nav-items";
export { readIdentityView, type IdentityView } from "@/components/app-shell/identity-view";
