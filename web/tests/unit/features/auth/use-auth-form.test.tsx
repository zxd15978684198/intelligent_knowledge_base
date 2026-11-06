import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "./browser-storage";

import { AuthProvider, useAuth } from "@/features/auth/auth-context";
import { useAuthForm, type AuthMode } from "@/features/auth/use-auth-form";

import { server } from "@/tests/msw/server";

import { CONFLICT_BODY, ME_RESPONSE, TOKEN_RESPONSE, UNAUTHENTICATED_BODY } from "./fixtures";

/**
 * `useAuthForm` 的行为契约（登录页的行为核心）。
 *
 * 跳转用 `router.replace`：登录后再按后退不该回到登录页。这里把 `next/navigation`
 * 整个替换成假实现，断言"跳到哪里、跳了几次"，不关心 Next 内部。
 *
 * Cookie 会话版：前端不写本地令牌，登录成功的判据是 status=authenticated +
 * router.replace('/kbs')。首屏总是打一次 /auth/me（默认无会话 → 401）。
 */
const routerMock = vi.hoisted(() => ({
  replace: vi.fn(),
  push: vi.fn(),
  refresh: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
  prefetch: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  usePathname: () => "/login",
  useSearchParams: () => new URLSearchParams(),
}));

function Form({ mode }: { mode: AuthMode }) {
  const { values, errors, formError, pending, setField, submit } = useAuthForm(mode);
  const { status } = useAuth();
  const messages = [errors.username, errors.email, errors.password, formError].filter(
    (message): message is string => typeof message === "string",
  );

  return (
    <form aria-label="probe" noValidate onSubmit={submit}>
      <label htmlFor="username">用户名</label>
      <input
        id="username"
        value={values.username}
        onChange={(event) => setField("username", event.target.value)}
      />

      {mode === "register" ? (
        <>
          <label htmlFor="email">邮箱</label>
          <input
            id="email"
            value={values.email}
            onChange={(event) => setField("email", event.target.value)}
          />
        </>
      ) : null}

      <label htmlFor="password">密码</label>
      <input
        id="password"
        type="password"
        value={values.password}
        onChange={(event) => setField("password", event.target.value)}
      />

      {messages.length === 0 ? null : <p role="alert">{messages.join(" / ")}</p>}

      <p data-testid="status">{status}</p>
      <p data-testid="pending">{pending ? "yes" : "no"}</p>
      <button type="submit">提交</button>
    </form>
  );
}

function renderForm(mode: AuthMode) {
  return render(
    <AuthProvider>
      <Form mode={mode} />
    </AuthProvider>,
  );
}

function fill(fields: { username?: string; email?: string; password?: string }): void {
  if (fields.username !== undefined) {
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: fields.username } });
  }
  if (fields.email !== undefined) {
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: fields.email } });
  }
  if (fields.password !== undefined) {
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: fields.password } });
  }
}

function submit(): void {
  fireEvent.submit(screen.getByRole("form", { name: "probe" }));
}

const text = (id: string): string => screen.getByTestId(id).textContent ?? "";

describe("features/auth/use-auth-form", () => {
  beforeEach(() => {
    routerMock.replace.mockClear();
    routerMock.push.mockClear();
    // 首屏总是探活：默认没有会话（401）。成功登录的用例会在用例内覆盖成 200。
    server.use(
      http.get("*/api/v1/auth/me", () => HttpResponse.json(UNAUTHENTICATED_BODY, { status: 401 })),
      http.post("*/api/v1/auth/logout", () => new HttpResponse(null, { status: 204 })),
    );
  });

  it("登录成功：status=authenticated 并 router.replace('/kbs')", async () => {
    let signedIn = false;
    server.use(
      http.post("*/api/v1/auth/login", () => {
        signedIn = true;
        return HttpResponse.json(TOKEN_RESPONSE);
      }),
      http.get("*/api/v1/auth/me", () =>
        signedIn
          ? HttpResponse.json(ME_RESPONSE)
          : HttpResponse.json(UNAUTHENTICATED_BODY, { status: 401 }),
      ),
    );

    renderForm("login");
    fill({ username: "alice", password: "password123" });
    submit();

    await waitFor(() => expect(routerMock.replace).toHaveBeenCalledWith("/kbs"));
    expect(routerMock.push).not.toHaveBeenCalled();
    expect(text("status")).toBe("authenticated");
    expect(text("pending")).toBe("no");
  });

  it("登录 401：显示『用户名或密码错误』，不跳转、状态保持 anonymous", async () => {
    server.use(
      http.post("*/api/v1/auth/login", () =>
        HttpResponse.json(UNAUTHENTICATED_BODY, { status: 401 }),
      ),
    );

    renderForm("login");
    fill({ username: "alice", password: "wrong-password" });
    submit();

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("用户名或密码错误"),
    );
    expect(routerMock.replace).not.toHaveBeenCalled();
    expect(text("status")).toBe("anonymous");
  });

  it("注册 409：显示『该用户名或邮箱已被注册』，不跳转", async () => {
    server.use(
      http.post("*/api/v1/auth/register", () => HttpResponse.json(CONFLICT_BODY, { status: 409 })),
    );

    renderForm("register");
    fill({ username: "alice", email: "alice@example.com", password: "password123" });
    submit();

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("该用户名或邮箱已被注册"),
    );
    expect(routerMock.replace).not.toHaveBeenCalled();
  });

  it("注册密码不足 8 位：本地拦截，一个请求都不发", async () => {
    let registerCalls = 0;
    server.use(
      http.post("*/api/v1/auth/register", () => {
        registerCalls += 1;
        return HttpResponse.json(TOKEN_RESPONSE);
      }),
    );

    renderForm("register");
    fill({ username: "alice", email: "alice@example.com", password: "short" });
    submit();

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("密码至少 8 位"));
    expect(registerCalls).toBe(0);
    expect(routerMock.replace).not.toHaveBeenCalled();
  });

  it("用户名为空：给出字段级提示，不发请求", async () => {
    let loginCalls = 0;
    server.use(
      http.post("*/api/v1/auth/login", () => {
        loginCalls += 1;
        return HttpResponse.json(TOKEN_RESPONSE);
      }),
    );

    renderForm("login");
    fill({ password: "password123" });
    submit();

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("请输入用户名"));
    expect(loginCalls).toBe(0);
  });
});
