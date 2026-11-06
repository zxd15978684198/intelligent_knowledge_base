"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { apiRequest } from "@/lib/api/client";
import { useAuth } from "@/features/auth/auth-context";
import type { MeResponse } from "@/features/auth/api-types";

import { hasPermission, normalizePermissions } from "./permissions";
import { invalidateSession, isUnauthenticated } from "./session";

/**
 * 权限判定的 React 入口。
 *
 * 主路径：直接读 `useAuth().identity.permissions`（`AuthProvider` 登录时已经拉过
 * `/auth/me`，不再重复请求）。
 *
 * 兜底路径：identity 存在但 `permissions` 不是字符串数组（旧快照、外部注入的 identity、
 * 后端将来字段改名）时，本 hook 自己补拉一次 `/auth/me`。补拉失败**不重试**：
 * 401 → 清会话；其他错误 → 空权限（少显示入口，不白屏、不反复打接口）。
 *
 * Cookie 化之后没有「令牌」可判：是否该补拉由 `status === "authenticated"` 决定，
 * 请求本身不带 token（身份在 HttpOnly Cookie 里）。
 */

export interface PermissionsApi {
  /** 当前生效的权限点（只读；可能为空数组）。 */
  permissions: readonly string[];
  /** 权限列表是否已经可信（加载中 / 补拉中为 false，此时不要据此隐藏入口）。 */
  ready: boolean;
  /** 单点判定。 */
  can: (permission: string) => boolean;
  /** 任一命中。 */
  canAny: (permissions: readonly string[]) => boolean;
}

const EMPTY: readonly string[] = [];

export function usePermissions(): PermissionsApi {
  const { identity, status } = useAuth();

  const fromIdentity = useMemo(
    () => normalizePermissions(identity?.permissions),
    [identity],
  );

  const [fetched, setFetched] = useState<readonly string[] | null>(null);

  useEffect(() => {
    // identity 里已经有可信权限列表 → 不请求。
    if (fromIdentity !== null) return;
    // 只有明确「已登录」才值得补拉；loading/anonymous 下补拉只会拿到 401。
    if (status !== "authenticated") return;

    let active = true;
    apiRequest<MeResponse>("/api/v1/auth/me")
      .then((me) => {
        if (!active) return;
        setFetched(normalizePermissions(me.permissions) ?? EMPTY);
      })
      .catch((error: unknown) => {
        if (!active) return;
        if (isUnauthenticated(error)) invalidateSession();
        setFetched(EMPTY);
      });

    return () => {
      active = false;
    };
  }, [fromIdentity, status]);

  const permissions = fromIdentity ?? fetched ?? EMPTY;

  const ready = status === "authenticated" && (fromIdentity !== null || fetched !== null);

  const can = useCallback(
    (permission: string) => hasPermission(permissions, permission),
    [permissions],
  );

  const canAny = useCallback(
    (candidates: readonly string[]) =>
      candidates.some((permission) => hasPermission(permissions, permission)),
    [permissions],
  );

  return useMemo(
    () => ({ permissions, ready, can, canAny }),
    [permissions, ready, can, canAny],
  );
}
