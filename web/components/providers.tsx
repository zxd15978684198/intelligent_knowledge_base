"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { AuthProvider } from "@/features/auth/auth-context";

/**
 * 客户端 Provider 组合。顺序是有意的：`AuthProvider` 在最外层，`QueryClientProvider`
 * 在里层——先有身份与工作区，查询层才能按工作区拼 queryKey。
 *
 * 查询默认值口径（DAG §2 N4）：
 * - `staleTime: 15_000`：15 秒内同一查询不重复发，切页/回退不会闪加载态；
 * - `retry: 1`：只重试一次。后端返回的多是 4xx（参数、权限、`If-Match` 冲突），重试
 *   只会把一次失败放大成三次请求；
 * - `refetchOnWindowFocus: false`：P0 用轮询表达"作业在跑"，窗口聚焦时全场重拉会在
 *   演示时刷出一片加载态；
 * - **不挂全局 `onError`**：查询失败由各页面就地展示（错误卡片 + 重试入口），全局弹窗
 *   会把一次失败变成满屏 toast，也会让"异常分支不伪装成功"难以核查。
 */
function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: 0,
      },
    },
  });
}

export function Providers({ children }: { children: ReactNode }) {
  // 惰性初始化：浏览器会话里只建一个 QueryClient；SSR 时每个请求各建一个，
  // 避免把上一个请求的缓存带进下一个用户（模块级单例在服务端会被跨请求复用）。
  const [queryClient] = useState(createQueryClient);

  return (
    <AuthProvider>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </AuthProvider>
  );
}
