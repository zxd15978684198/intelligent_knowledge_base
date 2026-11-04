import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import type { ChunkItem } from "@/lib/contracts";
import { ChunkList } from "@/features/documents/ChunkList";
import { server } from "@/tests/msw/server";
import { FIXTURE_DOC_ID, FIXTURE_WS_ID, sampleChunk } from "@/tests/fixtures/entities";

/**
 * 切片列表只能"加载更多"，不能有分页器：
 * 后端 `ChunkPage.total` 是**页内条数**（`documents.py:324`），拿它算页数永远是 1 页。
 * 所以"还有下一页"的判据只能是"本页满 limit"，这里用 20 条满页 + 第二页 1 条来钉住它。
 */

function makeChunk(index: number): ChunkItem {
  return {
    ...sampleChunk,
    chunk_id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`,
    chunk_ordinal: index,
    content: `第 ${index} 段内容`,
  };
}

function renderList(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <ChunkList documentId={FIXTURE_DOC_ID} workspaceId={FIXTURE_WS_ID} />
    </QueryClientProvider>
  );
  render(ui);
}

describe("features/documents/ChunkList", () => {
  it("满页时显示「加载更多」，第二页取完后停止；不显示总条数", async () => {
    const offsets: number[] = [];
    server.use(
      http.get("*/api/v1/documents/:documentId/chunks", ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("offset") ?? "0");
        offsets.push(offset);
        const items = offset === 0 ? Array.from({ length: 20 }, (_, index) => makeChunk(index)) : [makeChunk(20)];
        // total 故意给页内条数：与后端 `total=len(chunks)` 一致。
        return HttpResponse.json({ items, limit: 20, offset, total: items.length });
      }),
    );

    renderList();

    await screen.findByText("已加载 20 条");
    // 没有"共 N 条"这种总数文案（后端给不出总数）。
    expect(screen.queryByText(/共 \d+ 条/)).toBeNull();
    expect(screen.getByText("第 0 段内容").className).toContain("whitespace-pre-wrap");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "加载更多" }));

    await screen.findByText("已加载 21 条");
    expect(screen.getByText(/没有更多切片了/)).toBeTruthy();
    expect(offsets).toEqual([0, 20]);
    // 第二页只有 1 条，不该再出现"加载更多"。
    expect(screen.queryByRole("button", { name: "加载更多" })).toBeNull();
  }, 15000);
});
