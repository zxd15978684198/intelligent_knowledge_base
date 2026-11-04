import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "@/tests/msw/server";
import { errorEnvelopeBody } from "@/tests/msw/handlers";
import {
  QueryValidationError,
  normalizeQueryInput,
  queryEndpoint,
  runQuery,
} from "@/features/query/api";

import { TEST_KB_ID, TEST_WORKSPACE_ID, makeChunk, makeQueryResponse } from "./fixtures";

/**
 * 接口层用例。
 *
 * 重点不是"能拿到数据"，而是三条容易写错的契约：
 * 1. 路径必须是 `/api/v1/knowledge-bases/{kb_id}/query`——任务书里的 `/api/v1/query` 不存在；
 * 2. 入参越界要在本地就炸，不能静默夹到合法范围（调试台骗人比报错更糟）；
 * 3. `degraded` 缺省时归一成 `{}`，组件才能用"空 = 两路都成功"这个口径。
 *
 * MSW 的 `onUnhandledRequest: "error"` 在这里是免费的断言：本地校验一旦漏了、真把越界请求
 * 发出去，用例会直接失败而不是拿到 404 继续跑。
 */

describe("features/query/api", () => {
  it("POST /knowledge-bases/{kb_id}/query：带上归一化后的 query/top_k 与工作区头", async () => {
    let capturedBody: unknown = null;
    let capturedWorkspace: string | null = null;

    server.use(
      http.post("*/api/v1/knowledge-bases/:kbId/query", async ({ request, params }) => {
        capturedBody = await request.json();
        capturedWorkspace = request.headers.get("x-workspace-id");
        expect(params.kbId).toBe(TEST_KB_ID);
        return HttpResponse.json(
          makeQueryResponse({ query: "融合排序", results: [makeChunk({ content: "命中的切片" })] }),
        );
      }),
    );

    const response = await runQuery(
      TEST_KB_ID,
      { query: "  融合排序  ", top_k: 20 },
      TEST_WORKSPACE_ID,
    );

    expect(capturedBody).toEqual({ query: "融合排序", top_k: 20 });
    expect(capturedWorkspace).toBe(TEST_WORKSPACE_ID);
    expect(response.results).toHaveLength(1);
    expect(response.degraded).toEqual({});
  });

  it("路径拼接带 kb_id 且做了 URL 编码", () => {
    expect(queryEndpoint("a/b?c")).toBe("/api/v1/knowledge-bases/a%2Fb%3Fc/query");
  });

  it("top_k 越界抛 QueryValidationError，消息里给出上限", async () => {
    await expect(runQuery(TEST_KB_ID, { query: "x", top_k: 51 })).rejects.toBeInstanceOf(
      QueryValidationError,
    );
    await expect(runQuery(TEST_KB_ID, { query: "x", top_k: 51 })).rejects.toThrow(/50/);
    await expect(runQuery(TEST_KB_ID, { query: "x", top_k: 0 })).rejects.toBeInstanceOf(
      QueryValidationError,
    );
    await expect(runQuery(TEST_KB_ID, { query: "x", top_k: 1.5 })).rejects.toThrow(/整数/);
  });

  it("空问题与超长问题都不发请求", async () => {
    await expect(runQuery(TEST_KB_ID, { query: "   " })).rejects.toThrow("请输入检索问题。");
    await expect(runQuery(TEST_KB_ID, { query: "问".repeat(1001) })).rejects.toThrow(/1000/);
  });

  it("top_k 省略时补后端默认值 10", () => {
    expect(normalizeQueryInput({ query: "x" })).toEqual({ query: "x", top_k: 10 });
  });

  it("403 原样抛出 ApiError，页面据此给权限文案", async () => {
    server.use(
      http.post("*/api/v1/knowledge-bases/:kbId/query", () =>
        HttpResponse.json(
          errorEnvelopeBody({ code: "FORBIDDEN", message: "missing permission query.run" }),
          { status: 403 },
        ),
      ),
    );

    await expect(runQuery(TEST_KB_ID, { query: "x" })).rejects.toMatchObject({
      status: 403,
      code: "FORBIDDEN",
      requestId: "req-test-0001",
    });
  });
});
