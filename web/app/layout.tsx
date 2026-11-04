import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { Providers } from "@/components/providers";

import "./globals.css";

/**
 * 根布局：只放"全站都必须有"的东西。
 *
 * - `lang="zh-CN"`：读屏发音与中文断行都依赖它，不能省。
 * - `Providers` 包住 children：QueryClient 与 AuthProvider 都是客户端上下文，
 *   放在这里之后 `(app)` 与 `(auth)` 两个路由组共享同一份缓存与登录态。
 * - 这里刻意**不**渲染侧栏：`(app)` 组有自己的壳（`app/(app)/layout.tsx`），
 *   `(auth)` 组是裸页面，根布局不该替某一路由组做决定。
 */
export const metadata: Metadata = {
  title: "PyKnora 证据工作台",
  description: "知识库检索、引用定位与证据核对的一体化工作台",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-dvh bg-canvas text-fg antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
