import { expect, test } from "@playwright/test";

import { SUPPORTED_QUESTION } from "./support/fixtures";
import { seedKbWithDoc } from "./support/seed";
import { uniqueSuffix } from "./support/state";
import { loginThroughUi } from "./support/ui";

/**
 * 验收项 4：`/kbs/{kbId}/query` 输入查询 → 排名表出现。
 *
 * 文档由 API 布置并等到作业 succeeded——检索用例要验的是"检索控制台能不能把结果
 * 摆出来"，不是上传链路（那条由 documents 用例负责）。问句与文档的重叠二元组在
 * `support/fixtures.ts` 里有说明，换词会让 baseline 策略召回不到东西。
 */
test.describe("检索调试", () => {
  test("输入查询后出现排名表与命中摘要", async ({ page }) => {
    const { kbId } = await seedKbWithDoc(`E2E 检索 ${uniqueSuffix()}`);

    await loginThroughUi(page);
    await page.goto(`/kbs/${kbId}/query`);
    await expect(page.getByRole("heading", { level: 1, name: "检索调试" })).toBeVisible();

    await page.getByLabel("检索问题").fill(SUPPORTED_QUESTION);
    await page.getByRole("button", { name: "运行检索" }).click();

    const rows = page.locator('[data-testid="retrieval-row"]');
    await expect(rows.first()).toBeVisible({ timeout: 30_000 });
    expect(await rows.count()).toBeGreaterThan(0);

    await expect(page.getByRole("table", { name: "检索结果" })).toBeVisible();
    await expect(page.locator('[data-testid="query-summary"]')).toBeVisible();
    // 摘要里必须回显问句，证明渲染的是本次结果而不是上一次的残留。
    await expect(page.locator('[data-testid="query-summary"]')).toContainText(SUPPORTED_QUESTION);
    // 命中的片段正文应出现在表里（fixtures 的那句话）。
    await expect(page.getByRole("table", { name: "检索结果" })).toContainText("保险丝");
  });
});
