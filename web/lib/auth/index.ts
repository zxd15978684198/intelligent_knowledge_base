/**
 * `@/lib/auth` 的公开出口：**只导出纯函数与存储工具**，不导出 React hook。
 *
 * 原因：`usePermissions`（`./use-permissions.ts`）依赖 `features/auth` 的
 * `useAuth`，属于客户端边界；把它塞进这个桶会让服务端组件 import `hasPermission`
 * 时顺带拉进客户端模块图。客户端侧请从 `@/features/auth` 取 `usePermissions`。
 *
 * 会话模型（Cookie 化之后）：本桶不再导出任何令牌存取函数——访问/刷新令牌在
 * HttpOnly Cookie 里，前端读不到。会话事件、CSRF 值读取、失效统一走 `./session`。
 */

export {
  USERNAME_STORAGE_KEY,
  cacheUsername,
  clearCachedUsername,
  readCachedUsername,
} from "./identity-cache";

export {
  invalidateSession,
  isUnauthenticated,
  postSession,
  readCsrfToken,
  subscribeSessionEvents,
  type SessionEvent,
} from "./session";

export {
  ALL_PERMISSIONS,
  PERMISSIONS,
  PERMISSION_LABELS,
  describePermission,
  hasAnyPermission,
  hasPermission,
  missingPermissions,
  normalizePermissions,
  type Permission,
} from "./permissions";
