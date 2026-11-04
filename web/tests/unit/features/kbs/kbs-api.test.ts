import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "@/tests/msw/server";
import { createKb, deleteKb, listKbs, renameKb } from "@/features/kbs/api";
import { errorEnvelope, makeKb, TEST_WORKSPACE_ID } from "./fixtures";

/**
 * 接口层用例：把"发什么请求"钉死，界面测试只关心"显示什么"。
 * MSW 在 `vitest.setup.ts` 里以 `onUnhandledRequest: "error"` 启动——
 * 没声明的请求会让用例失败，正好用来抓"调了不存在的接口"。
 */

describe("features/kbs/api", () => {
  it("listKbs 按 limit + cursor 请求游标分页，并把 next_cursor 原样交给调用方", async () => {
    const seen: URLSearchParams[] = [];
    const workspaceHeaders: (string | null)[] = [];
    server.use(
      http.get("*/api/v1/knowledge-bases", ({ request }) => {
        seen.push(new URL(request.url).searchParams);
        workspaceHeaders.push(request.headers.get("X-Workspace-ID"));
        return HttpResponse.json({
          items: [makeKb()],
          next_cursor: "cursor-2",
          limit: 20,
        });
      }),
    );

    const page = await listKbs({
      cursor: "cursor-1",
      limit: 5,
      workspaceId: TEST_WORKSPACE_ID,
    });

    expect(seen).toHaveLength(1);
    expect(seen[0]?.get("limit")).toBe("5");
    expect(seen[0]?.get("cursor")).toBe("cursor-1");
    expect(workspaceHeaders).toEqual([TEST_WORKSPACE_ID]);
    expect(page.next_cursor).toBe("cursor-2");
    expect(page.items[0]?.kb_id).toBe("11111111-1111-4111-8111-111111111111");
  });

  it("createKb 以 POST 提交固化的嵌入与切片参数", async () => {
    let body: unknown = null;
    server.use(
      http.post("*/api/v1/knowledge-bases", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(makeKb({ name: "客服知识库" }), { status: 201 });
      }),
    );

    const created = await createKb({
      name: "客服知识库",
      embedding_model_key: "BAAI/bge-small-zh-v1.5",
      embedding_dim: 512,
      chunk_size: 512,
      chunk_overlap: 64,
      normalize: true,
    });

    expect(body).toEqual({
      name: "客服知识库",
      embedding_model_key: "BAAI/bge-small-zh-v1.5",
      embedding_dim: 512,
      chunk_size: 512,
      chunk_overlap: 64,
      normalize: true,
    });
    expect(created.name).toBe("客服知识库");
  });

  it("renameKb 带 If-Match（当前 row_version）并对 200 返回最新版本", async () => {
    let ifMatch: string | null = null;
    let body: unknown = null;
    server.use(
      http.patch("*/api/v1/knowledge-bases/:kbId", async ({ request }) => {
        ifMatch = request.headers.get("If-Match");
        body = await request.json();
        return HttpResponse.json(makeKb({ name: "新名字", row_version: 4 }));
      }),
    );

    const updated = await renameKb({
      kbId: "11111111-1111-4111-8111-111111111111",
      name: "新名字",
      rowVersion: 3,
    });

    expect(ifMatch).toBe("3");
    expect(body).toEqual({ name: "新名字" });
    expect(updated.row_version).toBe(4);
    expect(updated.name).toBe("新名字");
  });

  it("deleteKb 用 DELETE + If-Match 换取 202 受理", async () => {
    let method = "";
    let ifMatch: string | null = null;
    server.use(
      http.delete("*/api/v1/knowledge-bases/:kbId", ({ request }) => {
        method = request.method;
        ifMatch = request.headers.get("If-Match");
        return HttpResponse.json(
          {
            kb_id: "11111111-1111-4111-8111-111111111111",
            status: "purging",
            generation: 2,
            row_version: 5,
          },
          { status: 202 },
        );
      }),
    );

    const deleted = await deleteKb({
      kbId: "11111111-1111-4111-8111-111111111111",
      rowVersion: 4,
    });

    expect(method).toBe("DELETE");
    expect(ifMatch).toBe("4");
    expect(deleted).toMatchObject({ status: "purging", row_version: 5 });
  });

  it("409 REVISION_CONFLICT 带着后端错误码抛出，供上层重新读取", async () => {
    server.use(
      http.patch("*/api/v1/knowledge-bases/:kbId", () =>
        HttpResponse.json(errorEnvelope("REVISION_CONFLICT", "row_version mismatch"), {
          status: 409,
        }),
      ),
    );

    await expect(
      renameKb({
        kbId: "11111111-1111-4111-8111-111111111111",
        name: "改名",
        rowVersion: 1,
      }),
    ).rejects.toMatchObject({ code: "REVISION_CONFLICT", status: 409 });
  });
});
