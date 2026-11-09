"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * 检索降级提示：`retrieval.degraded` 非空才渲染。
 *
 * 为什么值得单独一个组件：`degraded` 的键是检索分支名（如 `vector`、`keyword`），
 * 值是错误码。它非空意味着**本次引用不是全量检索的结果**——用户看到"只找到 1 条"
 * 时，必须能分辨"资料里就这 1 条"和"另一路检索挂了只剩 1 条"。把这两件事混为一谈，
 * 会让用户得出"知识库里没有相关内容"的错误结论。
 */
export interface DegradedNoteProps {
  readonly degraded: Readonly<Record<string, string>>;
  readonly className?: string;
}

export function DegradedNote({ degraded, className }: DegradedNoteProps): React.ReactElement | null {
  const entries = Object.entries(degraded);
  if (entries.length === 0) {
    return null;
  }

  return (
    <div
      role="status"
      className={cn(
        "space-y-1 rounded-lg border border-warning bg-warning-weak px-3 py-2 text-[12px] leading-relaxed",
        className,
      )}
    >
      <p className="font-semibold text-warning">本次检索有通道降级，引用可能不完整</p>
      <ul className="space-y-0.5 text-fg-muted">
        {entries.map(([channel, code]) => (
          <li key={channel}>
            <span className="font-mono">{channel}</span>
            <span aria-hidden="true">：</span>
            <span className="font-mono text-[11px]">{code}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
