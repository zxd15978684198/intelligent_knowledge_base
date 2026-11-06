import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import "./browser-storage";

import { ApiError } from "@/lib/api/client";
import { USERNAME_STORAGE_KEY } from "@/lib/auth/identity-cache";

import { AuthProvider, useAuth } from "@/features/auth/auth-context";

import { server } from "@/tests/msw/server";

import { ME_RESPONSE, TOKEN_RESPONSE, UNAUTHENTICATED_BODY, WORKSPACE_ID } from "./fixtures";

/**
 * `AuthProvider` / `useAuth` 的行为契约（Cookie 会话版）。
 *
 * 会话凭据在 HttpOnly Cookie 里，前端读不到也不保存令牌；登录态唯一真值源是
 * 服务端 `/auth/me`。这里钉住五件事：
 * 1. 首屏**总是**探活一次 `/auth/me`：401 → anonymous（正常未登录），不发多余请求；
 * 2. 登录成功 → 后端下发 Cookie，前端再打一次 `/auth/me` 建身份，登录名进本地缓存；
 * 3. 登录 401 → 不验活、状态保持 anonymous、只打一次登录请求（不重试）；
 * 4. 首屏验活成功（有会话 Cookie）→ 恢复身份，username 取本地缓存；
 * 5. 首屏网络失败 → anonymous + 可读原因；logout → 清缓存与身份。
 */

function AuthProbe() {
  const { status, identity, sessionError, login, register, logout } = useAuth();
  const [failure, setFailure] = useState("-");

  const capture = (promise: Promise<void>): void => {
    void promise.catch((error: unknown) => {
      setFailure(error instanceof ApiError ? `${error.status}:${error.code}` : String(error));
    });
  };

  return (
    <div>
      <p data-testid="status">{status}</p>
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
  beforeEach(() => {
    // 默认：没有会话（首屏探活 401）。各用例按需要覆盖成 200。
    server.use(
      http.get("*/api/v1/auth/me", () => HttpResponse.json(UNAUTHENTICATED_BODY, { status: 401 })),
      http.post("*/api/v1/auth/logout", () => new HttpResponse(null, { status: 204 })),
    );
  });

  it("首屏无会话：总是打一次 /auth/me，401 后进入 anonymous", async () => {
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
    expect(text("session-error")).toBe("-");
  });

  it("登录成功：Cookie 会话 + /auth/me 建身份，登录名写入本地缓存", async () => {
    let sentBody: unknown = null;
    let meAuthorization: string | null = "sentinel";
    let signedIn = false;
    server.use(
      http.post("*/api/v1/auth/login", async ({ request }) => {
        sentBody = await request.json();
        signedIn = true;
        return HttpResponse.json(TOKEN_RESPONSE);
      }),
      http.get("*/api/v1/auth/me", ({ request }) => {
        meAuthorization = request.headers.get("authorization");
        return signedIn
          ? HttpResponse.json(ME_RESPONSE)
          : HttpResponse.json(UNAUTHENTICATED_BODY, { status: 401 });
      }),
    );

    renderAuth();
    await waitFor(() => expect(text("status")).toBe("anonymous"));
    fireEvent.click(screen.getByRole("button", { name: "login" }));
    await waitFor(() => expect(text("status")).toBe("authenticated"));

    expect(sentBody).toEqual({ username: "alice", password: "password123" });
    expect(window.localStorage.getItem(USERNAME_STORAGE_KEY)).toBe("alice");
    // /auth/me 省略 token → 走 Cookie 会话，不发 Authorization 头。
    expect(meAuthorization).toBeNull();
    expect(text("username")).toBe("alice");
    expect(text("workspace")).toBe(WORKSPACE_ID);
    expect(text("permissions")).toBe("document.read,document.write");
    expect(text("session-error")).toBe("-");
  });

  it("登录 401：不验活、状态保持 anonymous，且只打一次登录请求", async () => {
    let loginCalls = 0;
    let meCalls = 0;
    server.use(
      http.post("*/api/v1/auth/login", () => {
        loginCalls += 1;
        return HttpResponse.json(UNAUTHENTICATED_BODY, { status: 401 });
      }),
      http.get("*/api/v1/auth/me", () => {
        meCalls += 1;
        return HttpResponse.json(UNAUTHENTICATED_BODY, { status: 401 });
      }),
    );

    renderAuth();
    await waitFor(() => expect(text("status")).toBe("anonymous"));
    fireEvent.click(screen.getByRole("button", { name: "login" }));
    await waitFor(() => expect(text("failure")).toBe("401:UNAUTHENTICATED"));

    expect(loginCalls).toBe(1);
    // 只有首屏那一次探活；登录失败后再没有额外的 /auth/me。
    expect(meCalls).toBe(1);
    expect(text("status")).toBe("anonymous");
  });

  it("首屏验活成功（已有会话 Cookie）：恢复身份，username 取本地缓存", async () => {
    window.localStorage.setItem(USERNAME_STORAGE_KEY, "alice");
    server.use(http.get("*/api/v1/auth/me", () => HttpResponse.json(ME_RESPONSE)));

    renderAuth();
    await waitFor(() => expect(text("status")).toBe("authenticated"));

    expect(text("username")).toBe("alice");
    expect(text("permissions")).toBe("document.read,document.write");
  });

  it("首屏网络失败：转 anonymous 并给出可读原因", async () => {
    server.use(http.get("*/api/v1/auth/me", () => HttpResponse.error()));

    renderAuth();
    await waitFor(() => expect(text("status")).toBe("anonymous"));

    expect(text("session-error")).toContain("无法连接服务");
  });

  it("logout：清掉登录名缓存与身份，状态转 anonymous", async () => {
    window.localStorage.setItem(USERNAME_STORAGE_KEY, "alice");
    server.use(http.get("*/api/v1/auth/me", () => HttpResponse.json(ME_RESPONSE)));

    renderAuth();
    await waitFor(() => expect(text("status")).toBe("authenticated"));
    fireEvent.click(screen.getByRole("button", { name: "logout" }));

    await waitFor(() => expect(text("status")).toBe("anonymous"));
    expect(window.localStorage.getItem(USERNAME_STORAGE_KEY)).toBeNull();
    expect(text("username")).toBe("-");
  });
});
