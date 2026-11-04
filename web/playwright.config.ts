import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { FAKE_CHAT_PORT, backendEnv } from "./tests/e2e/support/backend-env";
import { REPO_ROOT, WEB_ROOT } from "./tests/e2e/support/paths";
import { venvPython } from "./tests/e2e/support/worker";

/**
 * 端到端验收的编排：**一次跑起四个进程**，浏览器只认 `127.0.0.1:3000`。
 *
 * ```
 * 浏览器 ──► Next 3000 ──rewrites /api/*──► FastAPI 8000 ──► pgvector(5434)
 *                                              │
 *                                              ├─► 假上游 8123（OpenAI 兼容 SSE）
 *                                              └─► worker（scripts/run_worker.py，无端口）
 * ```
 *
 * 为什么用真实后端而不是 `page.route()` 造响应：这一轮要验收的恰恰是"接起来之后还能
 * 不能用"——`next.config.ts` 的 `/api/*` rewrites、SSE 会不会被代理缓冲、上传的
 * multipart 字段名与服务端是否对得上，这些在 mock 层全部看不见。前端单测与契约测试
 * 已经覆盖了组件级行为，e2e 再 mock 一遍只是自我复述。
 *
 * worker 没有端口可探活，所以不放在 `webServer` 里，而是由 `support/global-setup.ts`
 * 起停（见该文件注释）。
 */

const API_ORIGIN = "http://127.0.0.1:8000";
const UI_ORIGIN = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";
const FAKE_UPSTREAM_SCRIPT = path.join(WEB_ROOT, "tests", "e2e", "support", "fake-chat-upstream.mjs");

/** 已经占了端口就别再起一份：本地反复跑时省掉每次 ~40s 的 build。 */
const REUSE_RUNNING = !process.env.CI;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  globalSetup: "./tests/e2e/support/global-setup.ts",
  globalTeardown: "./tests/e2e/support/global-teardown.ts",
  use: {
    baseURL: UI_ORIGIN,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  // 只留桌面视口：移动端（Pixel 7）的 Tabs 布局不在本轮验收范围，两个项目会把
  // 用例数翻倍、运行时间翻倍，却提高不了这六条链路的证据强度。
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
  ],
  webServer: [
    {
      // 用 venv 里的 python 直接跑 uvicorn：不经 `uv run`，进程树少一层，
      // 收尾时不会留下孤儿 python。
      command: `"${venvPython(REPO_ROOT)}" -m uvicorn pyknora.api.main:create_app --factory --host 127.0.0.1 --port 8000`,
      cwd: REPO_ROOT,
      url: `${API_ORIGIN}/health`,
      env: backendEnv(),
      reuseExistingServer: REUSE_RUNNING,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `node "${FAKE_UPSTREAM_SCRIPT}"`,
      cwd: WEB_ROOT,
      url: `http://127.0.0.1:${FAKE_CHAT_PORT}/health`,
      env: { FAKE_CHAT_PORT },
      reuseExistingServer: REUSE_RUNNING,
      timeout: 30_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      // `pnpm start` 需要先 build；Next 16 的产物一次 ~35s，给足超时。
      command: "pnpm build && pnpm start",
      cwd: WEB_ROOT,
      url: `${UI_ORIGIN}/login`,
      env: { API_PROXY_TARGET: API_ORIGIN },
      reuseExistingServer: REUSE_RUNNING,
      timeout: 240_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
