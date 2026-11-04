/**
 * jsdom 27.4 在这台机器上**没有** `window.localStorage`。
 *
 * 原因不在我们：jsdom 27 的 storage 实现依赖 Node 的 `--localstorage-file`
 * 实验特性，Node 未开该 flag 时 `window.localStorage` 直接是 `undefined`，
 * 于是 `vitest.setup.ts` 的 `afterEach(() => window.localStorage.clear())`
 * 会在**每个**用例后抛 `TypeError: Cannot read properties of undefined`。
 *
 * 这里只在缺失时补一个浏览器语义的 `localStorage`：直接复用 jsdom 的
 * `sessionStorage` 实例（同一个 `Storage` WebIDL 实现，`setItem/getItem/clear/key/length`
 * 行为与浏览器一致，`Storage.prototype` 的 spy 也照常生效）。用例之间由
 * `vitest.setup.ts` 的 `clear()` 隔离，互不影响；唯一代价是 localStorage 与
 * sessionStorage 共用同一份数据，而这几个用例只碰 `pyknora.*` 键，没有冲突。
 *
 * 真实浏览器（有 localStorage）里这个模块什么也不做，所以它不是被测代码的一部分。
 */
function installFallbackLocalStorage(): void {
  if (typeof window === "undefined") return;

  const existing: Storage | undefined = window.localStorage;
  if (existing !== undefined) return;

  const scoped: Storage | undefined = window.sessionStorage;
  if (scoped === undefined) {
    throw new Error("jsdom 同时缺少 localStorage 与 sessionStorage，测试环境无法提供 Web Storage");
  }

  Object.defineProperty(window, "localStorage", {
    configurable: true,
    enumerable: false,
    get: () => scoped,
  });
}

installFallbackLocalStorage();
