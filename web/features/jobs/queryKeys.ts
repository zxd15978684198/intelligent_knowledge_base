/**
 * 作业查询键。
 *
 * 全部委托给 `@/lib/api/hooks` 的 `qk`：那里规定"每个 key 的第二段必须是 workspaceId"，
 * 业务代码自己拼数组迟早会漏掉 ws，切工作区后就会读到别人的作业。
 *
 * `qk.jobs(ws, jobId)` = `["jobs", ws, jobId]`，于是 `scope(ws)` 前缀失效能一次
 * 刷新该工作区下所有作业。
 */

import { qk } from "@/lib/api/hooks";

export const jobKeys = {
  detail: (workspaceId: string, jobId: string): readonly unknown[] => qk.jobs(workspaceId, jobId),
  scope: (workspaceId: string): readonly unknown[] => ["jobs", workspaceId],
} as const;
