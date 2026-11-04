import { expect, test } from "@playwright/test";

import { UPLOAD_FILE, UPLOAD_FILENAME } from "./support/fixtures";
import { seedEmptyKb } from "./support/seed";
import { uniqueSuffix } from "./support/state";
import { loginThroughUi } from "./support/ui";

/**
 * 验收项 3：`/kbs/{kbId}/documents` 上传文件 → 202 → 轮询到 succeeded。
 *
 * 这条链路跨了三个进程：浏览器 → Next 代理 → FastAPI（入队）→ worker（解析/切片/向量）
 * → 浏览器轮询作业状态。断言按真实状态机写，不写"期望值"：
 * - 先要到 202 的受理文案（`已受理（202），作业已入队。`）和 `job_id` 驱动的 JobPoller；
 * - 再等 `[data-status="succeeded"]`（终态徽标），以及轮询停止文案；
 * - 最后回到文档列表确认这份文件已经可检索（`可检索` 列 = 是）。
 */
test.describe("文档上传与索引", () => {
  test("上传 txt → 202 → 轮询到 succeeded → 列表可检索", async ({ page }) => {
    const kbId = await seedEmptyKb(`E2E 上传 ${uniqueSuffix()}`);

    await loginThroughUi(page);
    await page.goto(`/kbs/${kbId}/documents`);
    await expect(page.getByRole("heading", { level: 1, name: "文档" })).toBeVisible();

    await page.locator("#document-upload-input").setInputFiles(UPLOAD_FILE);
    await expect(page.getByRole("button", { name: "开始上传" })).toBeVisible();
    await page.getByRole("button", { name: "开始上传" }).click();

    // 202 分支：受理文案 + 作业轮询器上线。
    await expect(page.getByText("已受理（202），作业已入队。", { exact: false })).toBeVisible({
      timeout: 30_000,
    });

    // 作业从 pending/processing 收敛到 succeeded；JobBadge 用 data-status 暴露状态。
    await expect(page.locator('[data-status="succeeded"]')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("已停止轮询（终态）")).toBeVisible();

    // 列表刷新后这份文件必须在，且「可检索」为是。
    const row = page.getByRole("row").filter({ hasText: UPLOAD_FILENAME });
    await expect(row).toBeVisible({ timeout: 20_000 });
    await expect(row).toContainText("是");
  });
});
