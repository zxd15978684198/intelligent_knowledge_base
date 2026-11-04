import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { server } from "@/tests/msw/server";
import { useKbListController, useKbs, useRenameKb } from "@/features/kbs/hooks";
import { errorEnvelope, makeKb, TEST_WORKSPACE_ID } from "./fixtures";

/**
 * Hook 用例：覆盖计划 §5 的恢复路径——
 * 游标翻页、409 冲突重新读取、410 游标过期重置到第一页。
 * 每个用例新建 QueryClient（retry:false），避免上一条用例的缓存串味。
 */

/**
 * jsdom 的 `AbortSignal` 和 Node（undici）的 `AbortSignal` 不是同一个类：
 * TanStack Query 用全局 `AbortController`（jsdom 那个）造 signal，undici 的 fetch 会因为它
 * `instanceof AbortSignal`（Node 那个）不成立而直接抛
 * `TypeError: RequestInit: Expected signal ("AbortSignal {}") to be an instance of AbortSignal`。
 * 生产代码转发 signal 是对的（请求取消靠它），用例里只把 signal 剥掉，别让它进入 fetch。
 * `baseFetch` 在 `beforeAll` 才读，因为那时 `vitest.setup.ts` 的 `server.listen()` 已经把
 * MSW 的 fetch 拦截器装上去了——在模块顶层读会拿到未拦截的原生 fetch，请求就真发出去了。
 */
let baseFetch: typeof globalThis.fetch = globalThis.fetch;

beforeAll(() => {
  baseFetch = globalThis.fetch;
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    if (!init?.signal) return baseFetch(input, init);
    const { signal: _signal, ...rest } = init;
    return baseFetch(input, rest);
  });
});

afterAll(() => {
  vi.stubGlobal("fetch", baseFetch);
});

function createWrapper(): ({ children }: { children: ReactNode }) => ReactNode {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("features/kbs/hooks", () => {
  it("useKbs 用 next_cursor 翻页，next_cursor 为 null 时停止", async () => {
    const cursors: (string | null)[] = [];
    const workspaceHeaders: (string | null)[] = [];
    server.use(
      http.get("*/api/v1/knowledge-bases", ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("cursor");
        cursors.push(cursor);
        workspaceHeaders.push(request.headers.get("X-Workspace-ID"));
        if (cursor === null) {
          return HttpResponse.json({
            items: [makeKb({ name: "第一页" })],
            next_cursor: "next-1",
            limit: 20,
          });
        }
        return HttpResponse.json({
          items: [makeKb({ kb_id: "33333333-3333-4333-8333-333333333333", name: "第二页" })],
          next_cursor: null,
          limit: 20,
        });
      }),
    );

    const { result } = renderHook(() => useKbs({ workspaceId: TEST_WORKSPACE_ID }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.pages).toHaveLength(1);
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => {
      await result.current.fetchNextPage();
    });

    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2));
    expect(cursors).toEqual([null, "next-1"]);
    expect(workspaceHeaders).toEqual([TEST_WORKSPACE_ID, TEST_WORKSPACE_ID]);
    expect(result.current.hasNextPage).toBe(false);
    expect(result.current.data?.pages.flatMap((page) => page.items)).toHaveLength(2);
  });

  it("改名遇到 409 REVISION_CONFLICT：先重新读取最新列表，再把错误交给界面", async () => {
    let listCalls = 0;
    server.use(
      http.get("*/api/v1/knowledge-bases", () => {
        listCalls += 1;
        return HttpResponse.json({
          items: [makeKb({ row_version: listCalls === 1 ? 1 : 2 })],
          next_cursor: null,
          limit: 20,
        });
      }),
      http.patch("*/api/v1/knowledge-bases/:kbId", () =>
        HttpResponse.json(errorEnvelope("REVISION_CONFLICT", "row_version mismatch"), {
          status: 409,
        }),
      ),
    );

    const { result } = renderHook(
      () => ({
        list: useKbs({ workspaceId: TEST_WORKSPACE_ID }),
        rename: useRenameKb(TEST_WORKSPACE_ID),
      }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));
    expect(listCalls).toBe(1);

    await act(async () => {
      await expect(
        result.current.rename.mutateAsync({
          kbId: "11111111-1111-4111-8111-111111111111",
          name: "改名",
          rowVersion: 1,
        }),
      ).rejects.toMatchObject({ code: "REVISION_CONFLICT" });
    });

    // 冲突后必须重新读到最新版本（row_version 已变成 2），不静默覆盖。
    await waitFor(() => expect(listCalls).toBe(2));
    expect(result.current.list.data?.pages[0]?.items[0]?.row_version).toBe(2);
  });

  it("加载更多遇到 410 CURSOR_EXPIRED：清空分页、重载第一页并给出提示", async () => {
    const requests: (string | null)[] = [];
    server.use(
      http.get("*/api/v1/knowledge-bases", ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("cursor");
        requests.push(cursor);
        if (cursor === null) {
          return HttpResponse.json({
            items: [makeKb({ name: "第一页" })],
            next_cursor: "expired-cursor",
            limit: 20,
          });
        }
        return HttpResponse.json(errorEnvelope("CURSOR_EXPIRED", "cursor expired"), {
          status: 410,
        });
      }),
    );

    const { result } = renderHook(
      () => useKbListController({ workspaceId: TEST_WORKSPACE_ID }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.items).toHaveLength(1);

    await act(async () => {
      await result.current.loadMore();
    });

    await waitFor(() =>
      expect(result.current.notice).toBe("分页游标已过期，已重新加载第一页。"),
    );
    // 重置后重新请求第一页（不带 cursor），分页与条目都回到单页状态。
    expect(requests).toEqual([null, "expired-cursor", null]);
    expect(result.current.pages).toHaveLength(1);
    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0]?.name).toBe("第一页");
  });
});
