/**
 * localStorage 的安全读写层：令牌与登录名缓存共用。
 *
 * 必须挡住的三种情况（否则一次异常就能把整页打挂）：
 * 1. **SSR**：服务端渲染时没有 `window`，所以本模块**不允许**在模块级触碰它，
 *    所有探测都延迟到函数调用时。
 * 2. **隐私模式 / 第三方 iframe 禁存储**：`window.localStorage` 的 getter 本身就可能抛
 *    `SecurityError`，`setItem` 也可能抛 `QuotaExceededError`。
 * 3. **配额写满**：写失败不能让登录流程整体失败——降级到进程内 Map，本次会话仍可用
 *    （刷新后必然丢失，这是明知代价的降级，不假装持久化成功）。
 *
 * 探测只做一次（写一个探针键再删掉），结果缓存。探测失败后不再重试，
 * 避免每次读令牌都触发一次异常。
 */

const PROBE_KEY = "__pyknora_probe__";

type Availability = "unknown" | "available" | "unavailable";

let availability: Availability = "unknown";

/** 存储不可用时的兜底。**只在真实存储读不到时**参与读写，避免出现两份真相。 */
const memory = new Map<string, string>();

function nativeStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  if (availability === "unavailable") return null;
  if (availability === "available") {
    try {
      return window.localStorage;
    } catch {
      availability = "unavailable";
      return null;
    }
  }
  try {
    const storage = window.localStorage;
    storage.setItem(PROBE_KEY, "1");
    storage.removeItem(PROBE_KEY);
    availability = "available";
    return storage;
  } catch {
    availability = "unavailable";
    return null;
  }
}

/** 读一个键；不存在返回 `null`（不区分"没有"与"读不到"，调用方都不该崩）。 */
export function readStored(key: string): string | null {
  const storage = nativeStorage();
  if (storage === null) return memory.get(key) ?? null;
  try {
    return storage.getItem(key);
  } catch {
    return memory.get(key) ?? null;
  }
}

/** 写一个键；写入失败只降级到内存，不抛错。 */
export function writeStored(key: string, value: string): void {
  const storage = nativeStorage();
  if (storage === null) {
    memory.set(key, value);
    return;
  }
  try {
    storage.setItem(key, value);
  } catch {
    memory.set(key, value);
  }
}

/** 删一个键；存储不可用时只删内存副本。 */
export function removeStored(key: string): void {
  memory.delete(key);
  const storage = nativeStorage();
  if (storage === null) return;
  try {
    storage.removeItem(key);
  } catch {
    // 内存副本已经删掉；持久层拒绝删除时没有更好的补偿手段。
  }
}
