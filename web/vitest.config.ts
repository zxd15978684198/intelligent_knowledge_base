import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

/**
 * 前端测试分两层（计划 D10）：
 * - tests/unit：纯函数、hook、组件（Testing Library + MSW 假接口）
 * - tests/contract：契约层（Zod 判别、SSE 解析器、分页适配）
 * 浏览器主链路在 tests/e2e（Playwright），不在这里跑。
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["tests/unit/**/*.test.{ts,tsx}", "tests/contract/**/*.test.{ts,tsx}"],
    exclude: ["tests/e2e/**", "node_modules/**", ".next/**"],
    css: false,
    restoreMocks: true,
    clearMocks: true,
    reporters: ["default"],
  },
});
