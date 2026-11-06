import { clearCachedUsername } from "./identity-cache";

/**
 * 会话层的唯一汇合点：广播、失效、判定。
 *
 * Cookie 化之后，访问令牌与刷新令牌都在 HttpOnly Cookie 里，前端读不到。于是：
 * - 「登录态」由服务端 401 + 探活结果说话，本地不再保存任何身份凭据；
 * - 会话失效只做三件事：清登录名缓存、幂等请求登出端点（撤销服务端家族）、
 *   跨标签页广播 `invalidated` 让所有页面一起转 anonymous；
 * - 刷新成功由 `client.ts` 广播 `refreshed`，等待中的标签页据此继续原请求。
 *
 * 本模块**刻意不 import `lib/api/client`**：`invalidateSession()` 直接用原生 `fetch`
 * 发登出，既避免循环依赖，也避免登出请求被 401 单飞逻辑接管（那会自递归）。
 */

/** 跨标签页会话事件。`refreshed` = 刷新成功；`invalidated` = 会话作废，跳登录。 */
export type SessionEvent = "refreshed" | "invalidated";

const CHANNEL_NAME = "pyknora-session";
const CSRF_COOKIE = "pyknora_csrf";
const CSRF_HEADER = "X-CSRF-Token";

/** 从可读 Cookie 里取双提交 CSRF 值。SSR / 无 document 时返回空串。 */
export function readCsrfToken(): string {
  if (typeof document === "undefined" || typeof document.cookie !== "string") return "";
  const prefix = `${CSRF_COOKIE}=`;
  for (const part of document.cookie.split(";")) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      try {
        return decodeURIComponent(trimmed.slice(prefix.length));
      } catch {
        return trimmed.slice(prefix.length);
      }
    }
  }
  return "";
}

let channel: BroadcastChannel | null | undefined;

function sessionChannel(): BroadcastChannel | null {
  if (channel !== undefined) return channel;
  if (typeof BroadcastChannel === "undefined") {
    channel = null;
    return channel;
  }
  try {
    channel = new BroadcastChannel(CHANNEL_NAME);
  } catch {
    // 隐私模式 / 老浏览器：广播不可用，退化为本标签页独立行为。
    channel = null;
  }
  return channel;
}

/** 向其它标签页广播一次会话事件。没有广播能力时静默退化。 */
export function postSession(kind: SessionEvent): void {
  try {
    sessionChannel()?.postMessage(kind);
  } catch {
    // 广播失败不影响本地状态推进。
  }
}

/** 订阅其它标签页的会话事件，返回 unsubscribe。对本标签页自己发的消息不敏感。 */
export function subscribeSessionEvents(listener: (kind: SessionEvent) => void): () => void {
  const ch = sessionChannel();
  if (ch === null) return () => undefined;
  const handler = (event: MessageEvent<unknown>) => {
    if (event.data === "refreshed" || event.data === "invalidated") {
      listener(event.data);
    }
  };
  ch.addEventListener("message", handler);
  return () => ch.removeEventListener("message", handler);
}

/**
 * 会话失效（401 且刷新失败，或用户主动登出）。
 *
 * **保持同步 `void` 签名**：调用方（AuthProvider、client.ts、ChatScreen）不需要 await，
 * 登出请求是 fire-and-forget——浏览器可能在它发出前就跳转，后端登出端点本身是幂等的
 * （无 Cookie 也 204），所以丢一次请求最多留下一个到绝对过期为止的家族行，不构成安全问题。
 */
export function invalidateSession(): void {
  clearCachedUsername();

  const headers: Record<string, string> = { Accept: "application/json" };
  const csrf = readCsrfToken();
  if (csrf !== "") headers[CSRF_HEADER] = csrf;
  try {
    void fetch("/api/v1/auth/logout", {
      method: "POST",
      credentials: "include",
      headers,
    }).catch(() => undefined);
  } catch {
    // fetch 不可用（SSR / 受限测试环境）：本地清理与广播已完成，忽略。
  }
  postSession("invalidated");
}

/**
 * 判断错误是否为「未认证」。
 *
 * 用结构化判定而不是 `instanceof ApiError`：`apiRequest` 抛的一定是 `ApiError`，但
 * 跨 realm（iframe / worker / 测试环境）时 `instanceof` 会失灵；只看 `status`/`code`
 * 既是这里唯一需要的两个字段，也对本模块零依赖（不 import client.ts，避免循环）。
 */
export function isUnauthenticated(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const candidate = error as { status?: unknown; code?: unknown };
  return candidate.status === 401 || candidate.code === "UNAUTHENTICATED";
}
