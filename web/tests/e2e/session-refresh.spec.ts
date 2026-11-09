import { expect, test } from "@playwright/test";

import { readState } from "./support/state";

/**
 * 验收项：跨标签页「只刷新一次」。
 *
 * M3 后端对 refresh token 做**单次轮换 + 重用检测**：同一串被消费两次就撤销整个
 * 会话家族。因此 M4 前端必须保证两个标签页并发收到 401 时，全局只放出一个
 * `POST /auth/refresh`（模块级 single-flight + Web Locks 选主 + BroadcastChannel
 * 广播）。否则第二个刷新会拿旧串去撞重用检测，把两个标签页一起踢下线。
 *
 * 这个用例刻意**不 mock 业务接口**：靠删掉 `pyknora_access`（保留 refresh 与
 * 可读的 csrf Cookie）让首屏 `/auth/me` 真实返回 401，走真实后端的轮换；只在
 * `/auth/refresh` 这一跳上计数并加一点延迟，把并发窗口撑开，使「抢锁 / 等对端」
 * 这条分支一定被走到。
 */
test.describe("跨标签页会话刷新", () => {
  test("两个标签页并发 401 全局只放出一个刷新请求，且两页都保持登录", async ({
    page,
    context,
  }) => {
    const state = readState();

    // 1) 主标签页登录，拿到三件套 Cookie。
    await page.goto("/login");
    await page.locator('input[name="username"]').fill(state.username);
    await page.locator('input[name="password"]').fill(state.password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await page.waitForURL("**/kbs", { timeout: 30_000 });
    await expect(page.getByRole("heading", { level: 1, name: "知识库" })).toBeVisible();

    // 2) 第二个标签页共享同一个 BrowserContext 的 Cookie jar。
    const second = await context.newPage();
    await second.goto("/kbs");
    await expect(second.getByRole("heading", { level: 1, name: "知识库" })).toBeVisible();

    // 3) 计数 + 撑开单飞窗口：首个刷新在真正转发前停 400ms，
    //    保证另一页的 401 落在「锁已被占用」的时间段里。
    const refreshCalls: string[] = [];
    await context.route("**/api/v1/auth/refresh", async (route) => {
      refreshCalls.push(route.request().url());
      await new Promise((resolve) => setTimeout(resolve, 400));
      await route.continue();
    });

    // 4) 只删访问令牌：csrf Cookie 仍在 → hasSessionHint() 为真 → 触发单飞刷新；
    //    refresh Cookie 仍在 → 后端能真实轮换。
    await context.clearCookies({ name: "pyknora_access" });

    // 5) 两页几乎同时重载，各自首屏 requestMe() 都会吃到 401。
    await Promise.all([
      page.reload({ waitUntil: "domcontentloaded" }),
      second.reload({ waitUntil: "domcontentloaded" }),
    ]);

    // 6) 两页都应当自愈回登录态；若发生第二次刷新，家族会被撤销而弹回 /login。
    await expect(page.getByRole("heading", { level: 1, name: "知识库" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(second.getByRole("heading", { level: 1, name: "知识库" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page).toHaveURL(/\/kbs$/);
    await expect(second).toHaveURL(/\/kbs$/);

    expect(refreshCalls.length, "跨标签页并发 401 应只放出一个 /auth/refresh").toBe(1);

    await second.close();
  });
});
