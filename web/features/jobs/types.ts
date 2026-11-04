/**
 * 作业状态的类型与判停逻辑。
 *
 * 类型本体来自 `@/lib/contracts`（openapi 生成 + N2 手工补丁），这里**只补三件事**：
 *
 * 1. 六态的中文标签与语义说明——颜色只是辅助，文字才是可靠通道；
 * 2. `isJobActive` —— 轮询与"取消"按钮共用的"还在跑"判据；
 * 3. `shouldPollJob` —— 把后端"`terminal` 漏了 superseded"这个缺陷封在一处，
 *    调用方不必各自记得写 `|| status === "superseded"`。
 *
 * 为什么不再抄一份 `JobDetail`：契约一改本地副本就静默漂移；`@/lib/contracts`
 * 的字段是 openapi 生成 + 逐字段核对过的。
 *
 * 后端事实（已逐行核对）：
 * - 前五态定义在 `src/pyknora/services/queue/jobs.py:29-33`；
 * - 第六态 `superseded` 由 `src/pyknora/workers/ingest_worker.py:365` 直接写库；
 * - 已知缺陷：`queue.TERMINAL_STATUSES`（jobs.py:35）只有 succeeded/failed/cancelled，
 *   于是 `GET /api/v1/jobs/{id}` 对 superseded 回 `terminal: false`。
 */

import { isJobTerminal, type JobDetail, type JobStatus } from "@/lib/contracts";

export { JOB_STATUSES, isJobTerminal } from "@/lib/contracts";
export type { JobDetail, JobStatus } from "@/lib/contracts";

/** 还在推进的两态：只有它们值得轮询，也只有它们允许"取消"。 */
export const ACTIVE_JOB_STATUSES = ["pending", "processing"] as const;

export type ActiveJobStatus = (typeof ACTIVE_JOB_STATUSES)[number];

/** 轮询间隔 1.5s（计划 §5 的审定值）。 */
export const POLL_INTERVAL_MS = 1500;

export function isJobActive(status: string): status is ActiveJobStatus {
  return status === "pending" || status === "processing";
}

const JOB_STATUS_SET: ReadonlySet<string> = new Set([
  "pending",
  "processing",
  "succeeded",
  "failed",
  "cancelled",
  "superseded",
]);

/**
 * 运行时收窄。
 *
 * `JobDetail.status` 在类型层已是 `JobStatus`，但后端声明是裸 `str`
 * （openapi 只有 `string`），真实响应可能给出六态之外的取值。渲染未知态时
 * 必须能安全降级，所以保留这个守卫。
 */
export function isJobStatus(value: string): value is JobStatus {
  return JOB_STATUS_SET.has(value);
}

/**
 * 是否继续轮询。
 *
 * 判据 = 还在跑 **且** 未被判终态：
 * - `isJobActive` 直接拦掉四个终态（succeeded/failed/cancelled/superseded）；
 * - `isJobTerminal` 兜住"状态看着还在跑、后端已判终态"的极端情况（将来加新状态时）。
 *
 * 这里**不能只读 `job.terminal`**：superseded 的 `terminal` 恒为 false，
 * 只读它会把一个永远不会再变的作业每 1.5s 轮询到用户关页面。
 */
export function shouldPollJob(job: Pick<JobDetail, "status" | "terminal">): boolean {
  return isJobActive(job.status) && !isJobTerminal(job);
}

const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  pending: "排队中",
  processing: "处理中",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
  superseded: "已被取代",
};

/** 六态中文标签。未知态如实带上原值，不硬翻成"未知"掩盖问题。 */
export function jobStatusLabel(status: string): string {
  return isJobStatus(status) ? JOB_STATUS_LABEL[status] : `未知状态（${status === "" ? "空" : status}）`;
}

const JOB_STATUS_DESCRIPTION: Record<JobStatus, string> = {
  pending: "作业已入队，还没有 Worker 认领，可以取消",
  processing: "Worker 正在解析、切片并建立索引，文档此时不可检索",
  succeeded: "解析与索引已完成，文档已可检索",
  failed: "作业失败，文档没有发布，可按错误码重试上传",
  cancelled: "作业已取消；处理中的任务不保证立即停止，可能跑完当前一步",
  superseded: "作业被更新的构建取代，结果不再生效，也不会再变化",
};

/** 给读屏用户的语义说明：解释这个状态**意味着什么**，而不是重复标签。 */
export function jobStatusDescription(status: string): string {
  return isJobStatus(status)
    ? JOB_STATUS_DESCRIPTION[status]
    : "后端返回了前端不认识的状态，界面按未知处理且不继续轮询";
}
