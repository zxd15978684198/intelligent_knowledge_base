import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn 约定：合并条件类名，后者覆盖前者的冲突工具类。 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** 把 UUID 缩成可读短码（表格里显示，完整值放 title / aria-label）。 */
export function shortId(id: string, keep = 8): string {
  return id.slice(0, keep);
}

/**
 * 后端时间戳是 UTC ISO 字符串（`created_at` 等），统一按本地时区展示。
 * 解析失败返回原串——界面上宁可显示怪值，也不要抛异常把整页打挂。
 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

/** 字节数人类可读。 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${units[unit] ?? "KB"}`;
}

/** 毫秒 → 人类可读延迟（P95、TTFT 展示用）。 */
export function formatMillis(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

/** 百分比展示：入参是 0–1 的小数，后端 summary.json 就是这个口径。 */
export function formatRatio(ratio: number | null | undefined, digits = 1): string {
  if (ratio === null || ratio === undefined) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}
