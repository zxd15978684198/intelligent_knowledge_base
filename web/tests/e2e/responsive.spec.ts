import { expect, test } from "@playwright/test";

import { seedEmptyKb } from "./support/seed";
import { uniqueSuffix } from "./support/state";
import { loginThroughUi } from "./support/ui";

/**
 * N7：知识库列表的三档宽度回归（`docs/plans/fronted/前端升级计划.md` §1.3 L4 / §5 A4）。
 *
 * 口径来自 2026-11-04 的 `.tmp-measure-widths.mjs` 真浏览器实测，**不是**修裁切：
 * - `Table` 外层 div 本就 `overflow-x-auto`（`components/ui/table.tsx`），`app-shell` 主列
 *   `min-w-0` 生效，所以 `documentElement.scrollWidth - clientWidth` 在三档全为 0；
 * - 旧账把"滚动容器**内** `<table>` 自身宽度（1117>1024、845>390）"当成了页面被裁，
 *   那是量错对象 → 本轮**不卡片化、不重排**（D2 已排除）；
 * - 真正要固化的是：① 三档页面级零横向溢出；② 表格容器滚到最右后最后一列可到达；
 *   ③ 窄档「可左右滑动」提示可见（宽档由 `xl:hidden` 隐藏）。
 *
 * 用 `setViewportSize` 在同一条用例里遍历三档而不是拆三个 project：playwright.config
 * 只保留 desktop project（移动端 project 已按 ACCEPTANCE.md 删除），viewport 是运行时属性，
 * 一条用例就能把"同一份 DOM 在不同宽度下"的行为连续量出来，也不会多占 worker。
 */

const WIDTHS = [
  { width: 1440, height: 900, label: "桌面宽档", hintVisible: false },
  { width: 1024, height: 768, label: "平板中档", hintVisible: true },
  { width: 390, height: 844, label: "手机窄档", hintVisible: true },
] as const;

test.describe("知识库列表三档宽度", () => {
  test("三档下页面零横向溢出、表格可横向滚动到最后一列、窄档有提示", async ({ page }) => {
    const name = `E2E 宽度 ${uniqueSuffix()}`;
    await seedEmptyKb(name);

    await loginThroughUi(page);
    await expect(page.getByRole("heading", { level: 1, name: "知识库" })).toBeVisible();

    const list = page.getByRole("region", { name: "知识库列表" });
    const table = list.locator("table");
    const scroller = list.locator("div.overflow-x-auto").first();
    const hint = list.getByText(/表格可左右滑动/);
    await expect(table).toBeVisible();
    await expect(scroller).toBeVisible();

    const measurements: string[] = [];

    for (const vp of WIDTHS) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      // 改 viewport 后布局要重排，等表格重新可见再量，避免量到上一档的瞬时几何。
      await expect(table).toBeVisible();

      const pageOverflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(pageOverflow, `${vp.label}（${vp.width}px）页面级横向溢出必须为 0`).toBe(0);

      const geometry = await scroller.evaluate((el) => ({
        clientWidth: el.clientWidth,
        scrollWidth: el.scrollWidth,
        right: el.getBoundingClientRect().right,
      }));
      expect(geometry.clientWidth, `${vp.label} 滚动容器必须有正宽度`).toBeGreaterThan(0);

      // 把容器推到最右，量最后一列（表头「操作」）的右边界是否落在容器内。
      const lastColumnRight = await scroller.evaluate((el) => {
        el.scrollLeft = el.scrollWidth;
        const header = el.querySelector("table thead th:last-child");
        return header ? header.getBoundingClientRect().right : null;
      });
      expect(lastColumnRight, `${vp.label} 表格必须有表头`).not.toBeNull();
      expect(
        (lastColumnRight ?? Number.POSITIVE_INFINITY) - geometry.right,
        `${vp.label} 滚到最右后最后一列必须落在滚动容器内（可到达）`,
      ).toBeLessThanOrEqual(2);

      if (vp.hintVisible) {
        await expect(hint, `${vp.label} 应显示窄屏可发现性提示`).toBeVisible();
      } else {
        await expect(hint, `${vp.label} 表格不溢出，提示应由 xl:hidden 隐藏`).toBeHidden();
      }

      measurements.push(
        [
          `${vp.label}=${vp.width}px`,
          `pageOverflow=${pageOverflow}`,
          `container=${geometry.clientWidth}`,
          `content=${geometry.scrollWidth}`,
          `lastColRight-containerRight=${(lastColumnRight ?? 0) - geometry.right}`,
          `hint=${vp.hintVisible ? "visible" : "hidden"}`,
        ].join(" "),
      );
    }

    await test.info().attach("kb-table-widths.txt", {
      body: measurements.join("\n"),
      contentType: "text/plain",
    });
  });
});
