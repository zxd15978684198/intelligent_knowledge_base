import { expect, test } from "@playwright/test";

import { readState } from "./support/state";

/**
 * 验收项 1：`/login` 登录成功 → 跳 `/kbs`（Cookie 会话版）。
 *
 * 会话凭据在 HttpOnly Cookie 里，前端 localStorage **不再**保存任何令牌。断言分三层：
 * 1. 表单真的渲染出来（无 `role=form` 就说明页面没起来）；
 * 2. 登录成功后 URL 变成 `/kbs`，且应用壳的 h1 出现（不是"跳转了但白屏"）；
 * 3. 浏览器拿到 `pyknora_access` 且 httpOnly=true，`document.cookie` 读不到它
 *    （XSS 无法窃取持久令牌）；失败登录则不应建立任何会话 Cookie。
 */
test.describe("登录", () => {
  test("真实账号登录后跳转 /kbs 并种下 HttpOnly 会话 Cookie", async ({ page }) => {
    const state = readState();

    await page.goto("/login");
    await expect(page.getByRole("heading", { level: 1, name: "登录工作台" })).toBeVisible();
    await expect(page.getByRole("form", { name: "登录表单" })).toBeVisible();

    await page.locator('input[name="username"]').fill(state.username);
    await page.locator('input[name="password"]').fill(state.password);
    await page.getByRole("button", { name: "登录", exact: true }).click();

    await page.waitForURL("**/kbs", { timeout: 30_000 });
    await expect(page.getByRole("heading", { level: 1, name: "知识库" })).toBeVisible();

    const cookies = await page.context().cookies();
    const access = cookies.find((cookie) => cookie.name === "pyknora_access");
    expect(access, "后端应下发 pyknora_access 会话 Cookie").toBeDefined();
    expect(access?.httpOnly, "访问令牌必须是 HttpOnly").toBe(true);

    // 页面脚本读不到 HttpOnly Cookie；localStorage 里也不该再有令牌。
    const leaked = await page.evaluate(
      () => document.cookie.includes("pyknora_access=") || window.localStorage.getItem("pyknora.token") !== null,
    );
    expect(leaked, "前端脚本不应能读到访问令牌").toBe(false);
  });

  test("刷新页面仍算登录态（Cookie 持久化，无需 localStorage 令牌）", async ({ page }) => {
    const state = readState();

    await page.goto("/login");
    await page.locator('input[name="username"]').fill(state.username);
    await page.locator('input[name="password"]').fill(state.password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await page.waitForURL("**/kbs", { timeout: 30_000 });

    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "知识库" })).toBeVisible();
    await expect(page).toHaveURL(/\/kbs$/);

    // 刷新后首屏总是打一次 /auth/me：有 Cookie 就验成，不会被弹回 /login。
    const access = (await page.context().cookies()).find((cookie) => cookie.name === "pyknora_access");
    expect(access).toBeDefined();
  });

  test("密码错误时停在 /login 并给出 role=alert，不建立会话 Cookie", async ({ page }) => {
    const state = readState();

    await page.goto("/login");
    await page.locator('input[name="username"]').fill(state.username);
    await page.locator('input[name="password"]').fill("definitely-not-the-password");
    await page.getByRole("button", { name: "登录", exact: true }).click();

    await expect(page.locator('form[aria-label="登录表单"] [role="alert"]')).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
    const cookies = await page.context().cookies();
    expect(cookies.find((cookie) => cookie.name === "pyknora_access"), "登录失败不应下发访问 Cookie").toBeUndefined();
  });
});
