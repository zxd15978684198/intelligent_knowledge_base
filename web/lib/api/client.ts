/**
 * 统一 API 客户端（N2）。
 *
 * 只做四件事，其余交给调用方：
 * 1. 拼路径（默认 `/api/v1`，走 Next 的 rewrite 代理到后端）；
 * 2. 注入横切头：`Authorization`、`X-Workspace-ID`、`Idempotency-Key`、`If-Match`；
 * 3. 把后端错误信封翻译成 `ApiError`（`code`/`status`/`details`/`requestId` 四个字段够 UI 分流）；
 * 4. 区分上传的 202/200（`deduped`）——这是上传流程唯一的分支依据，必须从响应里带出来。
 *
 * 刻意不做的事：
 * - **不重试**。重试策略归 TanStack Query（`retry` 回调按 `ApiError.retryable` 决定），
 *   客户端里再藏一层重试会让"重试了几次"不可见，还会把幂等键用乱。
 * - **不吞异常**。特别是 `AbortError` 原样抛出：上传/流式问答的"用户点了取消"和
 *   "网络断了"必须能区分，包成 `ApiError` 就分不出来了。
 * - **不手写 multipart 的 `Content-Type`**：边界串由浏览器生成，手写必然缺 boundary，
 *   后端会 422。`apiUpload` 因此完全不碰 `Content-Type`。
 */

import { getToken } from "@/lib/auth/token-store";
import { safeParseErrorEnvelope, type ErrorEnvelope } from "@/lib/contracts";

/**
 * 业务路由的统一前缀（后端所有业务路由都是 `prefix="/api/v1"`）。
 *
 * `/health`、`/api/*`（Next 自己的路由）不在这个前缀下，见 `resolveApiPath`。
 */
export const API_V1_PREFIX = "/api/v1";

/** 网络层失败（没拿到 HTTP 响应）时用的伪状态码。0 不会与任何真实状态码冲突。 */
export const NETWORK_ERROR_STATUS = 0;

/** 错误信封解析成功但形状不认识（比如反代返回了 HTML）。 */
export const INVALID_RESPONSE_CODE = "INVALID_RESPONSE";

/** 请求根本没发出去/没收到响应（DNS、断网、CORS 预检失败、连接重置）。 */
export const NETWORK_ERROR_CODE = "NETWORK_ERROR";

/**
 * 后端错误信封的载体。
 *
 * `status` 是 HTTP 状态码（401/403/404/409/410/422/428/500…），`code` 是信封里的业务码。
 * 两者都要：状态码决定"要不要跳登录页"，业务码决定"给用户看哪句话"
 * （例如 409 可能是 `REVISION_CONFLICT` 也可能是 `IDEMPOTENCY_CONFLICT`，处理方式完全不同）。
 *
 * `requestId` 来自响应头 `X-Request-ID`（`main.py` 的中间件给每个响应都加），
 * 信封体里的 `request_id` 兜底——报错提示里带上它，用户截图就能在日志里定位。
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly requestId: string | null;

  constructor(params: {
    status: number;
    code: string;
    message: string;
    details?: unknown;
    requestId?: string | null;
    cause?: unknown;
  }) {
    super(params.message, params.cause === undefined ? undefined : { cause: params.cause });
    this.name = "ApiError";
    this.status = params.status;
    this.code = params.code;
    this.details = params.details ?? null;
    this.requestId = params.requestId ?? null;
  }
}

/** `instanceof` 在跨 realm（iframe / worker）时会失灵，这里只判 `name`。 */
export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/**
 * 请求选项。`body` 允许传普通对象（内部 `JSON.stringify`），也允许传 `FormData`/`Blob`
 * 等原生 `BodyInit`（原样透传，不设 `Content-Type`）。
 */
export interface ApiRequestInit extends Omit<RequestInit, "body"> {
  /** JSON 可序列化对象，或原生 `BodyInit`（`FormData`/`Blob`/`URLSearchParams`…）。 */
  body?: unknown;
  /**
   * 访问令牌。
   *
   * - **省略（`undefined`）**：用当前会话令牌（`lib/auth/token-store`）。各 feature 因此可以
   *   只写业务参数，不必在每个请求里重复 `useAuth().token`——漏传就是 401，而那种 401
   *   在 UI 上表现为"列表永远空"，极难定位（实测踩过：kbs/documents 两个 feature 全部漏传）。
   * - **传 `null`**：强制匿名。登录、注册、健康检查必须这条，否则会把上一次的旧令牌发出去。
   * - **传字符串**：显式指定，覆盖会话令牌（e2e 里换账号、或后台任务自带令牌时用）。
   */
  token?: string | null;
  /** 显式指定工作区。省略时后端按 token 里的 `default_workspace_id` 解析。 */
  workspaceId?: string;
  /** 上传去重键。同键重发不会产生新文档版本，后端回 200 + `deduped`。 */
  idempotencyKey?: string;
  /** 乐观锁版本号。后端会 strip `W/` 与引号，所以直接传 `row_version` 的字符串即可。 */
  ifMatch?: string;
}

/**
 * 把相对路径补成真正要 fetch 的地址。
 *
 * 三个例外：已经带 `/api/` 前缀的（可能是 `/api/v1/...`，也可能是 Next 自己的 `/api/...`）、
 * `/health`（在 v1 之外）、绝对 URL（测试里直接打 `http://localhost/...`）。
 * 其余一律补 `/api/v1`，调用方因此可以只写 `/knowledge-bases`。
 */
export function resolveApiPath(path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  const normalized = path.startsWith("/") ? path : `/${path}`;
  if (normalized === "/health" || normalized.startsWith("/health?") || normalized.startsWith("/health/")) {
    return normalized;
  }
  if (normalized.startsWith("/api/")) {
    return normalized;
  }
  return `${API_V1_PREFIX}${normalized}`;
}

/**
 * 能直接当 `fetch` body 用的原生类型。
 *
 * 这些类型不能被 `JSON.stringify`（`FormData` 会变成 `{}`，`Blob` 变成 `{}`），
 * 所以必须先认出来再决定序列化方式。
 */
function isNativeBody(body: unknown): body is BodyInit {
  if (typeof body === "string") {
    // 字符串按 JSON 值处理（`JSON.stringify("x")` → `"x"`）：本 API 的请求体都是 JSON。
    // 真要发裸文本请直接用 fetch —— 这条分支存在只是为了避免 `JSON.stringify` 抛出。
    return false;
  }
  return (
    body instanceof FormData ||
    body instanceof Blob ||
    body instanceof ArrayBuffer ||
    ArrayBuffer.isView(body) ||
    body instanceof URLSearchParams ||
    (typeof ReadableStream !== "undefined" && body instanceof ReadableStream)
  );
}

function buildHeaders(init: ApiRequestInit, jsonBody: boolean): Headers {
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }
  if (jsonBody && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  // 省略 = 用会话令牌；显式 null = 匿名（登录/注册）。两者必须分开，不能用 `??` 合并。
  const token = init.token === undefined ? getToken() : init.token;
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (init.workspaceId) {
    headers.set("X-Workspace-ID", init.workspaceId);
  }
  if (init.idempotencyKey) {
    headers.set("Idempotency-Key", init.idempotencyKey);
  }
  if (init.ifMatch) {
    headers.set("If-Match", init.ifMatch);
  }
  return headers;
}

type ReadBodyResult =
  | { kind: "empty" }
  | { kind: "json"; value: unknown }
  | { kind: "text"; value: string };

/**
 * 读响应体。区分"空"和"不是 JSON"——两者在成功路径上的处理完全不同
 * （204 是合法成功，非 JSON 是服务端违约）。
 */
async function readBody(response: Response): Promise<ReadBodyResult> {
  // 204/205 没有响应体，`res.text()` 会立即返回空串，这里提前短路避免误判。
  if (response.status === 204 || response.status === 205) {
    return { kind: "empty" };
  }
  const text = await response.text();
  if (text === "") {
    return { kind: "empty" };
  }
  try {
    return { kind: "json", value: JSON.parse(text) as unknown };
  } catch {
    return { kind: "text", value: text };
  }
}

function requestIdOf(response: Response, envelope: ErrorEnvelope | null): string | null {
  return response.headers.get("x-request-id") ?? envelope?.request_id ?? null;
}

/**
 * 判断是否"主动中止"。
 *
 * 不用 `instanceof Error` 加 `name` 两层判断：各环境的 AbortError 来源不同
 * （浏览器是 `DOMException`、Node 侧可能是 `DOMException`，某些 polyfill 只是普通对象），
 * 而 `DOMException` 在部分运行时并不继承 `Error`。只看 `name` 是跨环境最稳的判据——
 * 认错它的代价是把"用户点了取消"显示成"网络错误"，还会触发一次无意义的重试。
 */
function isAbortError(cause: unknown): boolean {
  return (
    typeof cause === "object" && cause !== null && "name" in cause && (cause as { name?: unknown }).name === "AbortError"
  );
}

/**
 * 发请求并把响应体当 JSON 反序列化。
 *
 * 返回值直接是 `T`；失败一律抛（`ApiError` 或 `AbortError`）。
 * `204` 与空响应体返回 `undefined`（调用方声明 `Promise<void>` 即可）。
 */
async function send<T>(
  path: string,
  init: ApiRequestInit,
  nativeBody?: BodyInit,
): Promise<{ data: T; status: number; headers: Headers }> {
  const { body, token: _token, workspaceId: _ws, idempotencyKey: _key, ifMatch: _match, headers: _h, ...rest } = init;

  const hasNativeBody = nativeBody !== undefined;
  const resolvedBody: BodyInit | undefined = hasNativeBody
    ? nativeBody
    : body === undefined
      ? undefined
      : isNativeBody(body)
        ? body
        : JSON.stringify(body);
  // 只有"真的是 JSON 序列化出来的"才设 Content-Type；原生 body 由浏览器自己定。
  const jsonBody = !hasNativeBody && body !== undefined && !isNativeBody(body);

  let response: Response;
  try {
    response = await fetch(resolveApiPath(path), {
      ...rest,
      headers: buildHeaders(init, jsonBody),
      body: resolvedBody,
    });
  } catch (cause) {
    // 用户/代码主动中止：原样抛出，让上层区分"取消"与"失败"。
    if (isAbortError(cause)) {
      throw cause;
    }
    throw new ApiError({
      status: NETWORK_ERROR_STATUS,
      code: NETWORK_ERROR_CODE,
      message: cause instanceof Error ? cause.message : "网络请求失败",
      cause,
    });
  }

  const bodyResult = await readBody(response);
  const envelope = bodyResult.kind === "json" ? safeParseErrorEnvelope(bodyResult.value) : null;
  const requestId = requestIdOf(response, envelope);

  if (!response.ok) {
    if (envelope) {
      throw new ApiError({
        status: response.status,
        code: envelope.error.code,
        message: envelope.error.message,
        details: envelope.error.details,
        requestId,
      });
    }
    // 非信封错误体（反代 502 HTML、框架 404 页面）：保留状态码，把原始片段塞进 message 便于排障。
    const snippet = bodyResult.kind === "text" ? bodyResult.value.slice(0, 200) : "";
    throw new ApiError({
      status: response.status,
      code: INVALID_RESPONSE_CODE,
      message: snippet === "" ? `请求失败（HTTP ${response.status}）` : `请求失败（HTTP ${response.status}）：${snippet}`,
      requestId,
    });
  }

  if (bodyResult.kind === "empty") {
    // 204 与"声明了 JSON 但返回空"都走这里；`undefined as unknown as T` 是唯一诚实的表达，
    // 调用方若真需要 T 就该自己校验（我们不会替它编一个 {} 出来）。
    return { data: undefined as unknown as T, status: response.status, headers: response.headers };
  }
  if (bodyResult.kind === "text") {
    throw new ApiError({
      status: response.status,
      code: INVALID_RESPONSE_CODE,
      message: `期望 JSON 响应，实际收到非 JSON（前 200 字符：${bodyResult.value.slice(0, 200)}）`,
      requestId,
    });
  }
  return { data: bodyResult.value as T, status: response.status, headers: response.headers };
}

/**
 * 单次 JSON 请求。
 *
 * ```ts
 * const page = await apiRequest<DocumentPage>(`/knowledge-bases/${kb}/documents?limit=50&offset=0`, {
 *   token, workspaceId: ws,
 * });
 * ```
 */
export async function apiRequest<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  const { data } = await send<T>(path, init);
  return data;
}

/**
 * 上传（multipart）。与 `apiRequest` 的差别只有两点，但都必须：
 *
 * 1. **不设 `Content-Type`** —— 浏览器从 `FormData` 生成带 boundary 的那一个，
 *    手写会丢 boundary，后端解析直接失败。
 * 2. **方法固定 `POST`** —— 不在调用点重复写 `method`：漏写时 fetch 默认 `GET`，
 *    请求还是"成功"的（服务端把 GET 当列表查询），错得很安静。
 * 3. **返回状态码** —— 后端对新文档回 `202`，命中 `Idempotency-Key` 去重时回
 *    `200 {deduped: true}`（`api/v1/documents.py`）。只读 body 会让 UI 分不清
 *    "刚受理" 和 "早就传过"（后者不该再提示"正在解析"）。
 */
export async function apiUpload<T>(
  path: string,
  form: FormData,
  init: ApiRequestInit = {},
): Promise<{ data: T; status: number }> {
  const { data, status } = await send<T>(path, { ...init, method: "POST" }, form);
  return { data, status };
}
