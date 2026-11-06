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
import {
  invalidateSession,
  isUnauthenticated,
  subscribeSessionEvents,
} from "@/lib/auth/session";
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
  identity: Identity | null;
  status: AuthStatus;
  login: (username: string, password: string) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => void;
  /**
   * 首屏探活时**非 401** 失败的原因（网络断开 / 5xx / 403 非本区成员）。
   *
   * `status` 仍是 `"anonymous"`：前端不假装已登录。之所以要单独一个字段，
   * 是因为页面需要区分「从没登录过」和「上次登录态这次没验成」——后者要提示用户
   * 重新登录的原因。401 不产生原因：那是正常未登录。
   */
  sessionError: string | null;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** 探活。Cookie 会话下不带 `token`：后端从 HttpOnly Cookie 解身份。 */
function requestMe(): Promise<MeResponse> {
  return apiRequest<MeResponse>(ME_ENDPOINT);
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

/**
 * 登录/注册响应体仍保留 `access_token`（命令行 / 评测脚本从响应体取令牌的兼容通道）。
 * 但浏览器**不**把它存进本地存储：Cookie 已由后端在 201/200 响应里下发。
 * 这里只做「响应体非空」的契约校验，值本身随即丢弃。
 */
function assertTokenResponse(response: TokenResponse): void {
  if (response.access_token.trim().length === 0) {
    // 200 却没有令牌 = 契约被破坏。静默当成功会让后续每个请求都 401，不如当场炸给日志。
    throw new Error("登录响应缺少 access_token");
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentityState] = useState<Identity | null>(null);
  const [status, setStatusState] = useState<AuthStatus>("loading");
  const [sessionError, setSessionError] = useState<string | null>(null);

  /**
   * 登录/注册统一收尾：Cookie 已在后端响应里下发，这里只探活建身份。
   *
   * 为什么必须先验活：`TokenResponse` 不含权限，而 `AuthGuard`/`PermissionGate`
   * 全靠 `identity.permissions` 决定渲染什么。若先把 status 置成 authenticated 再拉
   * `/me`，会出现「已登录但没有任何权限」的瞬间，所有按钮闪一下禁用态。
   */
  const completeSignIn = useCallback(
    async (response: TokenResponse, submittedUsername: string): Promise<void> => {
      assertTokenResponse(response);
      setStatusState("loading");
      cacheUsername(submittedUsername);
      try {
        const me = await requestMe();
        setIdentityState(buildIdentity(me, submittedUsername));
        setSessionError(null);
        setStatusState("authenticated");
      } catch (error: unknown) {
        invalidateSession();
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
    // 幂等登出端点会撤销服务端会话家族；广播让所有标签页一起转 anonymous。
    invalidateSession();
    setIdentityState(null);
    setSessionError(null);
    setStatusState("anonymous");
  }, []);

  // 跨标签页：其它标签页登出 / 刷新失败时，本页立刻失效（不留「已登录」空壳）。
  // 刷新成功（`refreshed`）不改动本页状态——它的 401 单飞已自行重放原请求。
  useEffect(() => {
    return subscribeSessionEvents((kind) => {
      if (kind === "invalidated") {
        setIdentityState(null);
        setSessionError(null);
        setStatusState("anonymous");
      }
    });
  }, []);

  // 首屏**总是**探活一次：有会话 Cookie 就能验成，没有就 401 → anonymous。
  // 不再读本地令牌，登录态唯一真值源是服务端 `/auth/me`。401 不重试。
  useEffect(() => {
    let cancelled = false;
    setStatusState("loading");
    requestMe()
      .then((me) => {
        if (cancelled) return;
        setIdentityState(buildIdentity(me, readCachedUsername()));
        setSessionError(null);
        setStatusState("authenticated");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (isUnauthenticated(error)) {
          // 401 是正常「未登录」。不调 invalidateSession()：那会向其它标签页广播
          // 一次登出，而这里只是「本页没有会话」，不是「会话被作废」。
          setIdentityState(null);
          setSessionError(null);
          setStatusState("anonymous");
          return;
        }
        // 非 401：不假装已登录，但保留原因文案（网络抖动不把用户判成从未登录）。
        setIdentityState(null);
        setStatusState("anonymous");
        setSessionError(describeAuthError(error, "session"));
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ identity, status, login, register, logout, sessionError }),
    [identity, status, login, register, logout, sessionError],
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
