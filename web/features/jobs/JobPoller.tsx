"use client";

/**
 * 作业状态轮询器。
 *
 * 计划 §5 的三条硬约束在这里落地：
 * - **1.5s 间隔、只在前台轮询**：`refetchInterval` 返回 `POLL_INTERVAL_MS` 或 `false`，
 *   `refetchIntervalInBackground: false`（标签页不可见时不打接口）。
 * - **终态立刻停**：停止条件是 `shouldPollJob()`，即
 *   `terminal === true || status === "superseded"`。后端 `terminal` 对 superseded 恒为
 *   false（`services/queue/jobs.py:35` 漏了该状态），只信 `terminal` 会一直轮询下去。
 * - **终态回调一次**：用 ref 按 job_id 去重，避免严格模式或缓存重渲染重复触发。
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { Button } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { cn, shortId } from "@/lib/utils";

import { JobBadge } from "./JobBadge";
import { JobCancelButton } from "./JobCancelButton";
import { getJob } from "./api";
import { describeApiError, formatErrorLabel } from "./errors";
import { jobKeys } from "./queryKeys";
import { POLL_INTERVAL_MS, isJobTerminal, shouldPollJob, type JobDetail } from "./types";

export interface JobPollerProps {
  jobId: string;
  /** 缓存键第二段（`qk.jobs` 的硬要求）。 */
  workspaceId: string;
  /** 进入终态时回调一次（成功/失败/取消/被取代都会触发，按需自行分支）。 */
  onTerminal?: (job: JobDetail) => void;
  /** 是否显示取消按钮，默认显示。 */
  showCancel?: boolean;
  className?: string;
}

export function JobPoller({
  jobId,
  workspaceId,
  onTerminal,
  showCancel = true,
  className,
}: JobPollerProps) {
  const notifiedJobId = useRef<string | null>(null);

  const query = useQuery({
    queryKey: jobKeys.detail(workspaceId, jobId),
    queryFn: () => getJob(jobId, { workspaceId }),
    refetchInterval: (current) => {
      const job = current.state.data;
      // 还没拿到第一份数据时不排轮询：交给 useQuery 自己取一次。
      if (job === undefined) return false;
      return shouldPollJob(job) ? POLL_INTERVAL_MS : false;
    },
    refetchIntervalInBackground: false,
    retry: (failureCount, error) => {
      // 404 = 不存在或无权查看，重试没有意义。
      if (error instanceof ApiError && error.status === 404) return false;
      return failureCount < 2;
    },
  });

  const job = query.data;

  useEffect(() => {
    if (job === undefined) return;
    if (!isJobTerminal(job)) return;
    if (notifiedJobId.current === job.job_id) return;
    notifiedJobId.current = job.job_id;
    onTerminal?.(job);
  }, [job, onTerminal]);

  if (query.isError) {
    const description = describeApiError(query.error);
    return (
      <div
        role="alert"
        className={cn("rounded-md border border-danger bg-danger-weak px-3 py-2 text-sm text-danger", className)}
      >
        <p>作业状态读取失败：{formatErrorLabel(description)}</p>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="mt-2"
          onClick={() => void query.refetch()}
        >
          重试
        </Button>
      </div>
    );
  }

  if (job === undefined) {
    return (
      <p className={cn("text-sm text-fg-muted", className)} aria-busy="true">
        正在读取作业状态…
      </p>
    );
  }

  const polling = shouldPollJob(job);
  const errorCode = job.error_code ?? null;

  return (
    <div
      className={cn("rounded-md border border-border bg-surface px-3 py-2", className)}
      aria-live="polite"
      aria-busy={polling}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <JobBadge status={job.status} />
        <span className="tabular text-xs text-fg-muted">
          作业 {shortId(job.job_id)} · 第 {job.attempt} 次尝试
        </span>
        <span className="text-xs text-fg-subtle">
          {polling ? `每 ${POLL_INTERVAL_MS / 1000} 秒自动刷新` : "已停止轮询（终态）"}
        </span>
      </div>

      {errorCode !== null ? (
        <p className="mt-1 text-xs text-danger">
          错误码 <code className="font-mono">{errorCode}</code>
          {job.status === "failed" ? "：作业失败，文档没有发布。" : "：作业以该错误码结束。"}
        </p>
      ) : null}

      {showCancel ? <JobCancelButton job={job} workspaceId={workspaceId} className="mt-2" /> : null}
    </div>
  );
}
