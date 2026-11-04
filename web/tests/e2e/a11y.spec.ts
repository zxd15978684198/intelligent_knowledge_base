import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { SUPPORTED_QUESTION } from "./support/fixtures";
import { seedEmptyKb, seedKbWithDoc } from "./support/seed";
import { uniqueSuffix } from "./support/state";
import { loginThroughUi } from "./support/ui";

/**
 * 验收项 6：五个路由的 axe 扫描。
 *
 * 门禁口径（任务书）：`critical` 与 `serious` 必须为 0；`moderate` / `minor` 只记录、不阻塞。
 *
 * 扫描时机是刻意的：
 * - `/kbs`、documents：等列表/表格渲染完，而不是扫骨架屏；
 * - query：先真的跑一次检索，让排名表在 DOM 里（表格是无障碍问题的高发区）；
 * - chat：停在**提问前的空态**——流式输出期间 DOM 每 60ms 变一次，扫描结果不可复现，
 *   与其得一个"有时 critical 有时没有"的结论，不如把扫描对象固定下来（这一点写进报告）。
 *
 * 为什么先扫再查前置条件：扫描是**测量**，前置条件是**对该测量的解释**。先测量，
 * 报告里五个路由就都有数；前置条件失败时用例照样红，且失败信息直接指向根因
 * （例如列表区没渲染 + 页面同时显示「登录状态已失效」= 业务请求缺 Authorization）。
 * 反过来先断言前置条件，只会得到"没扫到"，拿不到任何 a11y 证据。
 */

interface AxeNode {
  /** 命中元素的 selector 链，例如 `#main-content > div > button`。 */
  readonly target: string;
  /** 元素开标签片段，用来判断是哪个控件。 */
  readonly html: string;
  /** axe 给出的修复建议（含对比度数值这类可复核的细节）。 */
  readonly summary: string;
}

interface AxeViolation {
  readonly id: string;
  readonly impact: string;
  readonly nodes: readonly AxeNode[];
}

interface AxeSummary {
  readonly route: string;
  readonly url: string;
  readonly critical: number;
  readonly serious: number;
  readonly moderate: number;
  readonly minor: number;
  readonly violations: readonly AxeViolation[];
}

interface ScanOptions {
  /** 测试名里的路由标识，只用于随包上报。 */
  readonly route: string;
  /** 扫描前后窗外的"页面是否处于验收目标状态"，false 时不改门禁口径，只用于解释数字。 */
  readonly ready?: () => Promise<boolean>;
}

/** 元素摘要里保留的字符数：够认控件，不至于把整段 HTML 打进报告。 */
const HTML_SNIPPET = 140;

function shorten(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= limit ? flat : `${flat.slice(0, limit)}…`;
}

async function scanRoute(page: Page, options: ScanOptions): Promise<AxeSummary> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();

  const count = (impact: string): number =>
    results.violations.filter((violation) => violation.impact === impact).length;

  const summary: AxeSummary = {
    route: options.route,
    url: page.url(),
    critical: count("critical"),
    serious: count("serious"),
    moderate: count("moderate"),
    minor: count("minor"),
    violations: results.violations.map((violation) => ({
      id: violation.id,
      impact: violation.impact ?? "unknown",
      nodes: violation.nodes.map((node) => ({
        target: node.target.join(" "),
        html: shorten(node.html, HTML_SNIPPET),
        summary: node.failureSummary ?? "",
      })),
    })),
  };

  await test.info().attach(`axe-${options.route}.json`, {
    body: JSON.stringify(summary, null, 2),
    contentType: "application/json",
  });

  // 直接打到 stdout：报告里的表抄这几行，元素明细抄下面那几行，不必翻 test-results。
  process.stdout.write(
    `[axe] ${options.route} critical=${summary.critical} serious=${summary.serious} ` +
      `moderate=${summary.moderate} minor=${summary.minor}\n`,
  );
  for (const violation of summary.violations) {
    for (const node of violation.nodes) {
      process.stdout.write(
        `[axe]   ${violation.impact} ${violation.id} ${node.target} :: ${node.summary.replace(/\n/g, " ")}\n`,
      );
    }
  }

  if (options.ready) {
    const ok = await options.ready();
    expect(
      ok,
      `${options.route} 未处于验收目标状态（元素缺失）。若页面同时显示「登录状态已失效」，` +
        "说明业务请求没带 Authorization（见报告 P0-1），下面的 a11y 数字是在降级页面上测的。",
    ).toBe(true);
  }

  const detail = summary.violations
    .map((violation) => `${violation.id}/${violation.impact}×${violation.nodes.length}`)
    .join("，");
  expect(summary.critical, `${options.route} 有 critical 违规：${detail}`).toBe(0);
  expect(summary.serious, `${options.route} 有 serious 违规：${detail}`).toBe(0);
  return summary;
}

test.describe("无障碍扫描", () => {
  let emptyKbId = "";
  let seededKbId = "";

  test.beforeAll(async () => {
    emptyKbId = await seedEmptyKb(`E2E A11y 空库 ${uniqueSuffix()}`);
    const seeded = await seedKbWithDoc(`E2E A11y 检索 ${uniqueSuffix()}`);
    seededKbId = seeded.kbId;
  });

  test("/login", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { level: 1, name: "登录工作台" })).toBeVisible();
    await scanRoute(page, { route: "/login" });
  });

  test("/kbs", async ({ page }) => {
    await loginThroughUi(page);
    await scanRoute(page, {
      route: "/kbs",
      ready: async () => page.getByRole("region", { name: "知识库列表" }).isVisible(),
    });
  });

  test("/kbs/{kbId}/documents", async ({ page }) => {
    await loginThroughUi(page);
    await page.goto(`/kbs/${emptyKbId}/documents`);
    await expect(page.getByRole("heading", { level: 1, name: "文档" })).toBeVisible();
    await scanRoute(page, {
      route: "/kbs/{kbId}/documents",
      // 空库会同时给出上传面板与空表提示，扫的是"没有文档时"这一态。
      ready: async () => page.getByRole("region", { name: "上传文档" }).isVisible(),
    });
  });

  test("/kbs/{kbId}/query", async ({ page }) => {
    await loginThroughUi(page);
    await page.goto(`/kbs/${seededKbId}/query`);
    await page.getByLabel("检索问题").fill(SUPPORTED_QUESTION);
    await page.getByRole("button", { name: "运行检索" }).click();
    await page.locator('[data-testid="retrieval-row"]').first().waitFor({ state: "visible", timeout: 20_000 }).catch(() => undefined);

    await scanRoute(page, {
      route: "/kbs/{kbId}/query",
      ready: async () => (await page.locator('[data-testid="retrieval-row"]').count()) > 0,
    });
  });

  test("/kbs/{kbId}/chat", async ({ page }) => {
    await loginThroughUi(page);
    await page.goto(`/kbs/${seededKbId}/chat`);
    await expect(page.getByRole("heading", { level: 1, name: "问答" })).toBeVisible();
    await scanRoute(page, {
      route: "/kbs/{kbId}/chat",
      ready: async () => page.getByLabel("问题").isVisible(),
    });
  });
});
