/**
 * 直连后端的 HTTP 助手。
 *
 * 用途只有一个：**布置数据与读取事实**。真正的验收发生在浏览器里，这里不替页面
 * 断言任何东西——否则用例会变成"测试自己的 API 客户端"，页面坏了也照样绿。
 *
 * 只依赖 Node 内建的 `fetch` / `FormData` / `Blob`，不新增任何运行期依赖。
 */

import {
  API_BASE_URL,
  API_TIMEOUT_MS,
  E2E_EMBEDDING_PROFILE,
  JOB_TIMEOUT_MS,
} from "./env";

/** `POST /auth/register` 与 `/auth/login` 的返回（`TokenResponse`）。 */
export interface TokenResponse {
  readonly access_token: string;
  readonly token_type: string;
  readonly expires_in: number;
  readonly principal_id: string;
  readonly workspace_id: string;
  readonly display_name: string | null;
}

/** `POST /knowledge-bases` 的返回里本文件用到的字段。 */
export interface KnowledgeBaseResponse {
  readonly kb_id: string;
  readonly name: string;
  readonly embedding_model_key: string;
  readonly embedding_dim: number;
}

/** `GET /jobs/{job_id}`（`JobDetail`）里本文件用到的字段。 */
export interface JobResponse {
  readonly job_id: string;
  readonly status: string;
  readonly terminal: boolean;
  readonly attempt: number;
  readonly error_code: string | null;
}

/** `POST /knowledge-bases/{kb_id}/documents` 的 202 返回。 */
export interface UploadResponse {
  readonly document_id: string;
  readonly deduped: boolean;
  readonly job_id: string;
  readonly document_version_id: string;
  readonly status: string;
}

/** 列表里的文档（`DocumentTable` 需要的子集）。 */
export interface DocumentListItem {
  readonly document_id: string;
  readonly filename: string | null;
  readonly searchable: boolean;
  readonly active_index_version: number | null;
  readonly created_at: string;
}

export interface DocumentPage {
  readonly items: readonly DocumentListItem[];
}

export interface AuthContext {
  readonly token: string;
  readonly workspaceId: string;
}

function authHeaders(context: AuthContext): Record<string, string> {
  return {
    Authorization: `Bearer ${context.token}`,
    "X-Workspace-Id": context.workspaceId,
  };
}

async function request<T>(
  path: string,
  init: RequestInit,
  context: AuthContext | null,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (context !== null) {
    for (const [key, value] of Object.entries(authHeaders(context))) {
      headers.set(key, value);
    }
  }
  if (init.body !== undefined && typeof init.body === "string") {
    headers.set("content-type", "application/json; charset=utf-8");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    signal: AbortSignal.timeout(API_TIMEOUT_MS),
  });

  const text = await response.text();
  if (!response.ok) {
    throw new Error(
      `${init.method ?? "GET"} ${path} → ${response.status} ${response.statusText}\n${text}`,
    );
  }
  return (text === "" ? null : JSON.parse(text)) as T;
}

/** 注册并直接拿到令牌：注册接口会顺带建好默认工作区。 */
export async function register(
  username: string,
  email: string,
  password: string,
  displayName?: string,
): Promise<TokenResponse> {
  return request<TokenResponse>(
    "/api/v1/auth/register",
    {
      method: "POST",
      body: JSON.stringify({ username, email, password, display_name: displayName ?? username }),
    },
    null,
  );
}

/** 建知识库；`name` 必须唯一到能被人眼看出来（列表断言按名字找）。 */
export async function createKnowledgeBase(
  context: AuthContext,
  name: string,
): Promise<KnowledgeBaseResponse> {
  return request<KnowledgeBaseResponse>(
    "/api/v1/knowledge-bases",
    { method: "POST", body: JSON.stringify({ name, ...E2E_EMBEDDING_PROFILE }) },
    context,
  );
}

/** 用 multipart 上传一段文本；返回 202 体（含 `job_id`）。 */
export async function uploadTextDocument(
  context: AuthContext,
  kbId: string,
  filename: string,
  content: string,
): Promise<UploadResponse> {
  const form = new FormData();
  form.set("file", new Blob([content], { type: "text/plain" }), filename);
  return request<UploadResponse>(
    `/api/v1/knowledge-bases/${kbId}/documents`,
    { method: "POST", body: form },
    context,
  );
}

export async function getJob(context: AuthContext, jobId: string): Promise<JobResponse> {
  return request<JobResponse>(`/api/v1/jobs/${jobId}`, { method: "GET" }, context);
}

/**
 * 轮询作业直到终态。
 *
 * 只接受 `succeeded`：`failed` / `cancelled` / `superseded` 直接抛错，把后端给的
 * `error_code` 带出来——测试里最怕的就是"假装成功"。
 */
export async function waitForJobSucceeded(
  context: AuthContext,
  jobId: string,
  timeoutMs: number = JOB_TIMEOUT_MS,
): Promise<JobResponse> {
  const deadline = Date.now() + timeoutMs;
  let last: JobResponse | null = null;
  while (Date.now() < deadline) {
    last = await getJob(context, jobId);
    if (last.status === "succeeded") return last;
    if (last.terminal || last.status === "superseded") {
      throw new Error(
        `作业 ${jobId} 未成功：status=${last.status} error_code=${last.error_code ?? "(null)"}`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(
    `作业 ${jobId} 在 ${timeoutMs}ms 内没有到终态（最后一次见到的 status=${last?.status ?? "未知"}）`,
  );
}

export async function listDocuments(
  context: AuthContext,
  kbId: string,
): Promise<DocumentPage> {
  return request<DocumentPage>(
    `/api/v1/knowledge-bases/${kbId}/documents`,
    { method: "GET" },
    context,
  );
}

/**
 * 建库后立刻上传，遇到 404 重试。
 *
 * 这是实测到的**真实竞态**（报告 P1-1）：`POST /knowledge-bases` 已经回了 201，
 * 紧接着的 `POST /knowledge-bases/{id}/documents` 偶发 404 `knowledge base not found`，
 * 而库里那一行确实存在。根因在写侧：`api/deps.py:50` 的 `get_session` 是 yield 依赖，
 * 提交发生在 `session_scope` 的 `__aexit__`（`db/session.py:53`），而 FastAPI 的 yield
 * 依赖清理排在**响应发出之后**——客户端看见 201 时事务可能还没提交。
 *
 * 布置数据不是被测对象，所以这里退让成"重试并留痕"：真正要验的东西不该被 1/8 概率的
 * 竞态打断；但每次重试都打一行 `[e2e][warn]`，不静默吞掉。若某次报告里这行频繁出现，
 * 说明 P1-1 的窗口在变大。
 */
async function uploadAfterCreate(
  context: AuthContext,
  kbId: string,
  filename: string,
  content: string,
  attempts = 3,
): Promise<UploadResponse> {
  let lastError: unknown = new Error("上传未执行");
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await uploadTextDocument(context, kbId, filename, content);
    } catch (error) {
      lastError = error;
      const notFound = error instanceof Error && error.message.includes("404 Not Found");
      if (!notFound || attempt === attempts) {
        throw error;
      }
      process.stdout.write(
        `[e2e][warn] 上传 404：KB ${kbId} 刚创建就找不到（疑似 P1-1 提交晚于响应），第 ${attempt} 次重试\n`,
      );
      await new Promise((resolve) => setTimeout(resolve, 250 * attempt));
    }
  }
  throw lastError;
}

/**
 * 建库 + 传文档 + 等作业成功，一次到位。
 *
 * 检索与问答用例需要"库里已经有一份可检索的文档"，但这两条链路本身不是被测对象
 * （上传链路由 documents 用例负责）。用 API 布置比用 UI 布置快十倍，也不会因为
 * 上传面板的选择器变动而连带失败——**测什么就用什么手段，别的用最短路径**。
 */
export async function seedKbWithDocument(
  context: AuthContext,
  name: string,
  filename: string,
  content: string,
): Promise<{ kbId: string; documentId: string }> {
  const kb = await createKnowledgeBase(context, name);
  const uploaded = await uploadAfterCreate(context, kb.kb_id, filename, content);
  await waitForJobSucceeded(context, uploaded.job_id);
  return { kbId: kb.kb_id, documentId: uploaded.document_id };
}
