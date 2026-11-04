import { expect, test } from "@playwright/test";

import { readState } from "./support/state";

/**
 * 验收项 1：`/login` 登录成功 → 跳 `/kbs`。
 *
 * 断言分三层，任何一层坏了都能立刻定位：
 * 1. 表单真的渲染出来（无 `role=form` 就说明页面没起来）；
 * 2. 登录成功后 URL 变成 `/kbs`，且应用壳的 h1 出现（说明不是"跳转了但白屏"）；
 * 3. 令牌落进 `localStorage`（token-store 的契约），刷新后仍算登录态。
 */
test.describe("登录", () => {
  test("真实账号登录后跳转 /kbs 并进入应用", async ({ page }) => {
    const state = readState();

    await page.goto("/login");
    await expect(page.getByRole("heading", { level: 1, name: "登录工作台" })).toBeVisible();
    await expect(page.getByRole("form", { name: "登录表单" })).toBeVisible();

    await page.locator('input[name="username"]').fill(state.username);
    await page.locator('input[name="password"]').fill(state.password);
    await page.getByRole("button", { name: "登录", exact: true }).click();

    await page.waitForURL("**/kbs", { timeout: 30_000 });
    await expect(page.getByRole("heading", { level: 1, name: "知识库" })).toBeVisible();

    const token = await page.evaluate(() => window.localStorage.getItem("pyknora.token"));
    expect(token, "登录成功后 token-store 应把令牌写进 localStorage").not.toBeNull();
  });

  test("密码错误时停在 /login 并给出 role=alert", async ({ page }) => {
    const state = readState();

    await page.goto("/login");
    await page.locator('input[name="username"]').fill(state.username);
    await page.locator('input[name="password"]').fill("definitely-not-the-password");
    await page.getByRole("button", { name: "登录", exact: true }).click();

    await expect(page.locator('form[aria-label="登录表单"] [role="alert"]')).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
    const token = await page.evaluate(() => window.localStorage.getItem("pyknora.token"));
    expect(token, "登录失败不应写入令牌").toBeNull();
  });
});
