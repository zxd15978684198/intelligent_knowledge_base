# PyKnora 前端实施计划（Next.js）

> 定位：**Day4 前置**。讲义把 Web 界面放在 Day4（`PyKnora_Day03_学生实战讲义.md` §十三），
> 不占 Day3 的评分维度，因此单独成文，与 `PyKnora_Day03_可信RAG闭环实施计划.md` 解耦。
> 状态：**待评审（提议）**；契约部分已于 2026-10-30 按当前工作区代码逐字段核准（见 §4、§12）。
> 本文件不含评测内容；评测见 Day3 计划。
>
> **口径**：§2、§4、§5、§7 里出现的每个路径、参数名、字段名、状态码、错误码，均在
> `src/pyknora/` 里核对过并附 `文件:行号`。计划正文不再有"凭印象写"的字段名；
> 与旧版不一致之处集中登记在 §12（旧值 → 真实值 → 出处）。

---

## 0. 为什么单独一份

Day3 的评分只看"可信 RAG 闭环"（评测集、失败定位、证据与引用、拒答与停止）。前端要做的是
"把这套能力展示出来并让人能用"，依赖的是**后端已经稳定的契约**——契约这轮已经补齐（跨页引用、
文档列表、统一错误体、`degraded`、`done` 事件），所以现在做前端不会反复返工；但把它混进
Day3 计划只会让两份交付物都说不清验收口径。

---

## 1. 决策（F1–F7）

| # | 决定 | 理由 | 被否方案 |
| --- | --- | --- | --- |
| F1 | **Next.js App Router**（用户已拍板） | 文件路由 + 原生 `fetch` 支持流式；社区资料多，教学场景容易讲 | Vite+React（要自己配路由/代理）、Remix（教学成本高） |
| F2 | **同源代理**：`next.config.ts` 的 `rewrites` 把 `/api/:path*` 转发到后端 | 后端**无 CORS**（`src/pyknora/api/main.py` 全文无 `CORSMiddleware`），也**无 `StaticFiles`**（不托管前端产物） | 后端加 CORS（要动共用文件、还要收紧 `allow_origins`）；前端直连 8010（跨域必挂） |
| F3 | 登录态 = **`localStorage` + React context** | 后端只认 `Authorization: Bearer`，没有 cookie 通道；改 cookie 要动 `api/deps.py` 并引入 CSRF | httpOnly cookie（后端不支持，等于重做鉴权） |
| F4 | 契约类型**手写**在 `lib/types.ts`，不引生成器 | `/api/v1` 共 **30 条**路由，前端本轮用到 **18 条**（§4 逐条列出）；没用到的 12 条是 `workspaces/*`（11）+ `admin/reindex`（1）。引 openapi-generator 要多一套构建链 | `openapi-typescript`（收益不抵配置成本） |
| F5 | SSE 用 **`fetch` + `ReadableStream`**，不用 `EventSource` | `EventSource` 不支持 POST、不支持自定义 `Authorization` 头 | EventSource（鉴权走不通） |
| F6 | 不引 UI 库 / 状态库 / 数据请求库 | 4 个页面、1 个用户；`useState`+`useContext` 够用 | Tailwind（可选，但要额外构建配置）、shadcn、`swr` |
| F7 | **纯 CSR**，不做 SSR 数据获取 | token 在 `localStorage`，SSR 拿不到；做了要维护两套路径 | RSC 直取数据（要有会话 cookie 才成立） |

---

## 2. 后端契约事实（前端能依赖什么）

| # | 事实 | 证据（本工作区实测行号） |
| --- | --- | --- |
| B1 | 业务路由 **31 条**：`/api/v1/*` **30 条** + `GET /health` 1 条。另有 4 条框架路由（`/docs`、`/docs/oauth2-redirect`、`/redoc`、`/openapi.json`），`app.routes` 合计 35 个 route 对象 | 枚举 `create_app().routes`（含 `_IncludedRouter.original_router.routes`）：`/api/v1` 前缀由 `src/pyknora/api/main.py:291-301` 统一加 |
| B2 | 上传成功 **202**；内容 hash 命中已有版本 → **200** 且 `deduped=true`（此时 `job_id` 为 `null`、`status="deduped"`）；正常入队 `status="pending"` | `src/pyknora/api/v1/documents.py:151-157`（声明 202）、`:194-196`（改 200）、`:198-204`；`src/pyknora/services/ingest/documents.py:149-155`、`:170-176` |
| B3 | `JobDetail` = `job_id/workspace_id/document_id/job_type/status/attempt/error_code/terminal`，**没有进度百分比**，也**没有** `created_at/updated_at`。`status` 是**五态** `pending/processing/succeeded/failed/cancelled`，`terminal` = 后三者 | `src/pyknora/api/v1/jobs.py:33-44`；`src/pyknora/services/queue/jobs.py:29-35` |
| B4 | `POST .../query` 的 `top_k` ∈ 1..**50**（默认 **10**）、`query` ≤ **1000** 字符；`POST .../chat` 的 `top_k` ∈ 1..**20**（默认 **6**）、`query` ≤ **8000** 字符。超上限一律 422，不静默截断 | `src/pyknora/schemas/query.py:20`（`MAX_TOP_K: Final[int] = 50`）、`:28-51`；`src/pyknora/schemas/chat.py:11-15` |
| B5 | 检索结果 `RetrievedChunk` 带 `rrf_score / vector_rank / keyword_rank / page_no / page_end_no / active_document_version_id` | `src/pyknora/schemas/query.py:54-86` |
| B6 | SSE 顺序固定：`retrieval` 必为第一帧 → 之后 `token`* → `done`（终态）。`error` 之后**一定还有一帧 `done`**（`status=FAILED`） | `src/pyknora/api/v1/chat.py:215-218`、`:250-282`、`:262-267` |
| B7 | `retrieval.degraded` 非空 = 只用到一路召回；键是 `"vector"` 或 `"keyword"`，值是错误码字符串 | `src/pyknora/schemas/chat.py:39-48`；`src/pyknora/services/retrieval/search.py:42-43`、`:236-244` |
| B8 | `DoneEvent.invalid_citations` = 正文里 `[资料 N]` 的序号**越界**条数：模型编了不存在的序号（N<1），或引用了被 `chat_max_context_chunks`（默认 6）截掉的那几条（N>context_size） | `src/pyknora/services/rag/answer.py:119-126`；`src/pyknora/api/v1/chat.py:186`、`:193-200` |
| B9 | 上传限额 **50 MiB**（`settings.max_upload_bytes`）；后缀白名单 `.pdf/.docx/.txt/.md/.markdown/.html/.htm`（7 个） | `src/pyknora/core/settings.py:97`；`src/pyknora/services/ingest/parser.py:19` |
| B10 | 跨租户访问 query/chat/documents 一律 **404**（与"不存在"同形，无从区分） | `src/pyknora/api/deps.py:156-180` |
| B11 | **所有**错误（业务 `AppError`、请求校验、框架 `HTTPException`、未捕获异常）都走同一体 `{"error":{code,message,retryable,details},"request_id"}`，并带响应头 `X-Request-ID`。请求校验错误 422 且**不回显输入值**（只留 `loc/type/msg` 三元组，最多 20 条） | `src/pyknora/api/main.py:203-269`；`src/pyknora/core/errors.py:86-95` |
| B12 | `DocumentSummary` **没有 `filename`** 字段（`DocumentSummary`/`DocumentDetail` 两个响应模型都没有），9 个字段见 §4.3；`searchable` 由 `build.state == "active"` 推导 | `src/pyknora/api/v1/documents.py:119-134`、`:248` |
| B13 | 本机 `node v26.5.1` / `pnpm 12.6.0`（`npm 11.17.0`） | 本工作区实测 `node --version` / `pnpm --version` |

> **Day3 契约已全部落地（不再是"将要新增"）**：`done.status`、`done.run_id`、`done.prompt_version`、
> `done.verdict` 已在 `src/pyknora/schemas/chat.py:90-126` 里是**必填/现有字段**。
> 唯一要注意的是 `retrieval_strategy`：它**只存在于 `POST .../query` 的响应体**
> （`src/pyknora/schemas/query.py:105-113`），**SSE 的 `retrieval` 事件里没有这个字段**
> （`src/pyknora/schemas/chat.py:39-48` 只有 `citations`/`degraded`）——前端不要等它出现。

---

## 3. 目录与依赖

```
web/
├── package.json                 # next / react / react-dom / typescript / @types/*
├── next.config.ts               # rewrites: /api/:path* -> ${PYKNORA_API_BASE}
├── tsconfig.json
├── app/
│   ├── layout.tsx               # 全局壳（AuthProvider）
│   ├── globals.css
│   ├── login/page.tsx
│   ├── kbs/page.tsx
│   ├── kbs/[kbId]/page.tsx
│   └── chat/page.tsx
├── components/                  # CitationPanel / JobBadge / DegradedBanner / UploadForm
└── lib/
    ├── api.ts                   # 手写契约客户端（fetch + 统一错误解析）
    ├── types.ts                 # 契约类型（字段名逐条对齐 §4，不引生成器）
    └── sse.ts                   # SSE 帧解析（纯函数）
```

**依赖克制**：不引 UI 组件库（先用原生 CSS + `globals.css` 变量），不引状态库
（`useState`/`useContext` 够），不引 `swr`/`react-query`（4 个页面）。理由：这是教学项目，
多一个依赖就多一层"为什么"要解释。

---

## 4. 前端调用契约表（核准版）

> 每行都带代码出处。**路径一律以 `/api/v1` 为前缀**（`/health` 除外），所有响应都是 JSON
> （聊天是 `text/event-stream`）。
> 本表覆盖 **18 条 `/api/v1` 路由**（页面必需的 17 条 + 血缘 1 条可选），另加 `/health`；
> 其余 12 条在 §4.6 只列不实现。

### 4.0 三个通用约定

**① 请求头**

| 头 | 何时必带 | 说明 | 出处 |
| --- | --- | --- | --- |
| `Authorization: Bearer <access_token>` | 除 `/auth/register`、`/auth/login`、`/health` 外**全部** | 缺失 → 401 `UNAUTHENTICATED` `"missing bearer token"` | `src/pyknora/api/deps.py:59`、`:56-94` |
| `X-Workspace-ID` | **可选**（大写 `ID`，大小写敏感） | 不传时回落到 token 内的 `default_workspace_id`（登录即写入）。仅"一个人属于多个空间要切换"时才需要显式带；值必须是 UUID 且本人是成员，否则 403 `"invalid workspace id"` / `"not a member of this workspace"` | `src/pyknora/api/deps.py:60`、`:76`；`src/pyknora/services/auth/jwt.py:69-70`、`:76-84` |
| `If-Match: "<row_version>"` | `PATCH`/`DELETE /knowledge-bases/{kb_id}` **必带** | 缺失 → **428 `REVISION_MISSING`**；`row_version` 已变 → **409 `REVISION_CONFLICT`**。接受 `"3"` 与 `W/"3"` 两种写法 | `src/pyknora/api/v1/knowledge_bases.py:59-72`、`:157`、`:180`；`src/pyknora/services/kb/service.py:62` |
| `Idempotency-Key` | 上传**建议必带**（`crypto.randomUUID()`） | 同键重复上传→落同一作业；同键换了内容 → 409 `IDEMPOTENCY_CONFLICT`。**去重判定靠内容 hash，不是靠这个头** | `src/pyknora/api/v1/documents.py:165`；`src/pyknora/services/queue/jobs.py:89` |
| `Content-Type: application/json` | 除上传外全部（上传是 `multipart/form-data`，**不要手写**，交给 `FormData`） | — | `src/pyknora/api/v1/documents.py:161` |

**② 错误体与错误码**（`code → HTTP`，全部来自 `src/pyknora/core/errors.py:12-60`）

```ts
type ApiError = {
  error: { code: string; message: string; retryable: boolean; details: Record<string, unknown> };
  request_id: string;
};
```

| `error.code` | HTTP | `retryable` | 前端动作 |
| --- | --- | --- | --- |
| `UNAUTHENTICATED` | 401 | false | 清 token → 跳 `/login` |
| `FORBIDDEN` | 403 | false | 顶部提示"当前角色无此权限"（`message` 里有缺的权限点） |
| `RESOURCE_NOT_FOUND` | 404 | false | "资源不存在或无权限"（后端刻意不区分） |
| `INVALID_REQUEST` | 422 | false | 展示 `details.errors[*].msg`（校验错误）；或 `details.max_bytes` 等 |
| `UNSUPPORTED_FORMAT` | 422 | false | 展示 `details.supported` 列表 |
| `PARSE_FAILED` / `PARSE_INCOMPLETE` | 422 | false | 作业 `error_code` 里会出现，转"重新上传" |
| `CONFLICT` | 409 | false | 注册重名 → "用户名/邮箱已被占用" |
| `IDEMPOTENCY_CONFLICT` | 409 | false | 同幂等键不同内容，换 key 重传 |
| `REVISION_CONFLICT` | 409 | false | 重新 `GET` 拿新 `row_version` 再改 |
| `REVISION_MISSING` | 428 | false | 忘了带 `If-Match`，补上（前端 bug） |
| `CURSOR_EXPIRED` | 410 | false | 列表游标失效，从第一页重拉 |
| `STALE_ATTEMPT` / `VERSION_SUPERSEDED` | 409 | true | 作业层重试语义，前端只展示 |
| `INDEX_INCOMPLETE` | 502 | true | 索引未就绪，可重试 |
| `DEPENDENCY_UNAVAILABLE` | 503 | true | 后端未配 embedder / 模型：提示"服务未配置完整" |
| `INTERNAL_ERROR` | 500 | true | "出错了，请重试" + `request_id`（便于对日志） |

**③ 分页有三种，别混**

| 形态 | 用在 | 分页参数 | 翻页标记 |
| --- | --- | --- | --- |
| 游标 | `GET /knowledge-bases` | `limit`(1..100, 默认 20) + `cursor` | 响应 `next_cursor`（`null` 表示到头） |
| 偏移（全量 total） | `GET /knowledge-bases/{kb_id}/documents` | `limit`(1..500, 默认 50) + `offset`(≥0) | 响应 `total` = **全量条数** |
| 偏移（**页内** total） | `GET /documents/{id}/chunks` | `limit`(1..500, 默认 100) + `offset` | 响应 `total` = **本页条数**（`len(chunks)`），**不能当总数用** |

出处：`src/pyknora/api/v1/knowledge_bases.py:110-111`；`src/pyknora/api/v1/documents.py:219-220`、`:253-254`、`:303-304`、`:317`。

---

### 4.1 认证（`src/pyknora/api/v1/auth.py`）

| 方法 路径 | 请求 | 响应字段 | 成功码 | 主要错误 | 出处 |
| --- | --- | --- | --- | --- | --- |
| `POST /api/v1/auth/register` | `username`(3..64, `^[A-Za-z0-9_.-]+$`)、`email`(3..255, 只校验含 `@`)、`password`(8..128 字符且 ≤72 字节)、`display_name?`(≤128) | `access_token`、`token_type="bearer"`、`expires_in`、`principal_id`、`workspace_id`、`display_name` | **201** | 409 `CONFLICT` "account already exists"（**不区分**用户名/邮箱）；422 校验 | `auth.py:47-75`、`:85-99`、`:123-128`、`:105` |
| `POST /api/v1/auth/login` | `username`(1..64)、`password`(1..128) | 同上 `TokenResponse` | 200 | 401 `UNAUTHENTICATED` "invalid credentials"；403 `FORBIDDEN`（无成员关系） | `auth.py:78-82`、`:177-181` |
| `GET /api/v1/auth/me` | 无 body | `principal_id`、`display_name`、`workspace_id`、`role`、`permissions: string[]`（已排序，**无 email**） | 200 | 401 / 403 "not a member of this workspace" | `auth.py:212-225`、`:228`、`:261` |

> 注册会**顺带建一个工作空间**并把 `workspace_id` 写进 token，所以前端不需要"选空间"就能开工
> （`src/pyknora/services/auth/jwt.py:69-70`）。

### 4.2 知识库（`src/pyknora/api/v1/knowledge_bases.py` + `src/pyknora/schemas/kb.py`）

**类型**（`src/pyknora/schemas/kb.py:30-50`）

```ts
type EmbeddingProfile = {           // schemas/kb.py:15-27
  embedding_model_key: string; embedding_dim: number;
  chunk_size: number; chunk_overlap: number; normalize: boolean;
};
type KnowledgeBase = {              // schemas/kb.py:30-42
  kb_id: string; workspace_id: string; name: string;
  profile: EmbeddingProfile;        // ★ 嵌套，不是扁平字段
  active_index_build_id: string | null;
  row_version: number; generation: number;
  deleted_at: string | null; created_at: string; updated_at: string;
};
type KnowledgeBasePage = { items: KnowledgeBase[]; next_cursor: string | null; limit: number }; // :45-50
```

| 方法 路径 | 请求 | 响应 | 成功码 | 主要错误 | 权限点 | 出处 |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /api/v1/knowledge-bases` | query `limit`(1..100,=20)、`cursor` | `KnowledgeBasePage` | 200 | 410 `CURSOR_EXPIRED`；403 | `knowledge_base.read` | `knowledge_bases.py:102-111` |
| `POST /api/v1/knowledge-bases` | 6 字段：`name`(1..200)、`embedding_model_key`(1..500)、`embedding_dim`(>0)、`chunk_size`(>0)、`chunk_overlap`(≥0)、`normalize`(默认 true)；`extra="forbid"` | `KnowledgeBase` | **201** | 422（`chunk_overlap >= chunk_size` 也算 422）；403 | `knowledge_base.write` | `knowledge_bases.py:31-39`、`:75-80`、`:86-87` |
| `GET /api/v1/knowledge-bases/{kb_id}` | — | `KnowledgeBase` | 200 | 404；403 | `knowledge_base.read` | `knowledge_bases.py:131-135`、`:143` |
| `PATCH /api/v1/knowledge-bases/{kb_id}` | **`If-Match` 必带**；body 只有 `name` | `KnowledgeBase` | 200 | 428 `REVISION_MISSING`、409 `REVISION_CONFLICT`、404 | `knowledge_base.write` | `knowledge_bases.py:42-47`、`:59-72`、`:147-157` |
| `DELETE /api/v1/knowledge-bases/{kb_id}` | **`If-Match` 必带** | `KnowledgeBaseDeleted{kb_id,status="purging",generation,row_version}`；响应头 `ETag: "<row_version>"` | **202** | 428、409、404 | `knowledge_base.delete` | `knowledge_bases.py:50-57`、`:168-192` |

### 4.3 文档 / 切片 / 血缘（`src/pyknora/api/v1/documents.py`、`lineage.py`）

**类型**（全部在 `src/pyknora/api/v1/documents.py`，响应模型就地定义）

```ts
type DocumentAccepted = {                    // documents.py:82-92
  document_id: string; deduped: boolean;
  job_id: string | null;                     // ★ 去重命中时为 null
  document_version_id: string | null;
  status: string;                            // "pending" | "deduped" | "purging"
};
type DocumentSummary = {                     // documents.py:119-134 —— 没有 filename！
  document_id: string; kb_id: string;
  active_document_version_id: string | null; active_build_id: string | null;
  active_index_version: number | null; generation: number; row_version: number;
  searchable: boolean; created_at: string;
};
type DocumentDetail = {                      // documents.py:94-107
  document_id: string; workspace_id: string; kb_id: string;
  active_document_version_id: string | null; active_build_id: string | null;
  active_index_version: number | null; generation: number; row_version: number;
  searchable: boolean;                       // ★ 比 DocumentSummary 多 workspace_id、少 created_at
};
type DocumentPage = { items: DocumentSummary[]; limit: number; offset: number; total: number }; // :137-143
type ChunkPage = {                           // documents.py:110-116
  items: ActiveChunk[];                      // 运行时是 ActiveChunk 的 20 字段（models 见下）
  limit: number; offset: number; total: number;   // ★ total = 页内条数
};
type ActiveChunk = {                         // schemas/document.py:47-85
  chunk_id: string; workspace_id: string; kb_id: string; document_id: string;
  build_id: string; index_version: number; attempt_id: string; chunk_ordinal: number;
  content: string; page_no: number | null; page_end_no: number | null;
  heading_path: string | null; char_start: number; char_end: number;
  token_count: number; locator_ref: string | null;
  embedding_model_key: string; embedding_dim: number; chunk_size: number;
  active_document_version_id: string | null;
};
```

| 方法 路径 | 请求 | 响应 | 成功码 | 主要错误 | 权限点 | 出处 |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /api/v1/knowledge-bases/{kb_id}/documents` | query `limit`(1..500,=50)、`offset`(≥0) | `DocumentPage`（`total` 全量） | 200 | 404、403 | `document.read` | `documents.py:207-220`、`:253-254` |
| `POST /api/v1/knowledge-bases/{kb_id}/documents` | `multipart/form-data`，**文件字段名就是 `file`**；头 `Idempotency-Key?` | `DocumentAccepted` | **202**；去重命中 **200** | 422 `UNSUPPORTED_FORMAT`（`details.supported`）、422 `INVALID_REQUEST`（`details={max_bytes,declared_bytes/at_least_bytes}`）、409 `IDEMPOTENCY_CONFLICT`、404、503 `DEPENDENCY_UNAVAILABLE` | `document.write` | `documents.py:151-204`；`settings.py:97`；`parser.py:19` |
| `GET /api/v1/documents/{document_id}` | — | `DocumentDetail` | 200 | 404 | `document.read` | `documents.py:257-272` |
| `GET /api/v1/documents/{document_id}/chunks` | query `limit`(1..500,=100)、`offset`(≥0) | `ChunkPage` | 200 | 404、403 | **`chunk.read`** | `documents.py:292-318`；`schemas/document.py:47-85` |
| `DELETE /api/v1/documents/{document_id}` | — | `DocumentAccepted{deduped:false, status:"purging", job_id?: string/null}` | **202** | 404、403 | `document.delete` | `documents.py:321-340` |
| `GET /api/v1/documents/{document_id}/lineage` | — | `DocumentLineage{document_id,active_version_id,contributors[],versions[]}`；`versions[*]` = `document_version_id/document_id/content_hash/byte_size/parse_status/created_by_principal_id/ingestion_source/created_at/ordinal/is_active` | 200 | 404 | `lineage.read` | `lineage.py:28-35`、`:48`；`schemas/lineage.py:22-57` |

### 4.4 作业（`src/pyknora/api/v1/jobs.py`）

```ts
type JobDetail = {                 // jobs.py:33-44
  job_id: string; workspace_id: string; document_id: string; job_type: string;
  status: "pending" | "processing" | "succeeded" | "failed" | "cancelled";  // 五态
  attempt: number; error_code: string | null; terminal: boolean;
};
```

| 方法 路径 | 请求 | 响应 | 成功码 | 主要错误 | 权限点 | 出处 |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /api/v1/jobs/{job_id}` | — | `JobDetail` | 200 | 404 | `job.read` | `jobs.py:47-52`、`:60` |
| `POST /api/v1/jobs/{job_id}/cancel` | 无 body | `JobDetail`（**当前状态**，不是"取消结果") | 200 | 404 | `job.cancel` | `jobs.py:73-100` |

> 取消**幂等且不报错**：作业已终态时原样返回当前 `JobDetail`；只有 `pending` 会被改成
> `cancelled`，`processing` 改不动（`jobs.py:88-100`、`:113-117`、`:126-141`）。
> 前端轮询到 `terminal === true` 就停；没有百分比，别画进度条，用五态徽标。

### 4.5 检索与问答

**`QueryResponse`**（`src/pyknora/schemas/query.py:89-113`）

```ts
type RetrievedChunk = {            // schemas/query.py:54-86
  chunk_id: string; workspace_id: string; kb_id: string; document_id: string;
  index_version: number; chunk_ordinal: number; content: string;
  page_no: number | null; page_end_no: number | null;
  heading_path: string | null; locator_ref: string | null;
  rrf_score: number; vector_rank: number | null; keyword_rank: number | null;
  active_document_version_id: string | null;
};
type QueryResponse = {             // schemas/query.py:89-113
  query: string; results: RetrievedChunk[];
  degraded: Record<string, string>;          // {} = 两路都成功
  retrieval_strategy: "baseline" | "candidate";   // ★ 只在 /query 有，SSE 没有
                                                   // （schema 声明为 str，取值来自 settings.retrieval_strategy）
};
```

**SSE 四个事件的负载**（`src/pyknora/schemas/chat.py`）

```ts
type Citation = {                  // schemas/chat.py:18-36
  chunk_id: string; document_id: string;
  page_no: number | null; page_end_no: number | null;
  snippet: string;                 // ★ = chunk.content[:500]，是截断文本
  active_document_version_id: string | null;
};
type RetrievalEvent = { citations: Citation[]; degraded: Record<string, string> };  // :39-48
type TokenEvent = { text: string };                                                 // :51-52
type DoneEvent = {                 // schemas/chat.py:90-126
  prompt_tokens: number | null;    // 拒答/超时路径可能为 null（usage 为 {}）
  completion_tokens: number | null;
  invalid_citations: number;       // 默认 0
  status: "COMPLETED" | "ABSTAINED" | "TIMEOUT" | "BUDGET_EXCEEDED" | "FAILED";  // 必填
  run_id: string;                  // 必填，uuid4().hex
  prompt_version: string | null;   // 现为 "pyknora-rag-v1"
  verdict: "SUPPORTED" | "INSUFFICIENT" | "CONFLICTING" | null;
};
type StreamErrorEvent = { code: string; message: string; retryable: boolean };      // :129-139
```

| 方法 路径 | 请求 | 响应 | 成功码 | 主要错误 | 权限点 | 出处 |
| --- | --- | --- | --- | --- | --- | --- |
| `POST /api/v1/knowledge-bases/{kb_id}/query` | `query`(1..1000)、`top_k`(1..50,=10) | `QueryResponse` | 200 | 422、404、503（embedder 未配置） | `query.run` | `query.py:25-49`、`:64-69`；`schemas/query.py:20-51` |
| `POST /api/v1/knowledge-bases/{kb_id}/chat` | `query`(1..8000)、`top_k`(1..20,=6) | `text/event-stream`：`retrieval` → `token`* → `done` | 200 | **建流前**：422、404、503（embedder 或 `chat_api_key` 未配置）；**建流后**：只有 `error` 事件 | `chat.run` | `chat.py:48`、`:91-113`、`:215-287`；`schemas/chat.py:11-15` |

> **`retrieval.citations` ≠ 检索命中全集**：它是门控放行后再按 `chat_max_context_chunks`（默认 6）
> 截断的那批，也就是"模型真正读到的资料"。拒答（`ABSTAINED`）时通常是空数组。
> 出处：`src/pyknora/api/v1/chat.py:144-169`；`src/pyknora/services/rag/answer.py:95-111`。

### 4.6 健康探针 + 本轮不实现的端点

| 方法 路径 | 响应 | 出处 |
| --- | --- | --- |
| `GET /health` | `{status:"ok", kb_repo: string?, embedder: string?}`（两者在未注入时是 `null`），**不查库** | `src/pyknora/api/main.py:271-279` |

本轮不做、但契约存在的端点（**只列出，不实现**，避免有人以为漏了）：

| 端点 | 权限点 | 出处 |
| --- | --- | --- |
| `GET/POST/DELETE /api/v1/workspaces/{ws_id}/members`、`PATCH .../members/{membership_id}`、`GET .../permissions` | `workspace.manage_members` | `workspaces.py:69-73`、`:433-472`、`:529-533`、`:650-655` |
| `GET /api/v1/workspaces/{ws_id}/roles`、`POST/DELETE .../roles`、`PUT .../roles/{role_key}/permissions` | `workspace.manage_members` / `platform.manage_roles` | `workspaces.py:195-233`、`:288-294`、`:350-355` |
| `GET /api/v1/workspaces/{ws_id}/permissions` | 无（本人权限） | `workspaces.py:163-167` |
| `PUT /api/v1/workspaces/{ws_id}/owner` | `workspace.manage_members` | `workspaces.py:586-591` |
| `POST /api/v1/admin/reindex` | `index.rebuild` | `admin.py:81-105` |

---

## 5. 登录态、请求头与权限

- `access_token` 存 `localStorage`（键 `pyknora.token`），同存 `workspace_id`（可选）；`AuthProvider`
  启动时若有 token 就调 `GET /api/v1/auth/me` 验活，401 就清掉并跳 `/login`。
- **不引 cookie**：见 F3。
- **请求头统一在 `lib/api.ts` 一处注入**：`Authorization` 必带，`X-Workspace-ID` 仅在"多空间切换"
  时带（大写 `ID`）；上传额外带 `Idempotency-Key`；改/删 KB 额外带 `If-Match`。
- 按钮级权限：`/auth/me` 返回 `permissions`，按需 `disabled` + tooltip 说明缺哪个权限。**不隐藏**
  （隐藏会让人以为功能不存在），置灰并给原因。前端会用到的权限点：
  `knowledge_base.read/write/delete`、`document.read/write/delete`、`chunk.read`、
  `job.read/cancel`、`query.run`、`chat.run`、`lineage.read`
  （15 个工作区权限常量见 `src/pyknora/services/auth/permissions.py:24-44`，另有平台级
  `platform.manage_roles` 在 `:56`、只由平台持有；四个内置角色 `owner/admin/member/guest`
  的授权矩阵见 `:99-107`——`member` = 全部 − 管理面三件，`guest` = 7 个只读点）。
- 错误处理按 `error.code` 分支，映射见 §4.0 ②。
- **`If-Match` 的前置动作**：`PATCH`/`DELETE /knowledge-bases/{kb_id}` 之前必须先
  `GET /api/v1/knowledge-bases/{kb_id}` 拿 `row_version`，否则 428。删完/改完要把新
  `row_version`（或 `ETag`）写回本地缓存，不然第二次操作必 409。

---

## 6. SSE 解析（`lib/sse.ts`）

`EventSource` 用不了（F5），所以：

```ts
// 伪代码：fetch + ReadableStream + TextDecoder，按 "\n\n" 切帧
const res = await fetch(`/api/v1/knowledge-bases/${kbId}/chat`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    // "X-Workspace-ID": workspaceId,   // 可选，多空间切换时才带
  },
  body: JSON.stringify({ query, top_k: 6 }),   // top_k 上限 20
});
if (!res.ok) { /* 建流前失败：走 §4.0 的统一错误体分支 */ }
const reader = res.body!.getReader();
const decoder = new TextDecoder();
let buffer = "";
for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  // 每帧形如 "event: token\ndata: {...}\n\n"（data 是单行 JSON，中文不转义）
  while (buffer.includes("\n\n")) { /* 切出 event/data，JSON.parse data */ }
}
```

**事件序列（真实分支，不是理想路径）**

1. `retrieval` —— **永远第一帧**，必到（`chat.py:215-218`）。带 `citations` + `degraded`。
2. 之后三选一：
   - 预算在建流前就失效：`token`（拒答文案）→ `done`（`status` = `TIMEOUT` 或
     `BUDGET_EXCEEDED`）→ 结束（`chat.py:220-227`）；
   - 正常：`token`* → `done`（`COMPLETED` / `ABSTAINED` / `TIMEOUT` / `BUDGET_EXCEEDED`）
     （`chat.py:250-282`）；
   - 上游出错：`error` → **`done`（`status="FAILED"`）** → 结束（`chat.py:262-267`）。

**必须处理的四个边界**

1. `error` 后仍会有一帧 `done`：解析器不能在 `error` 就 `break`，否则把"上游模型失败"显示成"网络错误"。
2. `retrieval.citations` 可能为空（`ABSTAINED` 路径）：仍要渲染"没有可引用资料"，再显示拒答正文。
3. `degraded` 非空：横幅写"本次回答只用到向量/关键词一路召回，结果可能不完整"，并带上错误码。
4. `done.prompt_tokens`/`completion_tokens`/`prompt_version`/`verdict` **可以为 `null`**（拒答与
   预算中止路径 `usage` 是 `{}`、`assessment` 是 `None`）：渲染前判空，别直接 `toLocaleString()`。

---

## 7. 页面细节

**`/login`**：注册/登录两个 tab。注册字段 `username/email/password/display_name`（`display_name`
可空）；成功后直接存 token 跳 `/kbs`。错误按 `error.code` 分支：`CONFLICT`→"用户名或邮箱已被
占用"（后端**不区分**是哪一个，见 `auth.py:105`）；`INVALID_REQUEST`→展示 `details.errors[*].msg`
（后端不回显输入值，别指望拿到原密码）。

**`/kbs`**：卡片列表（名称、`profile.chunk_size`、`generation`；**列表接口不返回文档数**，不要伪造）
+ 新建表单（6 个字段 `name/embedding_model_key/embedding_dim/chunk_size/chunk_overlap/normalize`，
默认值取后端默认：`BAAI/bge-small-zh-v1.5` / 512 / 512 / 64，`normalize` 勾选
—— 出处 `src/pyknora/core/settings.py:46-47`、`:93-94`）。删除要二次确认（会级联删文档），
**且必须先拿到 `row_version` 当 `If-Match`**（§5）。

**`/kbs/[kbId]`**：三段式——

1. 文档区：列表（`DocumentSummary` **没有 filename**，显示 `document_id` 前 8 位，见 §10 R3）、
   `searchable` 徽标、删除按钮；
2. 上传区：`<input type="file">`，先本地校验后缀/50 MiB，再上传（带 `Idempotency-Key`）；
   看 **HTTP 状态码**而不是 body 字段：`202` → 进轮询，`200 && deduped === true` → 直接提示
   "内容重复，已跳过"（**不要轮询**，此时 `job_id` 是 `null`）；
3. 作业区：轮询 `GET /api/v1/jobs/{job_id}`，间隔 1s（`terminal === true` 就停），**五态**徽标
   （`pending/processing/succeeded/failed/cancelled`）+ `attempt` + `error_code`；`failed` 时显示
   `error_code` 并给"重新上传"入口。

可选加分项："检索调试"面板（`POST .../query`，展示 `rrf_score`/`vector_rank`/`keyword_rank`/
`retrieval_strategy`）——**这是唯一能让人看懂混合检索的界面**，建议做。

**`/chat?kb=`**：左对话区 + 右引用侧栏。每条回答下方挂它这一帧的 `citations`（点击滚动到侧栏对应项）；
页码显示 `第 3–4 页`（`page_end_no !== null && page_no !== page_end_no` 时显示区间，`null` 显示 "—"）。
`snippet` 是 500 字截断文本，侧栏要标注"片段"。`invalid_citations > 0` 时标黄"有 N 处引用越界"。
`done.verdict` 可用于显示"证据充分 / 不足 / 冲突"徽标（`INSUFFICIENT`/`CONFLICTING` 时答案通常
也是拒答文案）。

---

## 8. 任务（W1–W4）

| 波次 | 任务 | 产出 | 验证信号 | 人日 |
| --- | --- | --- | --- | --- |
| W1 | T1.1 脚手架：`web/` + Next.js + TS + `rewrites` 代理 | 能跑 `pnpm dev` 并打通 `/api/v1/knowledge-bases` | 浏览器 Network 里 200；后端日志有请求 | 0.3 |
| W1 | T1.2 `lib/types.ts`（§4 的 18 条路由请求/响应类型）+ `lib/api.ts`（统一错误解析 + 头注入） | 两个模块 | 类型 `tsc --noEmit` 通过；错误体按 `error.code` 分支 | 0.4 |
| W2 | T2.1 `/login`（注册/登录/`me` 验活） | 页面 | 注册新用户 → 跳 `/kbs`；刷新不掉登录 | 0.3 |
| W2 | T2.2 `/kbs`（列表 + 新建 + 删除确认 + `If-Match`） | 页面 | 新建后出现在列表；删除后消失（且不发 428） | 0.4 |
| W3 | T3.1 `/kbs/[kbId]` 文档区 + 上传区（202/200 分支 + `Idempotency-Key`） | 页面 | 真上传一份 PDF → 文档出现；重复上传显示"已跳过" | 0.6 |
| W3 | T3.2 作业轮询（五态 + `error_code` + 取消） | 组件 | 坏文件上传 → 显示 `failed`/`PARSE_FAILED` | 0.3 |
| W3 | T3.3 检索调试面板（可选但建议） | 组件 | 输入查询 → 显示 `rrf_score` 与两路 rank + `retrieval_strategy` | 0.3 |
| W4 | T4.1 `lib/sse.ts` + `/chat` 页面（含四个边界） | 页面 | 真模型（或桩）流式出字；`error` 后仍收到 `done` | 0.6 |
| W4 | T4.2 引用侧栏 + 降级横幅 + `invalid_citations` 警示 | 组件 | 引用可点；降级时横幅出现 | 0.4 |
| W4 | T4.3 手工走查清单 + `web/README.md`（启动三步） | 文档 | 换机器按 README 能跑起来 | 0.2 |

**合计约 3.8 人日。**

---

## 9. 验收（手工走查，不做 E2E 框架）

| # | 检查项 | 期望 |
| --- | --- | --- |
| V1 | 注册 → 登录 → 刷新页面 | 仍登录；`/auth/me` 200 |
| V2 | 新建 KB → 上传 PDF → 轮询到 `succeeded` | `searchable=true`；`/chunks` 返回条数 |
| V3 | 上传坏 PDF（截断文件） | 作业 `status="failed"` 且 `error_code="PARSE_FAILED"`，页面有"重新上传" |
| V4 | 重复上传同一文件 | HTTP **200** + `deduped=true` → 提示"内容重复，已跳过"，**不轮询** |
| V5 | 提问命中知识库 | 流式出字；引用侧栏有页码区间；`done.status="COMPLETED"` |
| V6 | 问语料里没有的事 | `done.status="ABSTAINED"`、`done.verdict="INSUFFICIENT"`、拒答文案 + 引用为空（不崩） |
| V7 | 改坏 token 后刷新 | 401 `UNAUTHENTICATED` → 自动跳 `/login` |
| V8 | 用 A 的 token 打开 B 的 KB 链接 | 404 `RESOURCE_NOT_FOUND` → "不存在或无权限"（不泄漏） |
| V9 | 上传超过 50 MiB | 前端拦截并提示；绕过前端则后端 422 `INVALID_REQUEST` |
| V10 | `degraded` 场景（停掉向量路） | 降级横幅出现且带错误码 |
| V11 | 删 KB 时故意不带 `If-Match` | 后端 428 `REVISION_MISSING`（用来验证 §5 的前置取版本逻辑没漏） |
| V12 | 响应式（1280 / 768 / 375 三档） | 不出现横向滚动条；引用侧栏在窄屏折到下方 |

---

## 10. 风险与不做

**风险**

| # | 风险 | 对策 |
| --- | --- | --- |
| R1 | ~~Day3 会改 `done` 字段~~ **已落地**：`status/run_id/prompt_version/verdict` 现就在契约里 | 直接按 §4.5 的类型写死；只对**可空**字段判空（`prompt_tokens`/`verdict`/`prompt_version`） |
| R2 | 后端只有 8010 一个实例、无 CORS | 同源代理是唯一解；`PYKNORA_API_BASE` 走环境变量，不写死端口 |
| R3 | `DocumentSummary` 无 filename → 列表里只能显示 id 前 8 位 | 记为本轮已知缺陷；下轮在 `documents` 表补 `filename` 列（要迁移）后再显示 |
| R4 | SSE 中断（服务重启） | 解析器捕获 `reader.read()` 抛错 → 提示"连接中断"，不把半截答案当成功 |
| R5 | 教学环境无 Node | `web/README.md` 写明 Node 版本与 `pnpm i`；后端演示不依赖前端 |
| R6 | `If-Match` 需要先读 `row_version`，多一次往返 | 列表/详情响应里已有 `row_version`，随页面数据一起缓存，别在删除前再请求一次 |

**不做**

- 不做 SSR 数据获取（F7）。
- 不做 E2E 测试（Playwright 属另一个依赖体系；本轮用 §9 手工走查）。
- 不做 i18n、不做暗色主题切换（`prefers-color-scheme` 一套就够）。
- 不做多文件批量上传（后端一次一个文件；批量=前端循环+队列，价值不大）。
- 不做**问答页**的原文高亮：`Citation` 只有 `snippet`（500 字截断），没有 `char_start/char_end`。
  注意这与旧版说法不同——`char_start/char_end` **已经在** `/chunks` 的 `ActiveChunk` 里
  （`src/pyknora/schemas/document.py:47-85`），所以**文档切片页**可以直接做高亮，只有问答引用
  侧栏需要后端补字段。
- 不做成员/角色管理页（契约在 §4.6，本轮不实现）。

---

## 11. 与 Day3 计划的接口

| Day3 产物 | 前端怎么用 |
| --- | --- |
| `done.status = ABSTAINED` | 聊天页显示"资料不足，已拒答"而不是普通答案样式；配 `done.verdict` 区分"不足/冲突" |
| `done.status = TIMEOUT / BUDGET_EXCEEDED` | 显示"本次回答超出预算已中止"，并给"重试" |
| `done.run_id` | 显示在回答角落（8 位），用户报障时对后端日志 `chat run finished: run_id=...` |
| `done.invalid_citations > 0` | 回答下方标黄"有 N 处引用越界" |
| `QueryResponse.retrieval_strategy`（**仅** `/query`，SSE 无此字段） | 检索调试面板/页脚显示"baseline / candidate"，便于现场演示切换 |
| `done.verdict`（`SUPPORTED/INSUFFICIENT/CONFLICTING`） | 引用侧栏顶部显示"证据充分 / 不足 / 冲突"徽标 |
| `docs/Day03/0x.md` 里的失败样本 | 手工走查时优先复现这些样本，验证前端提示是否可理解 |

---

## 12. 本次核准发现的偏差（旧值 → 真实值 → 出处）

> 核准时间 2026-10-30，对象：本文件旧版 238 行 + 当前工作区 `src/pyknora/`。

| # | 位置 | 旧值（错/不全） | 真实值 | 出处 |
| --- | --- | --- | --- | --- |
| D1 | §4 `/kbs/[kbId]` | `DocumentAccepted.status_code`（"202 vs 200"） | **不存在该字段**。202/200 是 HTTP 状态码；body 只有 `document_id/deduped/job_id/document_version_id/status`，`status` ∈ `pending`/`deduped`/`purging` | `src/pyknora/api/v1/documents.py:82-92`、`:194-204`、`:339`；`src/pyknora/services/ingest/documents.py:154`、`:175` |
| D2 | §2 B9 | `core/settings.py:61` | `max_upload_bytes` 在 `core/settings.py:97`（`50 * 1024 * 1024`） | `src/pyknora/core/settings.py:97` |
| D3 | §2 B3 + §7 | "`JobDetail` 只有 status/attempt/error_code/terminal"；"四态徽标" | 共 8 字段（另有 `job_id/workspace_id/document_id/job_type`）；状态是**五态** `pending/processing/succeeded/failed/cancelled` | `src/pyknora/api/v1/jobs.py:33-44`；`src/pyknora/services/queue/jobs.py:29-35` |
| D4 | §2 B8 | `invalid_citations` = "引用了不存在的 `[资料 N]`" | 还包括"引用了被 `chat_max_context_chunks` 截掉的条目"：`i < 1 or i > context_size` | `src/pyknora/services/rag/answer.py:119-126`；`src/pyknora/api/v1/chat.py:186` |
| D5 | §2 B12 | "`DocumentSummary` 没有 filename，**只有** `document_id`" | 确实无 `filename`，但有 9 个字段（`document_id/kb_id/active_document_version_id/active_build_id/active_index_version/generation/row_version/searchable/created_at`） | `src/pyknora/api/v1/documents.py:119-134` |
| D6 | F4 + §3 + §8 | "31 条路由里前端只用 **12** 条" | `/api/v1` 30 条里前端本轮用到 **18 条**（§4 表 18 行 + `/health`），未用的 12 条 = `workspaces/*` 11 + `admin/reindex` 1 | §4；路由枚举实测（`/api/v1` 合计 30 条） |
| D7 | §2 表后提示 | "Day3 若落地会新增 `done.status/run_id/prompt_version`、`retrieval_strategy`（SSE `retrieval` 事件）" | `done.*` **已落地**；`retrieval_strategy` **只在 `QueryResponse`**，SSE `retrieval` 事件**没有**该字段 | `src/pyknora/schemas/chat.py:39-48`、`:90-126`；`src/pyknora/schemas/query.py:105-113` |
| D8 | §11 | `retrieval.strategy` | 字段名是 `retrieval_strategy`，且只在 `POST .../query` 响应里 | `src/pyknora/schemas/query.py:105`；`src/pyknora/api/v1/query.py:68` |
| D9 | §4 全表 | 路径前缀混用（`POST /auth/register` vs `GET /api/v1/knowledge-bases`） | 统一 `/api/v1/...`（`/health` 除外） | `src/pyknora/api/main.py:291-301` |
| D10 | §4 `/kbs` | 只列列表项字段，漏了响应外壳 | 外壳是 `KnowledgeBasePage{items,next_cursor,limit}`：**游标分页**（不是 offset）；游标失效 → 410 `CURSOR_EXPIRED` | `src/pyknora/schemas/kb.py:45-50`；`src/pyknora/services/kb/cursor.py:78`；`src/pyknora/api/v1/knowledge_bases.py:110-111` |
| D11 | §7 `/kbs` | 卡片显示 `chunk_size`（当顶层字段） | 是 `profile.chunk_size`（`profile` 嵌套） | `src/pyknora/schemas/kb.py:30-42` |
| D12 | §7 `/kbs` | 新建表单字段 `dim` | 真实字段名 `embedding_dim` | `src/pyknora/api/v1/knowledge_bases.py:31-39` |
| D13 | §3/§5/§6 | 前端只提到 `Authorization` | 还有 `X-Workspace-ID`（可选，大写 `ID`）、`If-Match`（改/删 KB 必带）、`Idempotency-Key`（上传） | `src/pyknora/api/deps.py:59-76`；`src/pyknora/api/v1/knowledge_bases.py:157`、`:180`；`src/pyknora/api/v1/documents.py:165` |
| D14 | 旧版全文 | **完全没提** `If-Match` / 428 `REVISION_MISSING` / 409 `REVISION_CONFLICT` / `ETag` | 改/删 KB 必须先读 `row_version` 当 `If-Match`；DELETE 返 202 + `ETag` | `src/pyknora/api/v1/knowledge_bases.py:59-72`、`:189`；`src/pyknora/services/kb/service.py:62`；`src/pyknora/core/errors.py:35-53` |
| D15 | §4 `/chat` | 漏 `done.verdict`、`citations[*].active_document_version_id`；`snippet` 当成全文 | `snippet` = `chunk.content[:500]`（截断）；`verdict`/`active_document_version_id` 都是现有字段 | `src/pyknora/api/v1/chat.py:157-169`；`src/pyknora/schemas/chat.py:18-36`、`:90-126` |
| D16 | §4 `/kbs/[kbId]` chunks | "chunks 的 `page_no/page_end_no/content`"（像是只有 3 个字段） | `items` 是 `ActiveChunk` 的 **20 字段**序列化（含 `char_start/char_end`、`heading_path`、`locator_ref`） | `src/pyknora/api/v1/documents.py:110-116`、`:314`；`src/pyknora/schemas/document.py:47-85` |
| D17 | §2 B1 | 证据写"`create_app().openapi()` 实测"，口径含糊 | 数值对：`/api/v1` 30 条 + `/health` = 31；另有 4 条框架路由，`app.routes` 共 35 个 route 对象（`include_router` 现为惰性 `_IncludedRouter`，直接遍历 `app.routes` 只能看到 5 条，必须展开 `original_router.routes`） | `src/pyknora/api/main.py:188-303`；本次实测枚举 |
| D18 | §2 B11 | "422/404/405 已统一为统一错误体" | 是**全部**错误统一（`AppError`、请求校验、框架 `HTTPException`、未捕获异常），17 个错误码映射到 401–503；405 保留 HTTP 405 但 `code=INVALID_REQUEST` | `src/pyknora/api/main.py:203-269`；`src/pyknora/core/errors.py:12-53` |
| D19 | §10 "不做" | "文档预览/高亮需要 `char_start/char_end` 进契约，属后端改动" | `char_start/char_end` **已在** `ActiveChunk`（`/chunks` 响应）里；只有**问答引用**缺这两个字段 | `src/pyknora/schemas/document.py:47-85`；`src/pyknora/schemas/chat.py:18-36` |
| D20 | §4 `/jobs` | 未说明取消的幂等语义 | 已终态/`processing` 时**原样返回当前 `JobDetail`，不报错**；只有 `pending` 会变 `cancelled` | `src/pyknora/api/v1/jobs.py:88-141` |
| D21 | §6 边界 | 只列 3 个边界，未提 `done.*` 可空 | `prompt_tokens/completion_tokens/prompt_version/verdict` 在拒答与预算中止路径可以是 `null` | `src/pyknora/api/v1/chat.py:193-200`、`:220-227`；`src/pyknora/services/rag/answer.py:259-264`、`:285-288` |
| D22 | §2 B13 | `node v26.5.1` / `pnpm 12.6.0`（原为未注明来源的断言） | 本次实测仍为 `v26.5.1` / `12.6.0`（`npm 11.17.0`） | 本工作区 `node --version`、`pnpm --version` |
| D23 | §4 `/query` | 未写 `query` 长度上限与默认 `top_k` | `query` ≤ 1000 字符、`top_k` 默认 10（chat 默认 6） | `src/pyknora/schemas/query.py:28-51`；`src/pyknora/schemas/chat.py:11-15` |
