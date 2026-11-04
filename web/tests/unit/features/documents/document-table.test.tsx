import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactElement, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { DocumentSummary } from "@/lib/contracts";
import { DocumentTable } from "@/features/documents/DocumentTable";
import { server } from "@/tests/msw/server";
import {
  FIXTURE_DOC_ID,
  FIXTURE_KB_ID,
  FIXTURE_WS_ID,
  sampleDocumentSummary,
} from "@/tests/fixtures/entities";

/**
 * 列表的两件事：
 * 1. 搜索框是**本地过滤**（只筛当前页），界面必须自己说出来；
 * 2. 删除（202 受理）之后必须重新取列表，被删的行要消失——不是只改本地状态。
 */

vi.mock("next/link", async () => {
  const React = await import("react");
  return {
    default: ({ href, children }: { href: string; children?: ReactNode }) =>
      React.createElement("a", { href }, children),
  };
});

const secondDocument: DocumentSummary = {
  ...sampleDocumentSummary,
  document_id: "77777777-7777-4777-8777-777777777777",
  filename: "另一个.pdf",
  searchable: false,
  active_build_id: null,
  active_index_version: null,
};

function renderTable(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <DocumentTable kbId={FIXTURE_KB_ID} workspaceId={FIXTURE_WS_ID} />
    </QueryClientProvider>
  );
  render(ui);
}

describe("features/documents/DocumentTable", () => {
  it("本地过滤只作用于当前页；删除后重新取列表并移除该行", async () => {
    let listCalls = 0;
    let deleted = false;

    server.use(
      http.get("*/api/v1/knowledge-bases/:kbId/documents", () => {
        listCalls += 1;
        const items = deleted ? [secondDocument] : [sampleDocumentSummary, secondDocument];
        return HttpResponse.json({ items, limit: 20, offset: 0, total: items.length });
      }),
      http.delete("*/api/v1/documents/:documentId", () => {
        deleted = true;
        return HttpResponse.json(
          {
            document_id: FIXTURE_DOC_ID,
            deduped: false,
            job_id: null,
            document_version_id: null,
            status: "purging",
          },
          { status: 202 },
        );
      }),
    );

    renderTable();

    await screen.findByText("手册 v2.pdf");
    expect(screen.getByText("另一个.pdf")).toBeTruthy();
    expect(screen.getByText(/共 2 篇/)).toBeTruthy();

    const user = userEvent.setup();
    // 本地过滤：只筛当前页，界面明说"本地过滤"。
    await user.type(screen.getByLabelText(/按文件名过滤/), "手册");
    expect(screen.queryByText("另一个.pdf")).toBeNull();
    expect(screen.getByText("手册 v2.pdf")).toBeTruthy();
    expect(screen.getAllByText(/本地过滤/).length).toBeGreaterThan(0);

    await user.clear(screen.getByLabelText(/按文件名过滤/));
    await screen.findByText("另一个.pdf");

    // 两段式删除确认（行内），确认后打 DELETE。
    const row = screen.getByRole("row", { name: /手册 v2\.pdf/ });
    await user.click(within(row).getByRole("button", { name: "删除" }));
    await user.click(within(row).getByRole("button", { name: "确认" }));

    await waitFor(() => expect(deleted).toBe(true));
    await waitFor(() => expect(screen.queryByText("手册 v2.pdf")).toBeNull());

    // 列表必须重新取过一次（失效 → refetch），不是本地删行。
    expect(listCalls).toBeGreaterThanOrEqual(2);
    // 删除回执是 202 + purging。
    expect(screen.getByText(/已提交删除（202）/)).toBeTruthy();
  }, 15000);
});
