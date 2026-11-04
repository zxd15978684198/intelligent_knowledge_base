"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { ShellSkeleton } from "@/components/app-shell/shell-skeleton";
import { useAuth } from "@/features/auth/auth-context";

/**
 * 受保护区守卫：`(app)` 组的所有页面都在它下面。
 *
 * 三种状态分别处理：
 * - `loading`：还在读 localStorage / 验活。渲染与真实壳同构的骨架屏——**不能**先渲染
 *   登录页，否则刷新页面时会闪一下登录页再跳回来，用户以为掉线了。
 * - `anonymous`：`router.replace("/login")`。用 `replace` 而不是 `push`：未登录页面
 *   不该留在历史栈里，否则登录后按"后退"又退回守卫态。
 * - `authenticated`：渲染 children。"能看哪个按钮"由各页面自己的权限组件决定，守卫
 *   只回答"有没有身份"这一件事。
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { status } = useAuth();

  useEffect(() => {
    if (status === "anonymous") {
      router.replace("/login");
    }
  }, [router, status]);

  // 跳转落地前继续显示骨架屏：既躲开"未登录内容 → 登录页"的闪动，也避免 anonymous
  // 期间继续渲染 children 里的查询（那会打出一串 401）。
  if (status !== "authenticated") {
    return <ShellSkeleton />;
  }

  return <>{children}</>;
}
