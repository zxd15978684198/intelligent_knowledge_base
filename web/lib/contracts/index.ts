/**
 * 契约层（N2）：线上形状的唯一出处。
 *
 * 为什么这么分：
 * 1. **REST 实体用手写 interface，Zod 只用在"运行时必须校验"的地方**。`openapi.d.ts` 是生成的
 *    大文件，业务代码直接引用 `components["schemas"][...]` 会把 pydantic 的 `description`
 *    噪音和生成路径带进每个组件；手写类型短、能写"为什么"，漂移由
 *    `tests/contract/schema-drift.test.ts` 的类型级断言兜住（方向：生成形状 extends 手写类型，
 *    后端删字段/改名/收紧可选性都会编译失败）。
 * 2. **错误信封与 SSE 必须用 Zod**：两者都不在 `openapi.json` 里——信封是 `main.py` 的中间件
 *    现拼的，SSE 是 `StreamingResponse`（没有 response_model，生成器不算进 schema）。
 *    它们又都是"决定 UI 走哪条分支"的运行时输入，光有类型不够。
 * 3. **可选项性镜像 `contracts/openapi.json` 的 `required`**：pydantic 里 `= None` 的字段
 *    在 openapi 里不 required（`error_code?: string | null`），非空默认值（`top_k: int = 10`）
 *    则被 `openapi-typescript` 渲染成 required。注意 FastAPI 序列化时**会把默认值一并输出**，
 *    所以 `?:` 只表示"类型层允许缺失"，线上并不真缺——UI 该给的兜底仍要给。
 * 4. **请求体例外**：`top_k`、`normalize` 这类"带默认值、省略有意义"的字段写成 `?:`。
 *    对漂移断言无影响（required 可赋给 optional），但调用方不该被迫重复后端默认值。
 */

import { z } from "zod";

// ────────────────────────────────────────────────────────────────
// 一、错误信封
// ────────────────────────────────────────────────────────────────

/**
 * `src/pyknora/core/errors.py` 的 17 个错误码。
 *
 * 只作为**文案映射的键**导出，不参与解析：`errorEnvelopeSchema.error.code` 是宽松的
 * `z.string()`。理由是这份清单本身还在长（`CONFLICT`/`CURSOR_EXPIRED`/`DEPENDENCY_UNAVAILABLE`/
 * `INTERNAL_ERROR` 都是实现期新增的），把前端解析绑死在闭集上，后端加一个码就会让
 * 所有错误 UI 抛异常——错误处理代码最不该在错误时再出错。
 */
export const ERROR_CODES = [
  "UNAUTHENTICATED",
  "FORBIDDEN",
  "RESOURCE_NOT_FOUND",
  "INVALID_REQUEST",
  "CONFLICT",
  "IDEMPOTENCY_CONFLICT",
  "REVISION_MISSING",
  "REVISION_CONFLICT",
  "UNSUPPORTED_FORMAT",
  "PARSE_FAILED",
  "PARSE_INCOMPLETE",
  "INDEX_INCOMPLETE",
  "STALE_ATTEMPT",
  "VERSION_SUPERSEDED",
  "CURSOR_EXPIRED",
  "DEPENDENCY_UNAVAILABLE",
  "INTERNAL_ERROR",
] as const;

/** 已知错误码。用于给码配文案，未知码走通用文案。 */
export type ErrorCode = (typeof ERROR_CODES)[number];

const KNOWN_ERROR_CODES: ReadonlySet<string> = new Set<string>(ERROR_CODES);

/** 未知码返回 false——先判再用，别把任意字符串当 `ErrorCode` 塞进映射表。 */
export function isErrorCode(value: string): value is ErrorCode {
  return KNOWN_ERROR_CODES.has(value);
}

/**
 * 错误信封：`{"error":{"code","message","retryable","details"},"request_id"}`。
 *
 * 401/403/404/409/410/422/428/500 全部共用这一形状（`main.py` 里 `AppError`、
 * `RequestValidationError`、`StarletteHTTPException`、兜底 `Exception` 四条路径都走它）。
 * `request_id` 缺省回落 `null` 而不是报错：它是排障信息，不该因为缺失就让整条错误链断掉。
 * `details` 保持 `unknown`：422 时是 `{"errors":[{loc,type,msg}]}`，其它码可能是 `null`，
 * 由使用方按 `code` 决定怎么读。这里用 `.optional()`：Zod 4 里 `z.unknown()` 作为对象字段
 * 仍要求键存在（报 `expected nonoptional`），而"信封少一个 details"是我们能容忍的偏差——
 * 缺了它错误处理照样能跑，缺了 code/message 才真的没法展示。
 */
export const errorEnvelopeSchema = z.object({
  error: z.object({
    code: z.string().min(1),
    message: z.string(),
    retryable: z.boolean().default(false),
    details: z.unknown().optional(),
  }),
  request_id: z.string().nullable().default(null),
});

export type ErrorEnvelope = z.infer<typeof errorEnvelopeSchema>;

/**
 * 宽容版解析：给 `apiRequest` 用。
 *
 * 网络层的失败响应体不一定是我们认识的信封（反代 502 的 HTML、Next 自己的 404 页面），
 * 这时返回 `null` 让调用方降级成"未知错误"即可，不要在这里抛——
 * 抛了会把"服务端返回了一个坏错误"变成"前端解析器崩了"，排障信息全丢。
 */
export function safeParseErrorEnvelope(body: unknown): ErrorEnvelope | null {
  const parsed = errorEnvelopeSchema.safeParse(body);
  return parsed.success ? parsed.data : null;
}

// ────────────────────────────────────────────────────────────────
// 二、作业状态（六态）
// ────────────────────────────────────────────────────────────────

/**
 * 作业六态。**`JobDetail.status` 在后端是裸 `str`**（`api/v1/jobs.py`），openapi 里也只有
 * `string`，所以这份闭集是前端约定，不是后端 schema 的反映；闭集在 `jobStatusSchema` 里
 * 严格校验，因为它是决定"继续轮询还是收工"的开关，认错了会空转到超时。
 */
export const JOB_STATUSES = [
  "pending",
  "processing",
  "succeeded",
  "failed",
  "cancelled",
  "superseded",
] as const;

export type JobStatus = (typeof JOB_STATUSES)[number];

export const jobStatusSchema = z.enum(JOB_STATUSES);

/**
 * 停止轮询的判据。
 *
 * `terminal` 由后端 `TERMINAL_STATUSES` 算出，但那份集合**漏了 `superseded`**
 * （`src/pyknora/services/queue/jobs.py:31`）：被新构建取代的作业线上会返回
 * `status="superseded"` 且 `terminal=false`。只看 `terminal` 就会永远轮询一个已经结束的作业，
 * 所以这里必须补一条 `status === "superseded"`。
 */
export function isJobTerminal(job: { status: string; terminal: boolean }): boolean {
  return job.terminal || job.status === "superseded";
}

// ────────────────────────────────────────────────────────────────
// 三、问答结论（三态）
// ────────────────────────────────────────────────────────────────

/** `services/rag/evidence.py:86` 的 `Verdict(StrEnum)`：大写、闭集。 */
export const ANSWER_VERDICTS = ["SUPPORTED", "INSUFFICIENT", "CONFLICTING"] as const;

export type AnswerVerdict = (typeof ANSWER_VERDICTS)[number];

export const answerVerdictSchema = z.enum(ANSWER_VERDICTS);

// ────────────────────────────────────────────────────────────────
// 四、SSE（手写 + Zod 判别）
// ────────────────────────────────────────────────────────────────

/** `DoneEvent.status`：`schemas/chat.py` 的 `RunStatus(StrEnum)`，必填无默认。 */
export const RUN_STATUSES = ["COMPLETED", "ABSTAINED", "TIMEOUT", "BUDGET_EXCEEDED", "FAILED"] as const;

export type RunStatus = (typeof RUN_STATUSES)[number];

export const runStatusSchema = z.enum(RUN_STATUSES);

/**
 * 引用条目。`page_no`/`page_end_no`/`active_document_version_id` 在 pydantic 里默认 `None`
 * 但 `model_dump(mode="json")` 会原样输出 `null`，所以这里用**必填 + nullable**（不是 `?:`）：
 * SSE 帧从不省略字段，写成 `?:` 只会让每个使用点多一次 `??`。
 */
export const citationSchema = z.object({
  chunk_id: z.string(),
  document_id: z.string(),
  page_no: z.number().int().nullable().default(null),
  page_end_no: z.number().int().nullable().default(null),
  snippet: z.string(),
  active_document_version_id: z.string().nullable().default(null),
});

export type Citation = z.infer<typeof citationSchema>;

/**
 * `retrieval` 帧：引用清单 + 降级实情。
 *
 * `degraded` 非空表示检索有一路挂了（键是分支名、值是错误码）。**必须透出**——
 * 否则用户会把"只有这些"和"一路挂了只剩一路"看成同一回事。
 */
export const retrievalEventSchema = z.object({
  citations: z.array(citationSchema),
  degraded: z.record(z.string(), z.string()).default(() => ({})),
});

export type RetrievalEvent = z.infer<typeof retrievalEventSchema>;

export const tokenEventSchema = z.object({ text: z.string() });

/**
 * `done` 帧。字段口径见 `schemas/chat.py:DoneEvent`。
 *
 * `verdict` 用 `.catch(null)`：后端声明是 `str | None`（不是 `Verdict` 枚举），
 * 将来增加第四种结论时，我们宁愿把它当作"没有结论"（不显示徽标），
 * 也不能让末帧解析失败——那会让前端拿不到 `run_id` 与 token 统计。
 * 反过来 `status` 保持严格：它是 `RunStatus(StrEnum)` 闭集，认不出就是真出问题了。
 */
export const doneEventSchema = z.object({
  status: runStatusSchema,
  run_id: z.string(),
  prompt_version: z.string().nullable().default(null),
  verdict: answerVerdictSchema.nullable().catch(null),
  invalid_citations: z.number().int().default(0),
  prompt_tokens: z.number().int().nullable().default(null),
  completion_tokens: z.number().int().nullable().default(null),
});

export type DoneEvent = z.infer<typeof doneEventSchema>;

/**
 * `error` 帧。`retryable` 保持 `?:`（与后端 `retryable: bool = False` 及任务书签名一致）。
 *
 * 注意：**`error` 之后后端还会再发一帧 `done`**（`api/v1/chat.py` 里错误分支照常收尾），
 * 所以消费方不能用"收到 error 就关流"的写法，必须继续读到 `done`。
 */
export const streamErrorEventSchema = z.object({
  code: z.string().min(1),
  message: z.string(),
  retryable: z.boolean().optional(),
});

export type StreamErrorEvent = z.infer<typeof streamErrorEventSchema>;

/**
 * 四类帧的判别联合。判别键是 SSE 的 `event:` 字段（不是帧体里的字段）。
 * 未知事件名在前端解析器里被跳过（前向兼容），所以这里只列后端真会发的四种。
 */
export const chatSseEventSchema = z.discriminatedUnion("event", [
  z.object({ event: z.literal("retrieval"), data: retrievalEventSchema }),
  z.object({ event: z.literal("token"), data: tokenEventSchema }),
  z.object({ event: z.literal("done"), data: doneEventSchema }),
  z.object({ event: z.literal("error"), data: streamErrorEventSchema }),
]);

export type ChatSseEvent = z.infer<typeof chatSseEventSchema>;

/** 后端会发的四种事件名，供解析器做"未知事件跳过"的白名单。 */
export const SSE_EVENT_NAMES = ["retrieval", "token", "done", "error"] as const;

export type SseEventName = (typeof SSE_EVENT_NAMES)[number];

// ────────────────────────────────────────────────────────────────
// 五、REST 实体（手写，镜像 contracts/openapi.json）
// ────────────────────────────────────────────────────────────────

/** 知识库的 embedding 配置。`normalize` 有非空默认值，FastAPI 一定会输出，故非可选。 */
export interface EmbeddingProfile {
  embedding_model_key: string;
  embedding_dim: number;
  chunk_size: number;
  chunk_overlap: number;
  normalize: boolean;
}

export interface KnowledgeBase {
  kb_id: string;
  workspace_id: string;
  name: string;
  profile: EmbeddingProfile;
  /** 还没建过索引时为空。 */
  active_index_build_id?: string | null;
  /** 乐观锁版本：写操作要回传 `If-Match`。 */
  row_version: number;
  generation: number;
  deleted_at?: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * 知识库列表用**游标**分页（不是 limit/offset）：`next_cursor` 为空即到底。
 * 混用两种分页是这块最容易踩的坑——见 `DocumentPage` / `ChunkPage` 的注释。
 */
export interface KnowledgeBasePage {
  items: KnowledgeBase[];
  limit: number;
  next_cursor?: string | null;
}

/** 建库请求。`normalize` 省略时后端按 `true` 处理。 */
export interface KnowledgeBaseCreate {
  name: string;
  embedding_model_key: string;
  embedding_dim: number;
  chunk_size: number;
  chunk_overlap: number;
  normalize?: boolean;
}

/** 改名（S05 只开放 name）。 */
export interface KnowledgeBasePatch {
  name: string;
}

export interface KnowledgeBaseDeleted {
  kb_id: string;
  status: string;
  generation: number;
  row_version: number;
}

/**
 * 文档列表的一行，是 `DocumentDetail` 的真子集。
 *
 * 三个指针字段（active_document_version_id / active_build_id / active_index_version）
 * 在 schema 里是"必填但可为 null"：null 表示这条文档当前**不可检索**
 * （还没发布、或已被新版本取代在途）。UI 不能用 `??` 把它们当成"字段不存在"。
 *
 * `filename` 可为空：对象键解析不出文件名时后端回落成短 document_id，用户会看到一串 id。
 */
export interface DocumentSummary {
  document_id: string;
  kb_id: string;
  filename?: string | null;
  active_document_version_id: string | null;
  active_build_id: string | null;
  active_index_version: number | null;
  generation: number;
  row_version: number;
  searchable: boolean;
  created_at: string;
}

/** 文档详情。**没有 `created_at`**（后端 Detail 模型未带），别照 Summary 抄。 */
export interface DocumentDetail {
  document_id: string;
  workspace_id: string;
  kb_id: string;
  filename?: string | null;
  active_document_version_id?: string | null;
  active_build_id?: string | null;
  active_index_version?: number | null;
  generation: number;
  row_version: number;
  searchable: boolean;
}

/** 文档列表：`limit/offset` 分页，且 `total` 是**全量条数**（能算总页数）。 */
export interface DocumentPage {
  items: DocumentSummary[];
  limit: number;
  offset: number;
  total: number;
}

/**
 * 上传/删除的受理回执（202）。
 *
 * `deduped=true` 表示同内容文档已存在、复用了旧版本——此时 HTTP 状态是 **200 而不是 202**，
 * 所以前端必须靠 `apiUpload` 返回的 `status` 区分，不能只看 body。
 */
export interface DocumentAccepted {
  document_id: string;
  deduped: boolean;
  job_id?: string | null;
  document_version_id?: string | null;
  status: string;
}

/**
 * 切片条目（`v_active_chunks` 一行）。
 *
 * 后端 `GET /documents/{id}/chunks` 的 schema 是 `list[dict[str, Any]]`（没用 `ActiveChunk`
 * 模型），openapi 里只是"任意对象"——这份形状来自 `ActiveChunk.model_dump(mode="json")`，
 * 20 个字段全部输出。因此无法在类型层做漂移比对，改后端时靠集成测试发现。
 */
export interface ChunkItem {
  chunk_id: string;
  workspace_id: string;
  kb_id: string;
  document_id: string;
  build_id: string;
  index_version: number;
  attempt_id: string;
  chunk_ordinal: number;
  content: string;
  page_no: number | null;
  page_end_no: number | null;
  heading_path: string | null;
  char_start: number | null;
  char_end: number | null;
  token_count: number | null;
  locator_ref: string | null;
  embedding_model_key: string;
  embedding_dim: number;
  chunk_size: number;
  active_document_version_id: string | null;
}

/**
 * 切片页。
 *
 * ⚠️ `total` 是**页内条数**，不是全量（后端没做 count；与 `DocumentPage.total` 语义相反）。
 * 分页 UI 只能"有下一页就一直取"，不能算总页数——照着名字用会得到永远 1 页。
 */
export interface ChunkPage {
  items: ChunkItem[];
  limit: number;
  offset: number;
  total: number;
}

/**
 * 作业详情。
 *
 * `status` 在后端是裸 `str`（openapi 只有 `string`），这里刻意收窄成 `JobStatus` 六态：
 * 前端要按状态分支渲染。收窄的漂移风险由 `jobStatusSchema` 在运行时兜，
 * 停止轮询请用 `isJobTerminal(job)` 而不是直接读 `terminal`（理由见该函数）。
 */
export interface JobDetail {
  job_id: string;
  workspace_id: string;
  document_id: string;
  job_type: string;
  status: JobStatus;
  attempt: number;
  error_code?: string | null;
  terminal: boolean;
}

/**
 * 成员一行。
 *
 * `role` 是**数据库里的一行**（`roles` 表，管理端可新建），不是闭集枚举，
 * 所以类型只能是 `string`——写成 union 会在管理员建新角色时静默失配。
 * 判断是否 owner 请用 `role === "owner"`（后端 `OWNER_ROLE` 常量）。
 */
export interface MembershipDetail {
  membership_id: string;
  workspace_id: string;
  user_id: string;
  role: string;
}

/** 成员列表。注意键名是 `members` 而不是 `items`，且**没有分页字段**（成员不分页）。 */
export interface MemberPage {
  members: MembershipDetail[];
}

/** 加成员：只收 `{user_id, role}`，没有邀请/邮件流程。 */
export interface MemberAdd {
  user_id: string;
  role?: string;
}

/**
 * 检索结果片段。
 *
 * 只有 `rrf_score` 与两路 **rank**，没有向量/关键词原始分数——后端只回融合后的排序依据。
 * `vector_rank`/`keyword_rank` 为 null 表示该路没召回这条（两路都 null 是不可能的）。
 */
export interface RetrievedChunk {
  chunk_id: string;
  workspace_id: string;
  kb_id: string;
  document_id: string;
  index_version: number;
  chunk_ordinal: number;
  content: string;
  page_no?: number | null;
  page_end_no?: number | null;
  heading_path?: string | null;
  locator_ref?: string | null;
  rrf_score: number;
  vector_rank?: number | null;
  keyword_rank?: number | null;
  active_document_version_id?: string | null;
}

/**
 * 检索响应。
 *
 * `degraded` 非空表示有一路挂了（键=分支名、值=错误码），必须让用户看见；
 * `retrieval_strategy` 是**本次实际**走的策略（baseline/candidate），A/B 归因靠它，
 * 不能拿配置反推。后端是裸 `str`，这里也留 `string`，别收窄。
 */
export interface QueryResponse {
  query: string;
  results: RetrievedChunk[];
  degraded?: Record<string, string>;
  retrieval_strategy: string;
}

/** 检索请求。`top_k` 省略时后端按 10（上限 50，超限 422 而不是静默截断）。 */
export interface QueryRequest {
  query: string;
  top_k?: number;
}

/** 流式问答请求。`top_k` 省略时后端按 6。 */
export interface ChatRequest {
  query: string;
  top_k?: number;
}

/** 登录请求。 */
export interface LoginRequest {
  username: string;
  password: string;
}

/** 注册请求。 */
export interface RegisterRequest {
  username: string;
  email: string;
  password: string;
  display_name?: string | null;
}

/** 登录/注册回执。`token_type` 恒为 bearer，`expires_in` 单位秒。 */
export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  principal_id: string;
  workspace_id: string;
  display_name?: string | null;
}

/**
 * 当前身份。`display_name` 必填但可为 null（后端 schema 里没有默认值）——
 * UI 必须准备回落（`shortId(principal_id)`）。
 */
export interface MeResponse {
  principal_id: string;
  display_name: string | null;
  workspace_id: string;
  role: string;
  permissions: string[];
}

/** 血缘里的一条版本记录。 */
export interface LineageEntry {
  document_version_id: string;
  document_id: string;
  content_hash: string;
  byte_size: number;
  parse_status: string;
  created_by_principal_id: string;
  ingestion_source: string;
  created_at: string;
  /** 时间序（0 最早）。 */
  ordinal: number;
  is_active: boolean;
}

/**
 * 文档血缘：版本链 + 贡献者。
 *
 * `active_version_id` 必填但可为 null（文档被清空/全部版本失效时）。
 */
export interface DocumentLineage {
  document_id: string;
  active_version_id: string | null;
  contributors: string[];
  versions: LineageEntry[];
}

/**
 * `/health` 存活探针回执。
 *
 * `kb_repo` 是进程级仓库的**类名**（没注入就是 null），`embedder` 是 `model_key`
 * （没注入就是 null）。两者为 null 说明"按请求构造"或"未预热"，**不是故障**——
 * 这个端点刻意不查数据库，别拿它当就绪探针。
 */
export interface HealthResponse {
  status: string;
  kb_repo: string | null;
  embedder: string | null;
}

/** `/health` 的解析器：MSW 桩与 UI 自检共用，保证桩和真实回执是同一个形状。 */
export const healthResponseSchema = z.object({
  status: z.string(),
  kb_repo: z.string().nullable(),
  embedder: z.string().nullable(),
});
