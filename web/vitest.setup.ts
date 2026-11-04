import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeAll, afterAll, vi } from "vitest";
import { server } from "./tests/msw/server";

/**
 * 统一测试环境：
 * - MSW 默认 `onUnhandledRequest: "error"`，任何没声明的请求都会让用例失败——
 *   这是刻意的：前端最容易出的问题是"调了一个后端没有的接口"，静默放行会漏掉。
 * - localStorage / matchMedia / scrollTo 这三样 jsdom 缺失或与浏览器行为不同，
 *   在这里补成确定性桩，避免每个用例各写一份。
 */

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));

/**
 * jsdom 27 的 Web Storage 依赖 Node 的 `--localstorage-file` 实验开关；
 * 本机没开这个开关时 `window.localStorage` 是 `undefined`（`sessionStorage` 正常）。
 * 不兜底的话，下面那行 `.clear()` 会让**每一个** unit 用例都抛
 * `TypeError: Cannot read properties of undefined (reading 'clear')`。
 * 测试不关心持久化语义，缺 localStorage 时直接拿 sessionStorage 顶上，保证确定性。
 */
if (!window.localStorage) {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: window.sessionStorage,
  });
}

afterEach(() => {
  server.resetHandlers();
  cleanup();
  window.localStorage?.clear();
});

afterAll(() => server.close());

if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

if (!window.HTMLElement.prototype.scrollIntoView) {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
}
