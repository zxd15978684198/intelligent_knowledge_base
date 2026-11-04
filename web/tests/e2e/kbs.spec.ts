import { expect, test } from "@playwright/test";

import { seedEmptyKb } from "./support/seed";
import { uniqueSuffix } from "./support/state";
import { loginThroughUi } from "./support/ui";

/**
 * 验收项 2：`/kbs` 列表渲染 + 新建知识库对话框提交成功。
 *
 * 两条用例刻意分工：列表用例的数据**用 API 布置**（只验收渲染），
 * 新建用例的数据**只能从界面产生**（验收对话框这条链路本身）。
 */
test.describe("知识库", () => {
  test("列表渲染出已有知识库与加载计数", async ({ page }) => {
    const name = `E2E 列表 ${uniqueSuffix()}`;
    await seedEmptyKb(name);

    await loginThroughUi(page);
    await expect(page.getByRole("heading", { level: 1, name: "知识库" })).toBeVisible();

    const list = page.getByRole("region", { name: "知识库列表" });
    await expect(list.getByRole("link", { name })).toBeVisible();
    await expect(page.getByText(/已加载 \d+ 个知识库/)).toBeVisible();
  });

  test("新建知识库对话框提交后列表出现新库", async ({ page }) => {
    await loginThroughUi(page);
    const name = `E2E 对话框 ${uniqueSuffix()}`;

    await page.getByRole("button", { name: "新建知识库" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("heading", { name: "新建知识库" })).toBeVisible();

    await dialog.getByLabel("名称").fill(name);
    // 后端进程跑在 PYKNORA_EMBEDDING_BACKEND=hashing 上，模型 key 必须是 test/hashing，
    // 否则能建成库、但 worker 侧 check_profile_compat 会拒绝入库（作业失败）。
    await dialog.getByLabel("嵌入模型").fill("test/hashing");
    // 向量维度 / 切片长度 / 切片重叠 沿用对话框默认（512 / 512 / 64），与 hashing 后端一致。

    await dialog.getByRole("button", { name: "创建", exact: true }).click();

    await expect(dialog).toBeHidden({ timeout: 20_000 });
    // 对话框成功后只关窗、不跳页；列表靠 invalidate 刷新，所以这里等的是列表里的链接。
    await expect(page.getByRole("link", { name })).toBeVisible({ timeout: 20_000 });

    await page.getByRole("link", { name }).click();
    await expect(page).toHaveURL(new RegExp(`/kbs/[^/]+/documents$`));
    await expect(page.getByRole("heading", { level: 1, name: "文档" })).toBeVisible();
  });
});
