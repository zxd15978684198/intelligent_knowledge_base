import { readStored, removeStored, writeStored } from "./storage";

/**
 * 访问令牌存取（D4：Bearer 令牌放 localStorage，键名 `pyknora.token`）。
 *
 * 选 localStorage 而不是 httpOnly Cookie 是已定的决策（同源代理 + 前端直取令牌），
 * 代价是 XSS 可读。因此这里只做三件事：存取、通知、跨标签页同步——
 * **不**在这里做校验、解码或过期判断（令牌是否有效由服务端 401 说了算）。
 *
 * 401 的全局清理路径：任何调用方捕获到 `ApiError.status === 401` 后调用
 * `clearToken()`（或 `invalidateSession()`），所有订阅者会同步到 anonymous。
 * 本模块不发请求、不重试。
 */

/** localStorage 键名；测试与排障时用它，不要另抄一份字面量。 */
export const TOKEN_STORAGE_KEY = "pyknora.token";

export type TokenListener = (token: string | null) => void;

const listeners = new Set<TokenListener>();

/** 当前令牌；没有、存储不可用、或值为空串时返回 `null`。 */
export function getToken(): string | null {
  const token = readStored(TOKEN_STORAGE_KEY);
  if (token === null) return null;
  const trimmed = token.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * 写入令牌并通知订阅者。
 *
 * 传空串等价于 `clearToken()`：避免把 `""` 当成"已登录但没有令牌"这种第三种状态。
 */
export function setToken(token: string): void {
  const normalized = token.trim();
  if (normalized.length === 0) {
    clearToken();
    return;
  }
  writeStored(TOKEN_STORAGE_KEY, normalized);
  emit(normalized);
}

/** 清除令牌（登出、401、令牌损坏）并通知订阅者。 */
export function clearToken(): void {
  removeStored(TOKEN_STORAGE_KEY);
  emit(null);
}

/**
 * 订阅令牌变化，返回取消订阅函数。
 *
 * 语义：**只收后续变化**，不回放当前值——初始值请显式调用 `getToken()`，
 * 否则订阅者拿到的第一帧是"上一次"的值，容易在挂载期误判登录态。
 * 跨标签页的登录/登出通过 `storage` 事件同步过来。
 */
export function subscribeToken(listener: TokenListener): () => void {
  listeners.add(listener);
  attachCrossTabSync();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) detachCrossTabSync();
  };
}

function emit(token: string | null): void {
  // 复制一份再遍历：订阅者在回调里取消订阅是正常用法，直接遍历 Set 会漏掉后面的订阅者。
  for (const listener of [...listeners]) {
    try {
      listener(token);
    } catch (error) {
      // 订阅者（通常是 React 状态更新）抛错不能打断令牌写入——
      // 否则一次渲染异常会让"登录成功但令牌没落盘"。
      console.error("[auth] token listener failed", error);
    }
  }
}

function handleStorageEvent(event: StorageEvent): void {
  // key === null 表示另一个标签页调了 localStorage.clear()。
  if (event.key !== null && event.key !== TOKEN_STORAGE_KEY) return;
  emit(getToken());
}

let crossTabAttached = false;

function attachCrossTabSync(): void {
  if (crossTabAttached || typeof window === "undefined") return;
  window.addEventListener("storage", handleStorageEvent);
  crossTabAttached = true;
}

function detachCrossTabSync(): void {
  if (!crossTabAttached || typeof window === "undefined") return;
  window.removeEventListener("storage", handleStorageEvent);
  crossTabAttached = false;
}
