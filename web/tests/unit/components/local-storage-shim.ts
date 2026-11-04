/**
 * 本地测试垫片：修复 N0 `web/vitest.setup.ts:19` 的 `window.localStorage.clear()`。
 *
 * Node 26 在 `globalThis` 上定义了实验性 `localStorage` getter（没有 `--localstorage-file`
 * 时返回 `undefined` 并告警），Vitest 的 jsdom 环境把 `window` 指向 `globalThis`，
 * 于是 jsdom 自带的 Storage 被遮蔽，`afterEach` 里的 `window.localStorage.clear()`
 * 直接抛 `TypeError: Cannot read properties of undefined (reading 'clear')`，
 * 导致**所有**测试文件都失败（与被测组件无关）。
 *
 * `vitest.setup.ts` 属于 N0 的写入范围，N3 不改；这里在测试模块加载期补一个内存 Storage，
 * 让全局钩子能跑完。N0 修好后本文件可删：
 *   if (typeof window.localStorage?.clear === "function") window.localStorage.clear();
 *
 * 注意：必须在每个测试文件里作为**第一个** import 引入（ESM 按书写顺序求值）。
 */

class MemoryStorage implements Storage {
  private readonly store = new Map<string, string>();

  get length(): number {
    return this.store.size;
  }

  clear(): void {
    this.store.clear();
  }

  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }

  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

const current = globalThis.localStorage;

if (typeof current?.clear !== "function") {
  Object.defineProperty(globalThis, "localStorage", {
    value: new MemoryStorage(),
    configurable: true,
    enumerable: false,
  });
}
