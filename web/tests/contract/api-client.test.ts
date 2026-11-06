import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiRequest,
  apiUpload,
  isApiError,
  refreshSession,
  resolveApiPath,
} from "@/lib/api/client";

import { FORBIDDEN_ENVELOPE, NON_ENVELOPE_BODY, UNAUTHENTICATED_ENVELOPE } from "../fixtures/errors";
import { errorResponse, TEST_REQUEST_ID } from "../msw/handlers";
import { server } from "../msw/server";

/**
 * 客户端契约：路径拼接、横切头（含 Cookie 会话与 CSRF）、错误信封 → `ApiError`、
 * 401 单飞刷新与重放、上传的 202/200 区分。
 *
 * 这些是**所有 feature 都依赖**的行为，坏了不是某个页面报错，而是全站沉默出错，
 * 所以宁可在这里用 MSW 把真实 HTTP 交互跑一遍，而不是只测纯函数。
 *
 * Cookie 会话模型的三个要点（与后端 M3 对齐）：
 * - 省略 `token` → 走 HttpOnly Cookie（`credentials: "include"`），写方法自动补
 *   `X-CSRF-Token`（值从可读的 `pyknora_csrf` Cookie 取）；
 * - `token: null` → 匿名端点（登录/注册），不发 Authorization、不补 CSRF；
 * - `token: "…"` → Bearer 兼容通道（命令行 / 评测脚本），无会话 Cookie 时后端豁免 CSRF。
 */

/** 会话是否存在的唯一前端可见信号：可读的 csrf Cookie。测试里手动种/清以精确控制 401 单飞。 */
function setCsrfCookie(value = "csrf-value-1"): void {
  document.cookie = `pyknora_csrf=${value}; path=/`;
}

function clearCsrfCookie(): void {
  document.cookie = "pyknora_csrf=; Max-Age=0; path=/";
}

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

describe("apiRequest：横切头与三态 token", () => {
  beforeEach(() => {
    clearCsrfCookie();
  });
  afterEach(() => {
    clearCsrfCookie();
  });

  it("token 为字符串时注入 Bearer + 其它横切头，并把对象序列化成 JSON", async () => {
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

  it("token 为 null 时不发 Authorization、也不补 CSRF 头（登录/注册走匿名）", async () => {
    setCsrfCookie(); // 即使有会话 hint，匿名端点也不该被补头
    let authorization: string | null = "sentinel";
    let csrfHeader: string | null = "sentinel";
    server.use(
      http.post("/api/v1/auth/login", ({ request }) => {
        authorization = request.headers.get("authorization");
        csrfHeader = request.headers.get("x-csrf-token");
        return HttpResponse.json({ access_token: "t" });
      }),
    );

    await apiRequest("/auth/login", { method: "POST", body: { username: "u", password: "p" }, token: null });
    expect(authorization).toBeNull();
    expect(csrfHeader).toBeNull();
  });

  it("省略 token 时不发 Authorization，带 credentials:include；写方法自动补 X-CSRF-Token", async () => {
    setCsrfCookie();
    const captured: { authorization: string | null; csrf: string | null; credentials: string } = {
      authorization: "sentinel",
      csrf: "sentinel",
      credentials: "",
    };
    server.use(
      http.post("/api/v1/knowledge-bases", ({ request }) => {
        captured.authorization = request.headers.get("authorization");
        captured.csrf = request.headers.get("x-csrf-token");
        captured.credentials = request.credentials;
        return HttpResponse.json({ kb_id: "kb-1" }, { status: 201 });
      }),
    );

    await apiRequest("/knowledge-bases", { method: "POST", body: { name: "x" } });

    // 身份在 HttpOnly Cookie 里，前端不该再发 Authorization。
    expect(captured.authorization).toBeNull();
    expect(captured.csrf).toBe("csrf-value-1");
    expect(captured.credentials).toBe("include");
  });

  it("GET（安全方法）省略 token 时不补 CSRF 头", async () => {
    setCsrfCookie();
    let csrfHeader: string | null = "sentinel";
    server.use(
      http.get("/api/v1/knowledge-bases", ({ request }) => {
        csrfHeader = request.headers.get("x-csrf-token");
        return HttpResponse.json({ items: [] });
      }),
    );

    await apiRequest("/knowledge-bases");
    expect(csrfHeader).toBeNull();
  });

  it("显式 token: null 优先于会话 hint（退出登录后必须真的匿名）", async () => {
    setCsrfCookie();
    let authorization: string | null = "sentinel";
    let csrfHeader: string | null = "sentinel";
    server.use(
      http.post("/api/v1/auth/login", ({ request }) => {
        authorization = request.headers.get("authorization");
        csrfHeader = request.headers.get("x-csrf-token");
        return HttpResponse.json({ access_token: "t" });
      }),
    );

    await apiRequest("/auth/login", { method: "POST", body: { username: "u", password: "p" }, token: null });
    expect(authorization).toBeNull();
    expect(csrfHeader).toBeNull();
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

describe("apiRequest：错误信封 → ApiError（无会话 hint，不触发刷新）", () => {
  beforeEach(() => {
    clearCsrfCookie();
  });

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
  beforeEach(() => {
    clearCsrfCookie();
  });

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

/* ------------------------------------------------------------------ *
 * 401 单飞刷新（六组前端行为，M5 新增）
 * ------------------------------------------------------------------ */
describe("401 单飞刷新与重放", () => {
  beforeEach(() => {
    clearCsrfCookie();
    // 刷新失败会 fire-and-forget 打一次登出端点；给个基线桩避免 onUnhandledRequest 噪音。
    server.use(http.post("/api/v1/auth/logout", () => new HttpResponse(null, { status: 204 })));
  });
  afterEach(() => {
    clearCsrfCookie();
  });

  it("并发 401 只放出一个 /auth/refresh（模块级单飞）", async () => {
    setCsrfCookie();
    let refreshCalls = 0;
    let hits = 0;
    server.use(
      http.get("/api/v1/knowledge-bases", () => {
        hits += 1;
        return hits <= 2
          ? errorResponse(401, UNAUTHENTICATED_ENVELOPE.error)
          : HttpResponse.json({ items: [] });
      }),
      http.post("/api/v1/auth/refresh", async () => {
        refreshCalls += 1;
        // 撑开单飞窗口，保证两个并发 401 都落在同一次 in-flight 上。
        await new Promise((resolve) => setTimeout(resolve, 25));
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const [a, b] = await Promise.all([
      apiRequest("/knowledge-bases").catch((cause: unknown) => cause),
      apiRequest("/knowledge-bases").catch((cause: unknown) => cause),
    ]);

    expect(refreshCalls).toBe(1);
    // 两个原请求都在刷新成功后重放成功，不再是 401 错误。
    expect(isApiError(a)).toBe(false);
    expect(isApiError(b)).toBe(false);
  });

  it("401 → 刷新成功 → 用原 init 重放一次并返回数据", async () => {
    setCsrfCookie();
    let businessCalls = 0;
    let refreshCalls = 0;
    let replayWorkspace: string | null = null;
    server.use(
      http.get("/api/v1/knowledge-bases", ({ request }) => {
        businessCalls += 1;
        replayWorkspace = request.headers.get("x-workspace-id");
        return businessCalls === 1
          ? errorResponse(401, UNAUTHENTICATED_ENVELOPE.error)
          : HttpResponse.json({ items: [{ kb_id: "kb-1" }] });
      }),
      http.post("/api/v1/auth/refresh", () => {
        refreshCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const data = await apiRequest<{ items: { kb_id: string }[] }>("/knowledge-bases", {
      workspaceId: "ws-replay",
    });

    expect(refreshCalls).toBe(1);
    expect(businessCalls).toBe(2); // 原始 + 重放各一次
    expect(replayWorkspace).toBe("ws-replay"); // 原 init 被原样重放
    expect(data.items[0]?.kb_id).toBe("kb-1");
  });

  it("刷新失败不嵌套：只打一次刷新，抛出原始 401 错误", async () => {
    setCsrfCookie();
    let refreshCalls = 0;
    server.use(
      http.get("/api/v1/knowledge-bases", () => errorResponse(401, UNAUTHENTICATED_ENVELOPE.error)),
      http.post("/api/v1/auth/refresh", () => {
        refreshCalls += 1;
        // 刷新端点自身返回 401；它走原生 fetch，不会被 apiRequest 的重放逻辑再次接管。
        return errorResponse(401, UNAUTHENTICATED_ENVELOPE.error);
      }),
    );

    const error = await apiRequest("/knowledge-bases").catch((cause: unknown) => cause);

    expect(refreshCalls).toBe(1); // 没有自递归
    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) {
      throw new Error("期望抛出 ApiError");
    }
    expect(error.status).toBe(401);
    expect(error.code).toBe("UNAUTHENTICATED");
  });

  it("apiUpload 完全不参与 401 单飞重放", async () => {
    setCsrfCookie();
    let refreshCalls = 0;
    server.use(
      http.post("/api/v1/knowledge-bases/kb-1/documents", () =>
        errorResponse(401, UNAUTHENTICATED_ENVELOPE.error),
      ),
      http.post("/api/v1/auth/refresh", () => {
        refreshCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const form = new FormData();
    form.append("file", new File(["x"], "a.pdf", { type: "application/pdf" }));
    const error = await apiUpload("/knowledge-bases/kb-1/documents", form).catch((cause: unknown) => cause);

    expect(refreshCalls).toBe(0); // 上传的 401 直接抛，不刷新、不重放 multipart
    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) {
      throw new Error("期望抛出 ApiError");
    }
    expect(error.status).toBe(401);
  });

  it("刷新请求带 X-CSRF-Token 且按会话稳定（值来自 pyknora_csrf）", async () => {
    setCsrfCookie("csrf-stable-9");
    let refreshCsrf: string | null = "sentinel";
    let businessCalls = 0;
    server.use(
      http.get("/api/v1/knowledge-bases", () => {
        businessCalls += 1;
        return businessCalls === 1
          ? errorResponse(401, UNAUTHENTICATED_ENVELOPE.error)
          : HttpResponse.json({ items: [] });
      }),
      http.post("/api/v1/auth/refresh", ({ request }) => {
        refreshCsrf = request.headers.get("x-csrf-token");
        return new HttpResponse(null, { status: 204 });
      }),
    );

    await apiRequest("/knowledge-bases");

    expect(refreshCsrf).toBe("csrf-stable-9");
  });

  it("业务写方法带 X-CSRF-Token，Bearer 兼容通道不补 CSRF", async () => {
    setCsrfCookie("csrf-write-1");
    const seen: (string | null)[] = [];
    server.use(
      http.post("/api/v1/knowledge-bases", ({ request }) => {
        seen.push(request.headers.get("x-csrf-token"));
        return HttpResponse.json({ kb_id: "kb-1" }, { status: 201 });
      }),
    );

    // 会话通道（省略 token）：写方法补 CSRF。
    await apiRequest("/knowledge-bases", { method: "POST", body: { name: "a" } });
    // Bearer 兼容通道：后端对无会话 Cookie 的请求豁免 CSRF，前端不补。
    await apiRequest("/knowledge-bases", { method: "POST", body: { name: "b" }, token: "cli-tok" });

    expect(seen).toEqual(["csrf-write-1", null]);
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
