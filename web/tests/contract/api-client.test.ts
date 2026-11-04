import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest, apiUpload, isApiError, resolveApiPath } from "@/lib/api/client";
import { clearToken, setToken } from "@/lib/auth/token-store";

import { FORBIDDEN_ENVELOPE, NON_ENVELOPE_BODY, UNAUTHENTICATED_ENVELOPE } from "../fixtures/errors";
import { errorResponse, TEST_REQUEST_ID } from "../msw/handlers";
import { server } from "../msw/server";

/**
 * 客户端契约：路径拼接、横切头、错误信封 → `ApiError`、上传的 202/200 区分。
 *
 * 这些是**所有 feature 都依赖**的行为，坏了不是某个页面报错，而是全站沉默出错，
 * 所以宁可在这里用 MSW 把真实 HTTP 交互跑一遍，而不是只测纯函数。
 */

describe("ApiError", () => {
  it("是 Error 子类，且四个字段都能读", () => {
    const error = new ApiError({ status: 409, code: "REVISION_CONFLICT", message: "版本冲突" });
    expect(error).toBeInstanceOf(Error);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.name).toBe("ApiError");
    expect(error.status).toBe(409);
    expect(error.code).toBe("REVISION_CONFLICT");
    expect(error.message).toBe("版本冲突");
    // 未显式给出时 details 是 null、requestId 是 null，避免调用方写 `?? null`。
    expect(error.details).toBeNull();
    expect(error.requestId).toBeNull();
    expect(isApiError(error)).toBe(true);
    expect(isApiError(new Error("普通错误"))).toBe(false);
  });
});

describe("resolveApiPath", () => {
  it("业务路径补 /api/v1 前缀", () => {
    expect(resolveApiPath("/knowledge-bases")).toBe("/api/v1/knowledge-bases");
    expect(resolveApiPath("knowledge-bases")).toBe("/api/v1/knowledge-bases");
    expect(resolveApiPath("/knowledge-bases?limit=50")).toBe("/api/v1/knowledge-bases?limit=50");
  });

  it("已带 /api/ 前缀的不重复加", () => {
    expect(resolveApiPath("/api/v1/knowledge-bases")).toBe("/api/v1/knowledge-bases");
    expect(resolveApiPath("/api/auth/session")).toBe("/api/auth/session");
  });

  it("/health 不在 v1 之下，原样使用", () => {
    expect(resolveApiPath("/health")).toBe("/health");
    expect(resolveApiPath("/health?deep=1")).toBe("/health?deep=1");
  });

  it("绝对 URL 原样返回（测试与直连后端时用）", () => {
    expect(resolveApiPath("http://127.0.0.1:8010/api/v1/x")).toBe("http://127.0.0.1:8010/api/v1/x");
    expect(resolveApiPath("https://example.com/y")).toBe("https://example.com/y");
  });
});

describe("apiRequest：横切头与请求体", () => {
  it("注入 Authorization / X-Workspace-ID / Idempotency-Key / If-Match，并把对象序列化成 JSON", async () => {
    const captured: {
      authorization: string | null;
      workspace: string | null;
      idempotencyKey: string | null;
      ifMatch: string | null;
      contentType: string | null;
      accept: string | null;
      bodyText: string | null;
      method: string;
    } = {
      authorization: null,
      workspace: null,
      idempotencyKey: null,
      ifMatch: null,
      contentType: null,
      accept: null,
      bodyText: null,
      method: "",
    };

    server.use(
      http.post("/api/v1/knowledge-bases", async ({ request }) => {
        captured.authorization = request.headers.get("authorization");
        captured.workspace = request.headers.get("x-workspace-id");
        captured.idempotencyKey = request.headers.get("idempotency-key");
        captured.ifMatch = request.headers.get("if-match");
        captured.contentType = request.headers.get("content-type");
        captured.accept = request.headers.get("accept");
        captured.method = request.method;
        captured.bodyText = await request.text();
        return HttpResponse.json({ kb_id: "kb-1", name: "产品手册" }, { status: 201 });
      }),
    );

    const data = await apiRequest<{ kb_id: string; name: string }>("/knowledge-bases", {
      method: "POST",
      body: { name: "产品手册", embedding_dim: 512 },
      token: "tok-abc",
      workspaceId: "ws-1",
      idempotencyKey: "idem-1",
      // 后端会 strip 引号与 W/，所以客户端不必加引号。
      ifMatch: "3",
    });

    expect(data.kb_id).toBe("kb-1");
    expect(captured.method).toBe("POST");
    expect(captured.authorization).toBe("Bearer tok-abc");
    expect(captured.workspace).toBe("ws-1");
    expect(captured.idempotencyKey).toBe("idem-1");
    expect(captured.ifMatch).toBe("3");
    expect(captured.contentType).toBe("application/json");
    expect(captured.accept).toBe("application/json");
    expect(JSON.parse(captured.bodyText ?? "null")).toEqual({ name: "产品手册", embedding_dim: 512 });
  });

  it("token 为 null 时不发 Authorization 头（登录/注册走匿名）", async () => {
    let authorization: string | null = "sentinel";
    server.use(
      http.post("/api/v1/auth/login", ({ request }) => {
        authorization = request.headers.get("authorization");
        return HttpResponse.json({ access_token: "t" });
      }),
    );

    await apiRequest("/auth/login", { method: "POST", body: { username: "u", password: "p" }, token: null });
    expect(authorization).toBeNull();
  });

  it("省略 token 时自动带上会话令牌（防止 feature 漏传导致列表恒空）", async () => {
    let authorization: string | null = "sentinel";
    server.use(
      http.get("/api/v1/knowledge-bases", ({ request }) => {
        authorization = request.headers.get("authorization");
        return HttpResponse.json({ items: [], limit: 50, next_cursor: null });
      }),
    );

    setToken("session-token");
    try {
      await apiRequest("/knowledge-bases");
      expect(authorization).toBe("Bearer session-token");
    } finally {
      clearToken();
    }
  });

  it("显式 token: null 不会被会话令牌覆盖（退出登录后必须真的匿名）", async () => {
    let authorization: string | null = "sentinel";
    server.use(
      http.post("/api/v1/auth/login", ({ request }) => {
        authorization = request.headers.get("authorization");
        return HttpResponse.json({ access_token: "t" });
      }),
    );

    setToken("session-token");
    try {
      await apiRequest("/auth/login", { method: "POST", body: { username: "u", password: "p" }, token: null });
      expect(authorization).toBeNull();
    } finally {
      clearToken();
    }
  });

  it("GET 请求不带 body 也不带 Content-Type", async () => {
    let contentType: string | null = "sentinel";
    let bodyText: string | null = "sentinel";
    server.use(
      http.get("/api/v1/knowledge-bases", async ({ request }) => {
        contentType = request.headers.get("content-type");
        bodyText = await request.text();
        return HttpResponse.json({ items: [], limit: 50, next_cursor: null });
      }),
    );

    await apiRequest("/knowledge-bases", { token: "t" });
    expect(contentType).toBeNull();
    expect(bodyText).toBe("");
  });
});

describe("apiRequest：错误信封 → ApiError", () => {
  it("401 信封带出 code/details/requestId（requestId 取响应头）", async () => {
    server.use(http.get("/api/v1/knowledge-bases", () => errorResponse(401, UNAUTHENTICATED_ENVELOPE.error)));

    const error = await apiRequest("/knowledge-bases").catch((cause: unknown) => cause);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) {
      throw new Error("期望抛出 ApiError");
    }
    expect(error.status).toBe(401);
    expect(error.code).toBe("UNAUTHENTICATED");
    expect(error.message).toBe(UNAUTHENTICATED_ENVELOPE.error.message);
    // 响应头优先（main.py 的中间件给每个响应都加 X-Request-ID）。
    expect(error.requestId).toBe(TEST_REQUEST_ID);
  });

  it("没有 X-Request-ID 头时回落到信封体里的 request_id", async () => {
    server.use(
      http.get("/api/v1/knowledge-bases", () =>
        // 刻意不带 X-Request-ID 头，模拟反代吞掉响应头的情况。
        HttpResponse.json(FORBIDDEN_ENVELOPE, { status: 403 }),
      ),
    );

    const error = await apiRequest("/knowledge-bases").catch((cause: unknown) => cause);
    if (!(error instanceof ApiError)) {
      throw new Error("期望抛出 ApiError");
    }
    expect(error.status).toBe(403);
    expect(error.code).toBe("FORBIDDEN");
    expect(error.requestId).toBe(TEST_REQUEST_ID);
  });

  it("422 的 details 原样带出（使用方要能定位到字段）", async () => {
    server.use(
      http.post(
        "/api/v1/knowledge-bases/kb-1/query",
        () =>
          errorResponse(422, {
            code: "INVALID_REQUEST",
            message: "请求参数不合法",
            details: { errors: [{ loc: ["body", "top_k"], type: "less_than_equal", msg: "too big" }] },
          }),
      ),
    );

    const error = await apiRequest("/knowledge-bases/kb-1/query", { method: "POST", body: { query: "q" } }).catch(
      (cause: unknown) => cause,
    );
    if (!(error instanceof ApiError)) {
      throw new Error("期望抛出 ApiError");
    }
    expect(error.code).toBe("INVALID_REQUEST");
    expect(error.details).toEqual({
      errors: [{ loc: ["body", "top_k"], type: "less_than_equal", msg: "too big" }],
    });
  });

  it("非信封错误体（反代 HTML）降级成 INVALID_RESPONSE，但状态码保留", async () => {
    server.use(
      http.get("/api/v1/knowledge-bases", () =>
        new HttpResponse(NON_ENVELOPE_BODY, { status: 502, headers: { "Content-Type": "text/html" } }),
      ),
    );

    const error = await apiRequest("/knowledge-bases").catch((cause: unknown) => cause);
    if (!(error instanceof ApiError)) {
      throw new Error("期望抛出 ApiError");
    }
    expect(error.status).toBe(502);
    expect(error.code).toBe("INVALID_RESPONSE");
    expect(error.message).toContain("502");
  });
});

describe("apiRequest：成功但无内容 / 网络层失败", () => {
  it("204 返回 undefined（删除/取消这类接口）", async () => {
    server.use(http.delete("/api/v1/documents/d-1", () => new HttpResponse(null, { status: 204 })));
    await expect(apiRequest("/documents/d-1", { method: "DELETE", token: "t" })).resolves.toBeUndefined();
  });

  it("网络层失败 → ApiError(status=0, code=NETWORK_ERROR)", async () => {
    server.use(http.get("/api/v1/knowledge-bases", () => HttpResponse.error()));

    const error = await apiRequest("/knowledge-bases").catch((cause: unknown) => cause);
    if (!(error instanceof ApiError)) {
      throw new Error("期望抛出 ApiError");
    }
    // 0 不会和任何真实状态码冲突，UI 靠它区分"没连上"和"服务端拒绝了"。
    expect(error.status).toBe(0);
    expect(error.code).toBe("NETWORK_ERROR");
  });

  it("AbortError 原样抛出，不包成 ApiError（取消 ≠ 失败）", async () => {
    // 中止时 fetch 拒绝的是一个 AbortError，客户端只做透传。这里直接把该拒绝注入 fetch，
    // 而不是用 AbortController：jsdom 的 AbortSignal 与 Node 的 fetch 不同源，后者会先报
    // "Expected signal to be an instance of AbortSignal"，测不到我们想测的那段代码。
    const abortError = new DOMException("The operation was aborted.", "AbortError");
    vi.stubGlobal("fetch", () => Promise.reject(abortError));

    try {
      const error = await apiRequest("/knowledge-bases").catch((cause: unknown) => cause);

      expect(isApiError(error)).toBe(false);
      // 同一个对象出去，调用方才好判断"是用户取消"而不是"网络失败"。
      expect(error).toBe(abortError);
      expect((error as { name?: string }).name).toBe("AbortError");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("apiUpload：multipart 与 202/200", () => {
  it("不手写 Content-Type；新文档回 202", async () => {
    const captured: { contentType: string | null; bodyPreview: string; idempotencyKey: string | null } = {
      contentType: null,
      bodyPreview: "",
      idempotencyKey: null,
    };

    server.use(
      http.post("/api/v1/knowledge-bases/kb-1/documents", async ({ request }) => {
        captured.contentType = request.headers.get("content-type");
        captured.idempotencyKey = request.headers.get("idempotency-key");
        captured.bodyPreview = (await request.text()).slice(0, 120);
        return HttpResponse.json({ document_id: "doc-1", deduped: false, status: "pending" }, { status: 202 });
      }),
    );

    const form = new FormData();
    form.append("file", new File(["第一节 安装说明"], "说明书.pdf", { type: "application/pdf" }));

    const { data, status } = await apiUpload<{ document_id: string; deduped: boolean; status: string }>(
      "/knowledge-bases/kb-1/documents",
      form,
      { token: "tok", workspaceId: "ws-1", idempotencyKey: "idem-doc-1" },
    );

    expect(status).toBe(202);
    expect(data.document_id).toBe("doc-1");
    expect(data.deduped).toBe(false);
    expect(captured.bodyPreview.length).toBeGreaterThan(0);
    expect(captured.idempotencyKey).toBe("idem-doc-1");

    // 单测环境测不到浏览器那个 multipart boundary：jsdom 的 FormData 与 Node 的 fetch
    // 不同源，undici 只会把它当普通对象序列化（content-type 变成 text/plain）。
    // 能测、且真正要钉住的是「客户端没有自己写 Content-Type」——否则这里会出现
    // application/json（走了 apiRequest 的 JSON 分支），或者一个没有 boundary 的
    // multipart/form-data（手写头的典型错误，后端解析必然 422）。
    expect(captured.contentType).not.toBeNull();
    expect(captured.contentType).not.toContain("application/json");
    expect(captured.contentType).not.toBe("multipart/form-data");
  });

  it("命中 Idempotency-Key 去重时回 200 且 deduped=true（状态码必须能被区分）", async () => {
    server.use(
      http.post("/api/v1/knowledge-bases/kb-1/documents", () =>
        HttpResponse.json({ document_id: "doc-1", deduped: true, status: "succeeded" }, { status: 200 }),
      ),
    );

    const form = new FormData();
    form.append("file", new File(["同一份内容"], "说明书.pdf", { type: "application/pdf" }));

    const { data, status } = await apiUpload<{ document_id: string; deduped: boolean; status: string }>(
      "/knowledge-bases/kb-1/documents",
      form,
      { token: "tok", idempotencyKey: "idem-doc-1" },
    );

    expect(status).toBe(200);
    expect(data.deduped).toBe(true);
  });

  it("上传失败也走信封 → ApiError（不然 413/415 会被当成空数据）", async () => {
    server.use(
      http.post(
        "/api/v1/knowledge-bases/kb-1/documents",
        () => errorResponse(415, { code: "UNSUPPORTED_FORMAT", message: "不支持的文件格式" }),
      ),
    );

    const form = new FormData();
    form.append("file", new File(["x"], "evil.exe", { type: "application/octet-stream" }));

    const error = await apiUpload("/knowledge-bases/kb-1/documents", form, { token: "tok" }).catch(
      (cause: unknown) => cause,
    );
    if (!(error instanceof ApiError)) {
      throw new Error("期望抛出 ApiError");
    }
    expect(error.status).toBe(415);
    expect(error.code).toBe("UNSUPPORTED_FORMAT");
  });
});
