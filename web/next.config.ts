import type { NextConfig } from "next";

/**
 * 同源代理：浏览器只访问 :3000，Next 把 /api/* 与 /health 转发给 FastAPI。
 * 选这条而不是给 FastAPI 加 CORS：D4 要求 Bearer 令牌留在 localStorage，
 * 同源可以让浏览器不触发预检，也不需要在后端放宽 `allow_origins`。
 *
 * 目标地址用环境变量覆盖（本地默认 8010，DAG 文档里记了怎么换端口）。
 */
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8010";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  /**
   * 容器交付需要 standalone 产物：`next build` 默认输出要带着 node_modules
   * 一起跑，`output: "standalone"` 会把最小运行时（含 server.js）单独放进
   * `.next/standalone`，镜像里因此不需要 node_modules（本仓库约 500MB）。
   *
   * 代价：`rewrites()` 在**构建时**求值并被烤进 standalone 产物，所以
   * `API_PROXY_TARGET` 必须在 `docker build` 时给定（见 web/Dockerfile 的 ARG），
   * 运行期改这个环境变量不会生效。
   */
  output: "standalone",
  /**
   * Next 16 默认拦跨来源的 dev 资源（HMR 的 WebSocket 等）。浏览器一律用
   * `http://127.0.0.1:3000` 打开、dev server 绑的是 `localhost`，两者算不同来源，
   * 于是 `/_next/hmr` 被拦（控制台刷 `WebSocket ... ERR_INVALID_HTTP_RESPONSE`）。
   * 只放行回环地址，不放开到局域网。
   */
  allowedDevOrigins: ["127.0.0.1", "localhost", "[::1]"],
  /**
   * 关掉 Next 自带的开发指示器（页面右下/右上那个圆形 N 徽标）。
   * 它的菜单是 Next 英文原文，本项目界面全是中文，留着只会让人以为是自己产品的控件；
   * 编译错误与 HMR 状态仍然照常输出到终端，不影响排错。
   */
  devIndicators: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_PROXY_TARGET}/api/:path*` },
      { source: "/health", destination: `${API_PROXY_TARGET}/health` },
      // 后端交互式文档也走同源，方便演示时点开查看。
      // `/docs` 是 Swagger UI 页面本身；只代理 openapi.json 会让页面里的 /docs 链接 404。
      // `/docs/:path*` 一并放行：Swagger 的 oauth2-redirect 等子路径也在 /docs 下。
      { source: "/openapi.json", destination: `${API_PROXY_TARGET}/openapi.json` },
      { source: "/docs", destination: `${API_PROXY_TARGET}/docs` },
      { source: "/docs/:path*", destination: `${API_PROXY_TARGET}/docs/:path*` },
      // Swagger UI 的 JS/CSS 由后端自托管（不发 CDN），同源转发静态资源。
      { source: "/static/:path*", destination: `${API_PROXY_TARGET}/static/:path*` },
    ];
  },
};

export default nextConfig;
