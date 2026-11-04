import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "登录 · PyKnora",
  description: "登录或注册 PyKnora 账号，管理知识库、文档与检索。",
};

/**
 * 认证页外壳：居中的单卡片，不挂应用导航。
 *
 * 服务端组件（只做排版与 metadata）：认证状态在客户端由 `AuthProvider` 管理，
 * 但登录页本身不依赖身份即可渲染，没必要把它也变成客户端组件。
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas px-4 py-10">
      <main className="w-full max-w-md rounded-lg border border-border bg-surface p-6 shadow-sm sm:p-8">
        {children}
      </main>
    </div>
  );
}
