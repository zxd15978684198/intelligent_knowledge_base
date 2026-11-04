import { describe, expect, it, vi } from "vitest";

import "./browser-storage";

import {
  TOKEN_STORAGE_KEY,
  clearToken,
  getToken,
  setToken,
  subscribeToken,
} from "@/lib/auth/token-store";

/**
 * 令牌存储的契约（D4：Bearer 放 localStorage，键 `pyknora.token`）。
 *
 * 这些用例守的是三条容易退化的性质：
 * 1. 键名不许漂移（改了等于把所有人踢下线）；
 * 2. 订阅者只收**后续**变化（回放当前值会让挂载期误判登录态）；
 * 3. 存储抛异常（隐私模式 / 配额满）时降级为内存，**绝不把异常抛给调用方**。
 *
 * `vitest.setup.ts` 的 afterEach 会 `localStorage.clear()`，用例之间不需要手动清。
 */
describe("lib/auth/token-store", () => {
  it("没有写入过时返回 null", () => {
    expect(getToken()).toBeNull();
  });

  it("setToken 写入 pyknora.token 并去掉首尾空白", () => {
    setToken("  tok-1  ");
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBe("tok-1");
    expect(getToken()).toBe("tok-1");
  });

  it("空串等价于 clearToken，不留下第三种状态", () => {
    setToken("tok-1");
    setToken("   ");
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(getToken()).toBeNull();
  });

  it("clearToken 删掉键", () => {
    setToken("tok-1");
    clearToken();
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(getToken()).toBeNull();
  });

  it("subscribeToken 只回放后续变化，取消订阅后不再收到", () => {
    const seen: (string | null)[] = [];
    const unsubscribe = subscribeToken((token) => seen.push(token));

    expect(seen).toEqual([]); // 不回放当前值
    setToken("tok-2");
    clearToken();
    expect(seen).toEqual(["tok-2", null]);

    unsubscribe();
    setToken("tok-3");
    expect(seen).toEqual(["tok-2", null]);
  });

  it("另一个标签页改动 localStorage 时同步给订阅者（含整库 clear）", () => {
    const seen: (string | null)[] = [];
    const unsubscribe = subscribeToken((token) => seen.push(token));

    window.localStorage.setItem(TOKEN_STORAGE_KEY, "tok-other");
    window.dispatchEvent(new StorageEvent("storage", { key: TOKEN_STORAGE_KEY, newValue: "tok-other" }));
    expect(seen).toEqual(["tok-other"]);

    // 另一个标签页调了 localStorage.clear()：事件里 key 为 null。
    window.localStorage.clear();
    window.dispatchEvent(new StorageEvent("storage", { key: null }));
    expect(seen).toEqual(["tok-other", null]);

    unsubscribe();
  });

  it("订阅者抛错不影响令牌写入，也不影响其他订阅者", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const seen: (string | null)[] = [];
    const unsubscribeBad = subscribeToken(() => {
      throw new Error("boom");
    });
    const unsubscribeGood = subscribeToken((token) => seen.push(token));

    try {
      expect(() => setToken("tok-4")).not.toThrow();
      expect(seen).toEqual(["tok-4"]);
      expect(getToken()).toBe("tok-4");
      expect(consoleError).toHaveBeenCalled();
    } finally {
      unsubscribeBad();
      unsubscribeGood();
      consoleError.mockRestore();
    }
  });

  it("localStorage 抛异常（隐私模式/配额满）时降级到内存，不抛给调用方", async () => {
    // 存储可用性是模块级缓存，必须换一份新模块才能重新走探测分支。
    vi.resetModules();
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });

    try {
      const store = await import("@/lib/auth/token-store");
      expect(() => store.setToken("tok-memory")).not.toThrow();
      expect(store.getToken()).toBe("tok-memory");
      expect(() => store.clearToken()).not.toThrow();
      expect(store.getToken()).toBeNull();
    } finally {
      setItem.mockRestore();
      getItem.mockRestore();
      vi.resetModules();
    }
  });
});
