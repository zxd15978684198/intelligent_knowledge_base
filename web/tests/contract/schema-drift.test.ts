import { describe, expect, it } from "vitest";

import { healthResponseSchema, jobStatusSchema } from "@/lib/contracts";
import type {
  ChunkPage,
  DocumentAccepted,
  DocumentDetail,
  DocumentLineage,
  DocumentPage,
  DocumentSummary,
  EmbeddingProfile,
  JobDetail,
  KnowledgeBase,
  KnowledgeBaseCreate,
  KnowledgeBasePage,
  LineageEntry,
  MemberPage,
  MeResponse,
  MembershipDetail,
  QueryResponse,
  RetrievedChunk,
  TokenResponse,
} from "@/lib/contracts";
import type { components } from "@/lib/contracts/openapi";

import { sampleHealth, sampleJobProcessing } from "../fixtures/entities";

/**
 * 契约漂移门禁。
 *
 * 手写类型与 `contracts/openapi.json`（N1 生成，再由 `pnpm gen:contracts` 转成
 * `lib/contracts/openapi.d.ts`）之间必须有东西盯着，否则后端删个字段、改个名、
 * 把某个字段从必填改成可选，前端要到运行时才炸。
 *
 * 断言方向是**单向的**：`后端形状 extends 前端类型`。
 * - 后端删字段/改名/收紧（把可选变必填）→ 不满足 extends → `tsc` 报错；
 * - 后端加字段 → 仍然满足（前端多收一个不认识的字段无害，Zod 那边也是 strip）。
 * 这个方向刻意不写成双向：双向会把"后端加字段"变成前端编译失败，
 * 每次后端迭代都要同步改前端，成本远大于收益。
 *
 * 三处刻意偏差单独拆开断言（下面有注释），它们不是漏网，是有意收窄/放宽。
 */

type Schemas = components["schemas"];

/** 编译期断言：参数必须是 `true`。传 `false` 时 `false` 不满足 `extends true` → tsc 报错。 */
type Assert<T extends true> = T;

/** 后端形状能否被前端类型接受（元组形式，避免联合类型被分布式展开）。 */
type Accepts<Backend, Frontend> = [Backend] extends [Frontend] ? true : false;

// ── 逐实体的漂移断言 ───────────────────────────────────────────

export type DriftKnowledgeBase = Assert<Accepts<Schemas["KnowledgeBase"], KnowledgeBase>>;
export type DriftEmbeddingProfile = Assert<Accepts<Schemas["EmbeddingProfile"], EmbeddingProfile>>;
export type DriftKnowledgeBasePage = Assert<Accepts<Schemas["KnowledgeBasePage"], KnowledgeBasePage>>;
export type DriftKnowledgeBaseCreate = Assert<Accepts<Schemas["KnowledgeBaseCreate"], KnowledgeBaseCreate>>;

export type DriftDocumentSummary = Assert<Accepts<Schemas["DocumentSummary"], DocumentSummary>>;
export type DriftDocumentDetail = Assert<Accepts<Schemas["DocumentDetail"], DocumentDetail>>;
export type DriftDocumentPage = Assert<Accepts<Schemas["DocumentPage"], DocumentPage>>;
export type DriftDocumentAccepted = Assert<Accepts<Schemas["DocumentAccepted"], DocumentAccepted>>;

export type DriftMemberPage = Assert<Accepts<Schemas["MemberPage"], MemberPage>>;
export type DriftMembershipDetail = Assert<Accepts<Schemas["MembershipDetail"], MembershipDetail>>;

export type DriftQueryResponse = Assert<Accepts<Schemas["QueryResponse"], QueryResponse>>;
export type DriftRetrievedChunk = Assert<Accepts<Schemas["RetrievedChunk"], RetrievedChunk>>;

export type DriftMeResponse = Assert<Accepts<Schemas["MeResponse"], MeResponse>>;
export type DriftTokenResponse = Assert<Accepts<Schemas["TokenResponse"], TokenResponse>>;

export type DriftDocumentLineage = Assert<Accepts<Schemas["DocumentLineage"], DocumentLineage>>;
export type DriftLineageEntry = Assert<Accepts<Schemas["LineageEntry"], LineageEntry>>;

/**
 * 偏差 1：`JobDetail.status`。
 * 后端把它声明成裸 `str`（`api/v1/jobs.py`），openapi 里就是 `string`；
 * 前端收窄成六态 `JobStatus`，否则 UI 无法穷举分支。收窄的漂移风险由
 * `jobStatusSchema` 在运行时兜（`parse` 失败即说明后端出了新状态）。
 * 所以这里把 `status` 摘掉再比，其余字段照常受门禁保护。
 */
export type DriftJobDetail = Assert<Accepts<Omit<Schemas["JobDetail"], "status">, Omit<JobDetail, "status">>>;

/** 把上一条的前提钉死：后端一旦真的给了枚举，这条断言会失败，提醒我们回来改写注释。 */
export type DriftJobStatusIsBareString = Assert<Accepts<Schemas["JobDetail"]["status"], string>>;

/**
 * 偏差 2：`ChunkPage.items`。
 * 后端这个端点的 response_model 是 `list[dict[str, Any]]`（没用 `ActiveChunk`），
 * openapi 里只能是"任意对象数组"；前端手写成 `ChunkItem[]` 才有字段可读。
 * 因此 items 无法做类型级比对，只断言分页元信息，另加一条"后端确实是任意对象"。
 */
export type DriftChunkPageMeta = Assert<Accepts<Omit<Schemas["ChunkPage"], "items">, Omit<ChunkPage, "items">>>;
export type DriftChunkItemsAreOpaque = Assert<
  Accepts<Schemas["ChunkPage"]["items"], { [key: string]: unknown }[]>
>;

/**
 * 偏差 3：`QueryResponse.retrieval_strategy` / `DocumentLineage.contributors` 等
 * 在后端是裸 `str` / `UUID[]`，前端保留 `string` / `string[]`，无需摘字段——
 * 但 `knowledge_bases` 的 `role` 同理（`MembershipDetail.role`），所以这条单独钉住，
 * 免得有人后来把 `role` 改成 union 导致管理端新建角色时静默失配。
 */
export type DriftMembershipRoleIsBareString = Assert<Accepts<Schemas["MembershipDetail"]["role"], string>>;

/**
 * 三个分页的 `total` 语义不同，类型层看不出来，这里用注释与运行时用例钉住：
 * - `KnowledgeBasePage`：游标分页，没有 total；
 * - `DocumentPage.total`：**全量**；
 * - `ChunkPage.total`：**页内条数**。
 */
type _PaginationFacts = Assert<
  Accepts<Schemas["KnowledgeBasePage"], { items: KnowledgeBase[]; limit: number; next_cursor?: string | null }>
>;

describe("契约漂移：类型层（见本文件顶部的 Assert 断言）", () => {
  it("上面的断言全部在 tsc 阶段生效，这里只留一个运行时锚点", () => {
    // 类型层断言没有运行时副作用；留一条用例是为了让 vitest 报告里能看到这个文件，
    // 免得有人以为"没跑"。
    expect(true).toBe(true);
  });

  it("/health 夹具必须能通过 healthResponseSchema", () => {
    const parsed = healthResponseSchema.parse(sampleHealth);
    expect(parsed.status).toBe("ok");
    expect(parsed.embedder).toBe("test/hashing");
  });

  it("作业夹具的 status 必须落在六态内", () => {
    expect(jobStatusSchema.parse(sampleJobProcessing.status)).toBe("processing");
  });
});
