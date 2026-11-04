"use client";

import type { ReactNode } from "react";

import { usePermissions } from "@/lib/auth/use-permissions";

export interface PermissionGateProps {
  /** 需要的权限点，例如 `document.write`。 */
  permission: string;
  children: ReactNode;
  /** 无权限时显示什么；不传则静默不渲染（默认：不给没权限的人看按钮）。 */
  fallback?: ReactNode;
  /** 权限尚未就绪（首屏 `/auth/me` 未回来）时显示什么；不传则渲染 null。 */
  loading?: ReactNode;
}

/**
 * 按权限决定是否渲染子树。
 *
 * **它只是体验层**：隐藏按钮 ≠ 拒绝请求。服务端 `deps.py` 的权限依赖才是唯一授权入口，
 * 所以这里的 fallback 用 `DisabledReason` 解释原因，而不是假装功能不存在。
 *
 * `loading` 默认渲染 null 而不是 fallback：首屏权限未知时先不画，避免"按钮禁用闪一下又可用"。
 */
export function PermissionGate({ permission, children, fallback, loading }: PermissionGateProps) {
  const { ready, can } = usePermissions();
  if (!ready) return <>{loading ?? null}</>;
  if (!can(permission)) return <>{fallback ?? null}</>;
  return <>{children}</>;
}
