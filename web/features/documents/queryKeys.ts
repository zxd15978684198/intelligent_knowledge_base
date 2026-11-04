/**
 * 文档域查询键。
 *
 * 列表 / 详情 / 切片全部委托给 `@/lib/api/hooks` 的 `qk`：那里规定"每个 key 的第二段
 * 必须是 workspaceId"，业务代码自己拼数组迟早漏掉 ws，切工作区就会读到别人的文档。
 *
 * 两个例外必须本地定义：
 * - `lineage`：`qk` 还没有这个工厂（N2 只给了 kbs/documents/document/chunks/jobs/members）。
 *   形状沿用 `qk` 的约定 `["lineage", ws, doc]`，等 `qk` 补上后改成 re-export 即可。
 * - `listScope` / `chunkScope`：前缀失效用的**前缀**，不是查询键。
 */

import { qk, type PageParams } from "@/lib/api/hooks";

export const documentKeys = {
  /** 文档列表（limit/offset 进键，total 是全量）。 */
  list: (workspaceId: string, kbId: string, page: PageParams): readonly unknown[] =>
    qk.documents(workspaceId, kbId, page),

  /** 前缀：失效一个 KB 下的所有文档页。 */
  listScope: (workspaceId: string, kbId: string): readonly unknown[] => ["documents", workspaceId, kbId],

  detail: (workspaceId: string, documentId: string): readonly unknown[] =>
    qk.document(workspaceId, documentId),

  /** 切片（infinite query：offset 由 pageParam 管，但 `qk.chunks` 仍要求完整 page）。 */
  chunkList: (workspaceId: string, documentId: string, page: PageParams): readonly unknown[] =>
    qk.chunks(workspaceId, documentId, page),

  /** 前缀：失效一个文档的所有切片页。 */
  chunkScope: (workspaceId: string, documentId: string): readonly unknown[] => [
    "chunks",
    workspaceId,
    documentId,
  ],

  lineage: (workspaceId: string, documentId: string): readonly unknown[] => [
    "lineage",
    workspaceId,
    documentId,
  ],
} as const;
