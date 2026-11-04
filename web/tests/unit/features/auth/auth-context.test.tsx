import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import "./browser-storage";

import { ApiError } from "@/lib/api/client";
import { TOKEN_STORAGE_KEY } from "@/lib/auth/token-store";
import { USERNAME_STORAGE_KEY } from "@/lib/auth/identity-cache";

import { AuthProvider, useAuth } from "@/features/auth/auth-context";

import { server } from "@/tests/msw/server";

import {
  ACCESS_TOKEN,
  ME_RESPONSE,
  TOKEN_RESPONSE,
  UNAUTHENTICATED_BODY,
  WORKSPACE_ID,
} from "./fixtures";

/**
 * `AuthProvider` / `useAuth` 的行为契约。
 *
 * 这里的断言都在守**状态机**，不是在测实现细节：
 * 1. 没有令牌 → anonymous，且不发 `/auth/me`（省一次必然失败的往返）；
 * 2. 登录成功 → 令牌落盘 + 身份（含 permissions / workspace）来自 `/auth/me`；
 * 3. 登录 401 → 不写令牌、状态不动、只打一次请求（**不重试**）；
 * 4. 刷新 → 用本地令牌验活；401 清令牌并转 anonymous，同样只打一次；
 * 5. 非 401 的验活失败（网络）→ 保留令牌但转 anonymous，并给出可读原因。
 */

function AuthProbe() {
  const { status, token, identity, sessionError, login, register, logout } = useAuth();
  const [failure, setFailure] = useState("-");

  const capture = (promise: Promise<void>): void => {
    void promise.catch((error: unknown) => {
      setFailure(error instanceof ApiError ? `${error.status}:${error.code}` : String(error));
    });
  };

  return (
    <div>
      <p data-testid="status">{status}</p>
      <p data-testid="token">{token ?? "-"}</p>
      <p data-testid="username">{identity?.username ?? "-"}</p>
      <p data-testid="workspace">{identity?.default_workspace_id ?? "-"}</p>
      <p data-testid="permissions">{identity === null ? "-" : identity.permissions.join(",")}</p>
      <p data-testid="session-error">{sessionError ?? "-"}</p>
      <p data-testid="failure">{failure}</p>
      <button type="button" onClick={() => capture(login("alice", "password123"))}>
        login
      </button>
      <button
        type="button"
        onClick={() =>
          capture(register({ username: "alice", password: "password123", email: "alice@example.com" }))
        }
      >
        register
      </button>
      <button type="button" onClick={logout}>
        logout
      </button>
    </div>
  );
}

function renderAuth() {
  return render(
    <AuthProvider>
      <AuthProbe />
    </AuthProvider>,
  );
}

const text = (id: string): string => screen.getByTestId(id).textContent ?? "";

describe("features/auth/auth-context", () => {
  it("没有令牌时直接进入 anonymous，且不请求 /auth/me", async () => {
    let meCalls = 0;
    server.use(
      http.get("*/api/v1/auth/me", () => {
        meCalls += 1;
        return HttpResponse.json(ME_RESPONSE);
      }),
    );

    renderAuth();
    await waitFor(() => expect(text("status")).toBe("anonymous"));
    expect(meCalls).toBe(0);
    expect(text("token")).toBe("-");
  });

  it("登录成功：令牌写入 localStorage，身份与权限来自 /auth/me", async () => {
    let sentBody: unknown = null;
    let authorization: string | null = null;
    server.use(
      http.post("*/api/v1/auth/login", async ({ request }) => {
        sentBody = await request.json();
        return HttpResponse.json(TOKEN_RESPONSE);
      }),
      http.get("*/api/v1/auth/me", ({ request }) => {
        authorization = request.headers.get("authorization");
        return HttpResponse.json(ME_RESPONSE);
      }),
    );

    renderAuth();
    await waitFor(() => expect(text("status")).toBe("anonymous"));
    fireEvent.click(screen.getByRole("button", { name: "login" }));
    await waitFor(() => expect(text("status")).toBe("authenticated"));

    expect(sentBody).toEqual({ username: "alice", password: "password123" });
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBe(ACCESS_TOKEN);
    expect(window.localStorage.getItem(USERNAME_STORAGE_KEY)).toBe("alice");
    expect(authorization).toBe(`Bearer ${ACCESS_TOKEN}`);
    expect(text("token")).toBe(ACCESS_TOKEN);
    expect(text("username")).toBe("alice");
    expect(text("workspace")).toBe(WORKSPACE_ID);
    expect(text("permissions")).toBe("document.read,document.write");
    expect(text("session-error")).toBe("-");
  });

  it("登录 401：不写令牌、状态保持 anonymous，且只打一次请求", async () => {
    let loginCalls = 0;
    let meCalls = 0;
    server.use(
      http.post("*/api/v1/auth/login", () => {
        loginCalls += 1;
        return HttpResponse.json(UNAUTHENTICATED_BODY, { status: 401 });
      }),
      http.get("*/api/v1/auth/me", () => {
        meCalls += 1;
        return HttpResponse.json(ME_RESPONSE);
      }),
    );

    renderAuth();
    await waitFor(() => expect(text("status")).toBe("anonymous"));
    fireEvent.click(screen.getByRole("button", { name: "login" }));
    await waitFor(() => expect(text("failure")).toBe("401:UNAUTHENTICATED"));

    expect(loginCalls).toBe(1);
    expect(meCalls).toBe(0);
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(text("token")).toBe("-");
    expect(text("status")).toBe("anonymous");
  });

  it("刷新：已有令牌先验活，成功则恢复身份（username 取本地缓存）", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, ACCESS_TOKEN);
    window.localStorage.setItem(USERNAME_STORAGE_KEY, "alice");
    server.use(http.get("*/api/v1/auth/me", () => HttpResponse.json(ME_RESPONSE)));

    renderAuth();
    await waitFor(() => expect(text("status")).toBe("authenticated"));

    expect(text("token")).toBe(ACCESS_TOKEN);
    expect(text("username")).toBe("alice");
    expect(text("permissions")).toBe("document.read,document.write");
  });

  it("刷新后 401：清掉本地令牌并转 anonymous，不自动重试", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, ACCESS_TOKEN);
    let meCalls = 0;
    server.use(
      http.get("*/api/v1/auth/me", () => {
        meCalls += 1;
        return HttpResponse.json(UNAUTHENTICATED_BODY, { status: 401 });
      }),
    );

    renderAuth();
    await waitFor(() => expect(text("status")).toBe("anonymous"));

    expect(meCalls).toBe(1);
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(text("token")).toBe("-");
  });

  it("刷新后网络失败：保留令牌但转 anonymous，并给出可读原因", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, ACCESS_TOKEN);
    server.use(http.get("*/api/v1/auth/me", () => HttpResponse.error()));

    renderAuth();
    await waitFor(() => expect(text("status")).toBe("anonymous"));

    // 一次网络抖动不该把令牌删掉（用户下次打开可能就好了）。
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBe(ACCESS_TOKEN);
    expect(text("session-error")).toContain("无法连接服务");
  });

  it("logout：清掉令牌、身份与登录名缓存", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, ACCESS_TOKEN);
    window.localStorage.setItem(USERNAME_STORAGE_KEY, "alice");
    server.use(http.get("*/api/v1/auth/me", () => HttpResponse.json(ME_RESPONSE)));

    renderAuth();
    await waitFor(() => expect(text("status")).toBe("authenticated"));
    fireEvent.click(screen.getByRole("button", { name: "logout" }));

    await waitFor(() => expect(text("status")).toBe("anonymous"));
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(window.localStorage.getItem(USERNAME_STORAGE_KEY)).toBeNull();
    expect(text("token")).toBe("-");
    expect(text("username")).toBe("-");
  });
});
