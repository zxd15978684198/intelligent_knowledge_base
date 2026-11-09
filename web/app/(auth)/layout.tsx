import type { Metadata } from "next";
import type { ReactNode } from "react";

import { shellEnter } from "@/components/app-shell/shell-motion";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: "登录 · PyKnora",
  description: "登录或注册 PyKnora 账号，管理知识库、文档与检索。",
};

/**
 * 认证页外壳：居中的单卡片，不挂应用导航。
 *
 * 视觉语言与应用壳同步（任务 N3-5）：卡片圆角 lg（=radius-lg 16px 令牌）+
 * shadow-lg 着色阴影浮起，入场走壳层同一条 stagger 动效（opacity + translateY(6px)，
 * `prefers-reduced-motion` 自动关闭）。:focus-visible 焦点环由 globals.css 统一提供。
 *
 * 服务端组件（只做排版与 metadata）：认证状态在客户端由 `AuthProvider` 管理，
 * 但登录页本身不依赖身份即可渲染，没必要把它也变成客户端组件。
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  const enter = shellEnter(0);

  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas px-4 py-10">
      <main
        className={cn(
          "w-full max-w-md rounded-lg border border-border bg-surface p-6 shadow-lg sm:p-8",
          enter.className,
        )}
        style={enter.style}
      >
        {children}
      </main>
    </div>
  );
}
