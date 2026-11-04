import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import type { ReactElement, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { UploadPanel } from "@/features/documents/UploadPanel";
import { server } from "@/tests/msw/server";
import {
  FIXTURE_DOC_ID,
  FIXTURE_JOB_ID,
  FIXTURE_KB_ID,
  FIXTURE_VERSION_ID,
  FIXTURE_WS_ID,
  sampleJobProcessing,
} from "@/tests/fixtures/entities";

/**
 * 上传面板的四个分支（计划 §5「上传分支」）：
 * 202 → 轮询到 succeeded 并立刻停；200 + deduped → **不轮询**；
 * 作业 failed → 展示 error_code；上传中 → 不确定进度条（没有百分比）。
 *
 * `next/link` 需要 App Router 上下文，测试里换成普通 `<a>`：这里断言的是文案与分支，
 * 不是 Next 的预取行为。
 */

vi.mock("next/link", async () => {
  const React = await import("react");
  return {
    default: ({ href, children }: { href: string; children?: ReactNode }) =>
      React.createElement("a", { href }, children),
  };
});

function renderPanel(): void {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <UploadPanel kbId={FIXTURE_KB_ID} workspaceId={FIXTURE_WS_ID} />
    </QueryClientProvider>
  );
  render(ui);
}

/** 选文件 → 点"开始上传"。上传按钮在选中文件后才出现。 */
async function pickAndUpload(filename = "手册.md"): Promise<void> {
  const user = userEvent.setup();
  const input = screen.getByLabelText("选择要上传的文档");
  await user.upload(input, new File(["# 手册\n\n正文"], filename, { type: "text/markdown" }));
  await user.click(screen.getByRole("button", { name: "开始上传" }));
}

function acceptedBody(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    document_id: FIXTURE_DOC_ID,
    deduped: false,
    job_id: FIXTURE_JOB_ID,
    document_version_id: null,
    status: "pending",
    ...overrides,
  };
}

describe("features/documents/UploadPanel", () => {
  it("202 受理后按 1.5s 轮询；作业 succeeded 时展示成功并停止轮询", async () => {
    let jobCalls = 0;
    let idempotencyKey: string | null = null;
    let contentType: string | null = null;

    server.use(
      http.post("*/api/v1/knowledge-bases/:kbId/documents", ({ request }) => {
        // 契约：multipart + Idempotency-Key（documents.py:166-170）。
        idempotencyKey = request.headers.get("idempotency-key");
        contentType = request.headers.get("content-type");
        return HttpResponse.json(acceptedBody({}), { status: 202 });
      }),
      http.get("*/api/v1/jobs/:jobId", () => {
        jobCalls += 1;
        const succeeded = jobCalls >= 2;
        return HttpResponse.json({
          ...sampleJobProcessing,
          status: succeeded ? "succeeded" : "processing",
          attempt: jobCalls,
          terminal: succeeded,
        });
      }),
    );

    renderPanel();
    await pickAndUpload();

    await screen.findByText(/已受理（202）/);
    await waitFor(() => expect(screen.getByText("成功")).toBeTruthy(), { timeout: 5000 });

    // 客户端不能强设 JSON Content-Type：`apiUpload` 把 FormData 交给浏览器，边界串由浏览器生成。
    // 注意：jsdom 的 FormData 与 Node 的 fetch 跨 realm，undici 认不出它会退成 text/plain，
    // 所以这里只断言"没有设成 application/json"（真实浏览器里是 multipart/form-data）。
    expect(idempotencyKey).toBeTruthy();
    expect(contentType ?? "").not.toContain("application/json");

    expect(jobCalls).toBe(2);
    expect(screen.getByText("已停止轮询（终态）")).toBeTruthy();

    // 终态后不再轮询：再等一个间隔，请求数必须还是 2。
    await new Promise((resolve) => setTimeout(resolve, 1800));
    expect(jobCalls).toBe(2);
  }, 20000);

  it("200 + deduped 时不创建轮询，明确说明内容已存在", async () => {
    let jobCalls = 0;

    server.use(
      http.post("*/api/v1/knowledge-bases/:kbId/documents", () =>
        HttpResponse.json(
          acceptedBody({ deduped: true, job_id: null, document_version_id: FIXTURE_VERSION_ID, status: "duplicate" }),
          { status: 200 },
        ),
      ),
      http.get("*/api/v1/jobs/:jobId", () => {
        jobCalls += 1;
        return HttpResponse.json(sampleJobProcessing);
      }),
    );

    renderPanel();
    await pickAndUpload();

    await screen.findByText(/没有新建作业/);
    expect(jobCalls).toBe(0);
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByText(/已受理（202）/)).toBeNull();
  }, 10000);

  it("作业 failed 时展示后端 error_code 与失败说明", async () => {
    server.use(
      http.post("*/api/v1/knowledge-bases/:kbId/documents", () =>
        HttpResponse.json(acceptedBody({}), { status: 202 }),
      ),
      http.get("*/api/v1/jobs/:jobId", () =>
        HttpResponse.json({
          ...sampleJobProcessing,
          status: "failed",
          terminal: true,
          attempt: 1,
          error_code: "PARSE_FAILED",
        }),
      ),
    );

    renderPanel();
    await pickAndUpload("坏文件.pdf");

    await screen.findByText("PARSE_FAILED");
    // sr-only 的描述里也有同一句话，所以用 allBy。
    expect(screen.getAllByText(/作业失败，文档没有发布/).length).toBeGreaterThan(0);
    expect(screen.getByText("失败")).toBeTruthy();
  }, 10000);

  it("上传中显示不确定进度（无 aria-valuenow、无百分比）", async () => {
    server.use(
      http.post("*/api/v1/knowledge-bases/:kbId/documents", async () => {
        await delay(600);
        return HttpResponse.json(acceptedBody({}), { status: 202 });
      }),
    );

    renderPanel();
    await pickAndUpload();

    const bar = await screen.findByRole("progressbar", { name: "上传进度（不确定）" });
    expect(bar.getAttribute("aria-valuenow")).toBeNull();
    expect(screen.getByText(/进度未知/)).toBeTruthy();
    expect(screen.queryByText(/%/)).toBeNull();
  }, 10000);
});
