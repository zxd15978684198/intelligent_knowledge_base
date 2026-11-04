/**
 * 六态作业徽标：颜色 + 文字双通道，颜色只是辅助，文字才是唯一可靠信号
 * （计划 §6「成功、警告、失败使用独立语义色并配文字或图标」、D10 可访问性）。
 *
 * `sr-only` 里补的是**语义说明**，不是重复一遍文字：读屏用户听到的是
 * 「处理中」+「Worker 正在解析、切片并建立索引，文档当前不可检索」，
 * 这比再念一遍「处理中」有用。
 */

import { cn } from "@/lib/utils";

import { isJobStatus, jobStatusDescription, jobStatusLabel } from "./types";

type Tone = "info" | "success" | "danger" | "muted" | "warning";

const TONE_CLASS: Record<Tone, string> = {
  info: "bg-info-weak text-info",
  success: "bg-success-weak text-success",
  danger: "bg-danger-weak text-danger",
  muted: "bg-surface-muted text-fg-muted",
  warning: "bg-warning-weak text-warning",
};

const DOT_CLASS: Record<Tone, string> = {
  info: "bg-info",
  success: "bg-success",
  danger: "bg-danger",
  muted: "bg-fg-subtle",
  warning: "bg-warning",
};

/** 状态 → 色调。未知态走 warning，绝不默认成"成功色"。 */
function toneOf(status: string): Tone {
  switch (status) {
    case "pending":
      return "info";
    case "processing":
      return "info";
    case "succeeded":
      return "success";
    case "failed":
      return "danger";
    case "cancelled":
      return "muted";
    case "superseded":
      return "muted";
    default:
      return "warning";
  }
}

export interface JobBadgeProps {
  /** 后端 `JobDetail.status` 是 `str`；未知取值也能安全渲染。 */
  status: string;
  className?: string;
}

export function JobBadge({ status, className }: JobBadgeProps) {
  const tone = toneOf(status);
  const label = jobStatusLabel(status);
  const description = jobStatusDescription(status);
  const pulsing = status === "processing";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-xs font-medium",
        TONE_CLASS[tone],
        className,
      )}
      data-status={isJobStatus(status) ? status : "unknown"}
    >
      <span
        aria-hidden="true"
        className={cn("h-1.5 w-1.5 shrink-0 rounded-full", DOT_CLASS[tone], pulsing && "animate-pulse")}
      />
      <span>{label}</span>
      <span className="sr-only">（{description}）</span>
    </span>
  );
}
