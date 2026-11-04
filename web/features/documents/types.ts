/**
 * 文档域类型。
 *
 * **全部来自 `@/lib/contracts`**（openapi 生成 + N2 手工补丁），这里只做转出，不镜像：
 * 本地副本会在契约变更时静默漂移，而契约文件里已经把三个坑写在注释里了。
 *
 * 三个必须记住的口径（写错就是 bug）：
 * 1. `DocumentPage.total` 是**全量条数** → 可以算页数、做 offset 分页器；
 * 2. `ChunkPage.total` 是**页内条数**（后端 `total=len(chunks)`）→ 只能"加载更多"；
 * 3. `DocumentDetail` **没有 `created_at`**（详情页不能显示创建时间，别从列表猜）。
 *
 * 另外 `filename` / `job_id` / `error_code` 与详情的三个指针在契约里都是**可选字段**，
 * 判断一律写 `x ?? null`，不要写 `x !== null`（`undefined !== null` 为 true）。
 */

export type {
  ChunkItem,
  ChunkPage,
  DocumentAccepted,
  DocumentDetail,
  DocumentLineage,
  DocumentPage,
  DocumentSummary,
  LineageEntry,
} from "@/lib/contracts";
