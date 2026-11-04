import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { JobPoller } from "@/features/jobs/JobPoller";
import { server } from "@/tests/msw/server";
import {
  FIXTURE_JOB_ID,
  FIXTURE_WS_ID,
  sampleJobProcessing,
  sampleJobSuperseded,
} from "@/tests/fixtures/entities";

/**
 * 轮询器的两条纪律：
 * 1. **superseded 必须停**。后端 `TERMINAL_STATUSES` 漏了这一态，线上返回的是
 *    `status="superseded"` + `terminal=false`（`sampleJobSuperseded` 就是照这个造的），
 *    只读 `terminal` 会永远轮询下去。
 * 2. **取消只在 pending/processing 出现**，且文案写明"不保证立即停止处理中的任务"。
 */

function renderPoller(): ReturnType<typeof render> {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <JobPoller jobId={FIXTURE_JOB_ID} workspaceId={FIXTURE_WS_ID} />
    </QueryClientProvider>
  );
  return render(ui);
}

describe("features/jobs/JobPoller", () => {
  it("superseded 且 terminal=false 时只请求一次（后端缺陷兜底）", async () => {
    let jobCalls = 0;
    server.use(
      http.get("*/api/v1/jobs/:jobId", () => {
        jobCalls += 1;
        return HttpResponse.json(sampleJobSuperseded);
      }),
    );

    renderPoller();

    await screen.findByText("已被取代");
    expect(screen.getByText("已停止轮询（终态）")).toBeTruthy();

    // 一个完整轮询间隔（1.5s）之后仍只有首次请求。
    await new Promise((resolve) => setTimeout(resolve, 1800));
    expect(jobCalls).toBe(1);
  }, 10000);

  it("取消按钮只在 pending/processing 显示，并写明取消的边界", async () => {
    let status: "succeeded" | "processing" = "succeeded";
    server.use(
      http.get("*/api/v1/jobs/:jobId", () =>
        HttpResponse.json({
          ...sampleJobProcessing,
          status,
          terminal: status === "succeeded",
        }),
      ),
    );

    const finished = renderPoller();
    await screen.findByText("成功");
    // 终态没有可取消的东西。
    expect(screen.queryByRole("button", { name: "取消作业" })).toBeNull();
    finished.unmount();

    status = "processing";
    renderPoller();
    await screen.findByText("处理中");
    expect(screen.getByRole("button", { name: "取消作业" })).toBeTruthy();
    expect(screen.getByText(/取消不保证立即停止处理中的任务/)).toBeTruthy();
  }, 10000);
});
