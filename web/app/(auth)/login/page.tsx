"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui";

import { useAuth } from "@/features/auth/auth-context";
import { AuthForm } from "@/features/auth/components/auth-form";
import { AFTER_LOGIN_PATH } from "@/features/auth/routes";

/**
 * 登录 / 注册单页（两个 Tab）。
 *
 * 页面只负责排版与"已登录就别停留在这里"的兜底跳转；表单行为全在
 * `useAuthForm`（成功后的 `router.replace("/kbs")` 也在那里）。
 *
 * 两个 Tab 的内容各自持有独立表单状态：注册填到一半切回登录不会串值。
 */
export default function LoginPage() {
  const { status, sessionError } = useAuth();
  const router = useRouter();

  // 已有有效令牌（例如从书签直接打开 /login）：验活通过后直接进应用，别让用户再登一次。
  useEffect(() => {
    if (status === "authenticated") router.replace(AFTER_LOGIN_PATH);
  }, [router, status]);

  return (
    <section className="space-y-6">
      <header className="space-y-2">
        <p className="text-xs font-medium tracking-[0.18em] text-fg-subtle uppercase">PyKnora</p>
        <h1 className="text-[28px]/[32px] font-semibold tracking-tight leading-tight text-fg">登录工作台</h1>
        <p className="max-w-[65ch] text-[13px] leading-relaxed text-fg-muted">
          管理知识库、上传文档、调试检索；工作区与权限由管理员分配。
        </p>
      </header>

      {sessionError === null ? null : (
        <p
          role="status"
          className="rounded-md border border-warning bg-warning-weak px-3 py-2 text-sm leading-relaxed text-fg"
        >
          {sessionError}
        </p>
      )}

      <Tabs defaultValue="login">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="login">登录</TabsTrigger>
          <TabsTrigger value="register">注册</TabsTrigger>
        </TabsList>
        <TabsContent value="login" className="pt-5">
          <AuthForm mode="login" />
        </TabsContent>
        <TabsContent value="register" className="pt-5">
          <AuthForm mode="register" />
        </TabsContent>
      </Tabs>

      <p className="max-w-[65ch] text-xs leading-relaxed text-fg-subtle">
        密码只在本次请求中用于换取令牌，不会写入 localStorage；刷新页面靠令牌重新验活。
      </p>
    </section>
  );
}
