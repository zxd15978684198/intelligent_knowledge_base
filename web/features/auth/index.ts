/**
 * `@/features/auth` 公共入口——N4（应用外壳/守卫）、N6+（各业务页）都从这里 import。
 *
 * 刻意**不写** `"use client"`：本文件只是 re-export，真正的客户端边界在各模块自己
 * 的 `"use client"` 指令上。在桶文件加指令会让 `AFTER_LOGIN_PATH` 这类纯常量也被
 * 当成客户端导出，服务端组件引用时会出问题。
 *
 * 稳定面（改了要通知依赖方）：
 * - `useAuth` 的返回形状（identity / status / login / register / logout，另有 sessionError）
 *   ——不再有 token：会话凭据在 HttpOnly Cookie 里，登录态由 status 唯一表达。
 * - `Identity` 的字段（principal_id / username / default_workspace_id / permissions + display_name / role）
 * - `PermissionGate` / `DisabledReason` / `usePermissions` 的 props 与返回值
 */
export { AuthProvider, useAuth } from "./auth-context";
export type { AuthContextValue, AuthStatus, Identity, RegisterInput } from "./auth-context";

export { AuthForm } from "./components/auth-form";
export type { AuthFormProps } from "./components/auth-form";
export { DisabledReason } from "./components/disabled-reason";
export type { DisabledReasonProps } from "./components/disabled-reason";
export { PermissionGate } from "./components/permission-gate";
export type { PermissionGateProps } from "./components/permission-gate";

export { useAuthForm } from "./use-auth-form";
export type { AuthFormController, AuthFormValues, AuthMode } from "./use-auth-form";

export { AFTER_LOGIN_PATH, LOGIN_PATH } from "./routes";
export { AuthInputError, describeAuthError } from "./errors";
export type { AuthField } from "./errors";
export { usePermissions } from "@/lib/auth/use-permissions";
export type { PermissionsApi } from "@/lib/auth/use-permissions";
