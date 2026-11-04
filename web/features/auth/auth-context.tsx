"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { apiRequest } from "@/lib/api/client";
import { cacheUsername, readCachedUsername } from "@/lib/auth/identity-cache";
import { normalizePermissions } from "@/lib/auth/permissions";
import { invalidateSession, isUnauthenticated } from "@/lib/auth/session";
import { getToken, setToken, subscribeToken } from "@/lib/auth/token-store";
import { shortId } from "@/lib/utils";

import type { MeResponse, TokenResponse } from "./api-types";
import { AuthInputError, describeAuthError } from "./errors";
import { firstFieldError, validateLogin, validateRegister } from "./validation";

const LOGIN_ENDPOINT = "/api/v1/auth/login";
const REGISTER_ENDPOINT = "/api/v1/auth/register";
const ME_ENDPOINT = "/api/v1/auth/me";

/**
 * 已登录身份。
 *
 * 字段映射说明（后端 `/auth/me` 的真实形状见 `src/pyknora/api/v1/auth.py`）：
 * - `default_workspace_id` ← `MeResponse.workspace_id`（后端不叫 default_*）
 * - `permissions` ← `MeResponse.permissions`（**唯一**授权来源，前端只做展示开关）
 * - `username` ← `/auth/me` 不返回登录名，只能取自登录/注册时缓存的输入
 *   （见 `lib/auth/identity-cache.ts`）；刷新后读缓存，缓存丢了退到 `display_name`。
 */
export interface Identity {
  principal_id: string;
  username: string;
  default_workspace_id: string;
  permissions: string[];
  /** 后端 `display_name`，可以是 null。 */
  display_name: string | null;
  /** 后端角色名（如 `viewer` / `editor` / `admin`），仅用于展示。 */
  role: string;
}

export type AuthStatus = "loading" | "authenticated" | "anonymous";

export interface RegisterInput {
  username: string;
  password: string;
  /** 后端 `RegisterRequest.email` 是**必填**；为 null/undefined 时本地直接拒绝，不发必 422 的请求。 */
  email?: string;
}

export interface AuthContextValue {
  token: string | null;
  identity: Identity | null;
  status: AuthStatus;
  login: (username: string, password: string) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => void;
  /**
   * 刷新页面验活时**非 401** 失败的原因（网络断开 / 5xx / 403 非本区成员）。
   *
   * `status` 仍是 `"anonymous"`：前端不假装已登录。之所以要单独一个字段，
   * 是因为页面需要区分"从没登录过"和"上次登录态这次没验成"——后者要提示用户
   * 重新登录的原因（例如令牌所属工作区已失效）。401 不产生原因：那是正常未登录。
   */
  sessionError: string | null;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function requestMe(token: string): Promise<MeResponse> {
  return apiRequest<MeResponse>(ME_ENDPOINT, { token });
}

/** `/auth/me` + 已知登录名 → `Identity`。`usernameHint` 为空时逐级兜底。 */
function buildIdentity(me: MeResponse, usernameHint: string | null): Identity {
  const hint = usernameHint?.trim() ?? "";
  const displayName = me.display_name?.trim() ?? "";
  let username = hint;
  if (username.length === 0) username = displayName;
  if (username.length === 0) username = `principal-${shortId(me.principal_id)}`;
  return {
    principal_id: me.principal_id,
    username,
    default_workspace_id: me.workspace_id,
    permissions: normalizePermissions(me.permissions) ?? [],
    display_name: me.display_name,
    role: me.role,
  };
}

function readAccessToken(response: TokenResponse): string {
  const accessToken = response.access_token.trim();
  if (accessToken.length === 0) {
    // 200 却没有令牌 = 契约被破坏。静默当成功会让后续每个请求都 401，不如当场炸给日志。
    throw new Error("登录响应缺少 access_token");
  }
  return accessToken;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(null);
  const [identity, setIdentityState] = useState<Identity | null>(null);
  const [status, setStatusState] = useState<AuthStatus>("loading");
  const [sessionError, setSessionError] = useState<string | null>(null);

  /**
   * 令牌到手后的统一收尾：先落盘再验活，`/auth/me` 成功才算登录完成。
   *
   * 为什么必须先验活：`TokenResponse` 不含权限，而 `AuthGuard`/`PermissionGate`
   * 全靠 `identity.permissions` 决定渲染什么。若先把 status 置成 authenticated 再拉
   * `/me`，会出现"已登录但没有任何权限"的瞬间，所有按钮闪一下禁用态。
   * 反之若 `/me` 失败，把刚落盘的令牌回滚掉并 rethrow——不留"有令牌无身份"的幽灵态。
   */
  const completeSignIn = useCallback(
    async (response: TokenResponse, submittedUsername: string): Promise<void> => {
      const accessToken = readAccessToken(response);
      setStatusState("loading");
      setToken(accessToken);
      cacheUsername(submittedUsername);
      try {
        const me = await requestMe(accessToken);
        setTokenState(accessToken);
        setIdentityState(buildIdentity(me, submittedUsername));
        setSessionError(null);
        setStatusState("authenticated");
      } catch (error: unknown) {
        invalidateSession();
        setTokenState(null);
        setIdentityState(null);
        setStatusState("anonymous");
        throw error;
      }
    },
    [],
  );

  const login = useCallback(
    async (username: string, password: string): Promise<void> => {
      const trimmed = username.trim();
      const invalid = firstFieldError(validateLogin({ username: trimmed, password }));
      if (invalid !== null) throw new AuthInputError(invalid.field, invalid.message);

      const response = await apiRequest<TokenResponse>(LOGIN_ENDPOINT, {
        method: "POST",
        // `apiRequest` 的 `body` 收普通对象并自己 JSON.stringify（传字符串会被再序列化一层）。
        body: { username: trimmed, password },
        token: null,
      });
      await completeSignIn(response, trimmed);
    },
    [completeSignIn],
  );

  const register = useCallback(
    async (input: RegisterInput): Promise<void> => {
      const username = input.username.trim();
      const email = (input.email ?? "").trim();
      const invalid = firstFieldError(validateRegister({ username, password: input.password, email }));
      if (invalid !== null) throw new AuthInputError(invalid.field, invalid.message);

      const response = await apiRequest<TokenResponse>(REGISTER_ENDPOINT, {
        method: "POST",
        body: { username, email, password: input.password },
        token: null,
      });
      await completeSignIn(response, username);
    },
    [completeSignIn],
  );

  const logout = useCallback((): void => {
    // 后端没有 logout 端点（JWT 无状态），登出 = 丢令牌 + 清缓存。
    invalidateSession();
    setTokenState(null);
    setIdentityState(null);
    setSessionError(null);
    setStatusState("anonymous");
  }, []);

  // 跨标签页 / 程序内登出：令牌一旦被清掉，身份立刻失效（不留着"已登录"的空壳）。
  useEffect(() => {
    return subscribeToken((next) => {
      setTokenState(next);
      if (next === null) {
        setIdentityState(null);
        setSessionError(null);
        setStatusState("anonymous");
      }
    });
  }, []);

  // 首屏：有令牌就先验活。401 → 清令牌转 anonymous，且**不重试**（重试只会再 401 一次）。
  useEffect(() => {
    let cancelled = false;
    const stored = getToken();
    if (stored === null) {
      setStatusState("anonymous");
      return () => {
        cancelled = true;
      };
    }

    setTokenState(stored);
    setStatusState("loading");
    requestMe(stored)
      .then((me) => {
        if (cancelled) return;
        setIdentityState(buildIdentity(me, readCachedUsername()));
        setSessionError(null);
        setStatusState("authenticated");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (isUnauthenticated(error)) {
          invalidateSession();
          setTokenState(null);
          setIdentityState(null);
          setStatusState("anonymous");
          return;
        }
        // 非 401：令牌**不删**（它可能仍然有效，只是这次没验成），但不假装已登录。
        // 清掉的话，一次网络抖动就会把用户踢下线并把令牌弄丢，重连后还得重新输密码。
        setIdentityState(null);
        setStatusState("anonymous");
        setSessionError(describeAuthError(error, "session"));
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ token, identity, status, login, register, logout, sessionError }),
    [token, identity, status, login, register, logout, sessionError],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth 必须在 <AuthProvider> 内使用");
  }
  return context;
}
