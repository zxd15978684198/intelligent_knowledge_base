"use client";

/**
 * 取消作业。
 *
 * 三个必须守住的点（都由后端语义决定，不是 UI 口味）：
 *
 * 1. **只在 pending / processing 显示**。终态没有可取消的东西；后端对终态取消是空操作。
 * 2. **不承诺立即停止**。`cancel_job` 只 UPDATE `status='pending'` 的行，处理中的作业
 *    会跑完当前一步再自行退出（jobs.py:102-108）。所以文案写"不保证立即停止"。
 * 3. **以响应体为准更新缓存**。后端用 `UPDATE ... RETURNING`，返回的就是它写进去的值；
 *    前端不要自己把 status 改成 cancelled —— 取消 processing 作业时那会显示假的终态。
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

import { cancelJob } from "./api";
import { describeApiError, formatErrorLabel } from "./errors";
import { jobKeys } from "./queryKeys";
import { isJobActive, type JobDetail } from "./types";

export interface JobCancelButtonProps {
  job: Pick<JobDetail, "job_id" | "status">;
  /** 缓存键第二段（`qk.jobs` 的硬要求）。 */
  workspaceId: string;
  className?: string;
}

export function JobCancelButton({ job, workspaceId, className }: JobCancelButtonProps) {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => cancelJob(job.job_id, { workspaceId }),
    onSuccess: (updated) => {
      // 后端回的是权威值，直接落缓存；再失效整个作业前缀兜底。
      queryClient.setQueryData(jobKeys.detail(workspaceId, job.job_id), updated);
      void queryClient.invalidateQueries({ queryKey: jobKeys.scope(workspaceId) });
    },
  });

  // 终态不渲染（未知状态同样不可取消：不能凭猜测发写请求）。放在 hook 之后，避免条件调用。
  if (!isJobActive(job.status)) return null;

  const wasPending = job.status === "pending";
  const settled = mutation.data;
  const stillRunning = settled !== undefined && isJobActive(settled.status);
  const error = mutation.isError ? describeApiError(mutation.error) : null;

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        className="w-fit"
        loading={mutation.isPending}
        onClick={() => mutation.mutate()}
      >
        {mutation.isPending ? "取消中…" : "取消作业"}
      </Button>

      <p className="text-xs text-fg-muted">
        取消不保证立即停止处理中的任务：
        {wasPending ? "排队中的作业会被立即置为已取消。" : "处理中的作业会跑完当前一步再退出。"}
      </p>

      {stillRunning ? (
        <p role="status" className="text-xs text-warning">
          后端没有改动这个作业：它仍在{settled.status === "pending" ? "排队" : "处理中"}。
          取消只对排队中的作业生效。
        </p>
      ) : null}

      {error !== null ? (
        <p role="alert" className="text-xs text-danger">
          取消失败：{formatErrorLabel(error)}
          {error.requestId !== null ? (
            <span className="text-fg-subtle">（请求 {error.requestId}）</span>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}
