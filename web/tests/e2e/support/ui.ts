/**
 * 浏览器侧的共用动作。
 *
 * 只用**真实交互**（填表、点按钮），不注入 localStorage、不 mock 接口：登录这一步
 * 本身就是要验收的东西，绕过它等于把用例的证据一起绕过。
 */

import type { Page } from "@playwright/test";

import { readState } from "./state";

/**
 * 用真实表单登录，停在 `/kbs`。
 *
 * 选择器用 `name` 而不是 label 文案：label 上带必填标记（`用户名` + 标记），
 * 文案会随 UI 微调变化，而 `name` 是提交契约的一部分，变了登录本身就不通。
 */
export async function loginThroughUi(page: Page): Promise<void> {
  const state = readState();

  await page.goto("/login");
  await page.locator('input[name="username"]').fill(state.username);
  await page.locator('input[name="password"]').fill(state.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();

  // 登录成功后由 useAuthForm 做 router.replace("/kbs")；等 URL 比等 DOM 稳。
  await page.waitForURL("**/kbs", { timeout: 30_000 });
}
