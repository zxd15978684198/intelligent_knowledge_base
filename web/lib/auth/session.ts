import { ApiError } from "@/lib/api/client";
import { clearCachedUsername } from "./identity-cache";
import { clearToken } from "./token-store";

/**
 * 会话失效的唯一汇合点。
 *
 * 前端只有一个信号能证明令牌已死：服务端回 **401**。收到 401 之后要做的事是固定的
 * （清令牌 + 清登录名缓存 + 通知所有订阅者转 anonymous），所以把它收敛成一个函数，
 * 而不是让每个功能模块各写一遍——写漏一处就会出现"页面还在显示登录态、请求全 401"。
 *
 * 谁来调用：捕获到 401 的最近一层。目前是 `AuthProvider`（挂载验活）与
 * `usePermissions`（补拉 `/auth/me`）。N2 的 `apiRequest` 若做统一拦截，应当调这里；
 * 本次不越界改 `lib/api/client.ts`。
 *
 * **不重试**：401 重试没有意义（同一令牌再发一次还是 401），重登是用户的动作。
 */
export function invalidateSession(): void {
  clearCachedUsername();
  clearToken();
}

/**
 * 判断错误是否为"未认证"。
 *
 * 先看 `ApiError.status`（HTTP 层），再退一步看 `code`——错误信封里的
 * `UNAUTHENTICATED` 与 401 是绑定的，但代理层也可能给出没有 status 的包装错误。
 */
export function isUnauthenticated(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.status === 401) return true;
  return error.code === "UNAUTHENTICATED";
}
