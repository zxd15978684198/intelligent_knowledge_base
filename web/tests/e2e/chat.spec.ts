import { expect, test } from "@playwright/test";

import { SUPPORTED_QUESTION } from "./support/fixtures";
import { seedKbWithDoc } from "./support/seed";
import { uniqueSuffix } from "./support/state";
import { loginThroughUi } from "./support/ui";

/**
 * 验收项 5：`/kbs/{kbId}/chat` 发问 → 流式回答出现并收敛到 done。
 *
 * 上游是 `support/fake-chat-upstream.mjs`（OpenAI 兼容 SSE），它把答案拆成三帧发：
 * `电源接反会` / `导致保险丝熔断` / `，请先断电再更换同规格保险丝。`
 * 所以断言最终正文等于三帧拼起来的结果，就同时证明了"逐帧到达并被追加"，
 * 而不是一次性替换。状态机收敛用 `article[data-phase="done"]` 判定
 * （`ChatPhase = idle|retrieving|streaming|done|aborted|errored`）。
 */
test.describe("问答", () => {
  test("发问后出现流式回答并收敛到已完成", async ({ page }) => {
    const { kbId } = await seedKbWithDoc(`E2E 问答 ${uniqueSuffix()}`);

    await loginThroughUi(page);
    await page.goto(`/kbs/${kbId}/chat`);
    await expect(page.getByRole("heading", { level: 1, name: "问答" })).toBeVisible();

    await page.getByLabel("问题").fill(SUPPORTED_QUESTION);
    await page.getByRole("button", { name: "发送", exact: true }).click();

    const turn = page.locator("article[data-turn-id]");
    await expect(turn).toHaveCount(1, { timeout: 30_000 });

    const answer = page.getByTestId("answer-body");
    // 三帧的拼接结果：只有把分帧都追加进来才会出现完整句子。
    await expect(answer).toContainText("电源接反会导致保险丝熔断，请先断电再更换同规格保险丝。", {
      timeout: 30_000,
    });

    // 收敛到终态：不再有 streaming 的回合，且状态徽标给出完成与支撑判定。
    await expect(page.locator('article[data-phase="done"]')).toHaveCount(1, { timeout: 60_000 });
    await expect(turn).toContainText("已完成");
    await expect(turn).toContainText("证据支持");

    // 发送按钮回到可用形态，说明流已关闭（按钮在流进行中是「停止」）。
    await expect(page.getByRole("button", { name: "发送", exact: true })).toBeVisible();
  });
});
