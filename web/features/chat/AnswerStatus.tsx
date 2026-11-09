"use client";

import * as React from "react";

import { cn } from "@/lib/utils";
import { StatusBadge } from "@/components/ui/status-badge";
import type { BadgeTone } from "@/components/ui/badge";

import type { AnswerVerdict, ChatDone, ChatStreamError, RunStatus } from "./events";
import type { ChatPhase } from "./machine";
import { STREAM_INCOMPLETE_CODE } from "./stream";

/**
 * 收尾状态面板：**运行状态**与**校验结论**是两个维度，必须分开显示。
 *
 * 把两维压成一维是这类界面最常见的错：`ABSTAINED`（拒答）配上 `INSUFFICIENT`
 * （证据不足）说的是"我查到了东西但不够，所以我选择不说"，而 `TIMEOUT`、
 * `BUDGET_EXCEEDED` 说的是"我还没说完就被掐断了"——前者用户该补资料，后者用户
 * 该放宽预算或重试，处置完全不同。`FAILED` 则是链路坏了。维表：
 *
 * | 运行状态 | 含义 | 用户的下一步 |
 * | --- | --- | --- |
 * | COMPLETED | 正常答完 | 读答案 |
 * | ABSTAINED / INSUFFICIENT | 证据不足拒答 | 补充或换更具体的资料 |
 * | TIMEOUT / BUDGET_EXCEEDED | 预算中止 | 缩小问题范围或重试 |
 * | FAILED | 失败 | 看错误码，必要时反馈 |
 * | CONFLICTING（verdict） | 证据冲突 | 人工裁决，别信单条引用 |
 *
 * `run_id` 与 `prompt_version` 必定展示：没有它们，用户反馈问题时我们无从在
 * 日志/评测报告里定位是哪一次回答。
 */

interface StatusMeta {
  readonly label: string;
  readonly tone: BadgeTone;
  readonly note: string;
}

const STATUS_META: Record<RunStatus, StatusMeta> = {
  COMPLETED: { label: "已完成", tone: "success", note: "本次回答已正常生成完毕。" },
  ABSTAINED: {
    label: "证据不足·已拒答",
    tone: "warning",
    note: "检索到的资料不足以支撑结论，系统选择不猜测。",
  },
  TIMEOUT: {
    label: "预算中止·超时",
    tone: "warning",
    note: "回答在超时预算内没有生成完毕，已输出部分可能不完整。",
  },
  BUDGET_EXCEEDED: {
    label: "预算中止·超限",
    tone: "warning",
    note: "回答触发了 token 预算上限，已输出部分可能不完整。",
  },
  FAILED: { label: "失败", tone: "danger", note: "本次回答失败了，详见下方错误信息。" },
};

const VERDICT_META: Record<AnswerVerdict, StatusMeta> = {
  SUPPORTED: { label: "证据支持", tone: "success", note: "引用的资料能支撑文中的结论。" },
  INSUFFICIENT: {
    label: "证据不足",
    tone: "warning",
    note: "引用的资料不足以支撑结论，结论可能超出资料范围。",
  },
  CONFLICTING: {
    label: "证据冲突",
    tone: "danger",
    note: "多条引用互相矛盾，结论需要人工裁决。",
  },
};

export interface AnswerStatusProps {
  readonly phase: ChatPhase;
  readonly done: ChatDone | null;
  readonly error: ChatStreamError | null;
  /** 断流：没收到 `done` 就关了。半截回答要在状态里显式承认。 */
  readonly incomplete: boolean;
  /** 前端反算出的越界引用序号（1 起）。后端只给条数，逐条定位靠这里。 */
  readonly invalidIndices?: readonly number[];
  readonly className?: string;
}

export function AnswerStatus({
  phase,
  done,
  error,
  incomplete,
  invalidIndices = [],
  className,
}: AnswerStatusProps): React.ReactElement | null {
  if (done === null && error === null && phase !== "aborted") {
    return null;
  }

  const status = done === null ? null : STATUS_META[done.status];
  const verdict = done?.verdict === null || done?.verdict === undefined ? null : VERDICT_META[done.verdict];
  const invalidCount = done?.invalid_citations ?? 0;

  return (
    <div className={cn("space-y-2", className)} data-phase={phase}>
      {error === null ? null : <ErrorBlock error={error} />}

      {status === null ? null : (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <StatusBadge label={status.label} tone={status.tone} srLabel={`运行状态：${status.label}`} />
          {verdict === null ? null : (
            <StatusBadge label={verdict.label} tone={verdict.tone} srLabel={`校验结论：${verdict.label}`} />
          )}
          {incomplete ? (
            <StatusBadge label="回答不完整" tone="warning" srLabel="本次回答没有收到结束帧，内容可能不完整" />
          ) : null}
        </div>
      )}

      {status === null ? null : <p className="text-[12px] leading-relaxed text-fg-muted">{status.note}</p>}

      {done === null && phase === "aborted" ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <StatusBadge label="已停止" tone="warning" srLabel="用户中止了本次回答" />
          <span className="text-[12px] leading-relaxed text-fg-muted">已收到的内容保留，但这不是完整回答，也不会续传；请重新提问。</span>
        </div>
      ) : null}

      {done === null ? null : (
        <dl className="grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-1 text-[12px] leading-relaxed">
          <dt className="text-fg-subtle">运行 ID</dt>
          <dd className="truncate font-mono tabular text-fg" title={done.run_id}>
            {done.run_id}
          </dd>
          <dt className="text-fg-subtle">提示词版本</dt>
          <dd className="truncate font-mono text-fg">{done.prompt_version ?? "（未走到模型）"}</dd>
          {done.prompt_tokens === null && done.completion_tokens === null ? null : (
            <>
              <dt className="text-fg-subtle">令牌数</dt>
              <dd className="tabular-nums tabular text-fg">
                输入 {formatCount(done.prompt_tokens)} / 输出 {formatCount(done.completion_tokens)}
              </dd>
            </>
          )}
          {invalidCount > 0 ? (
            <>
              <dt className="text-fg-subtle">引用校验</dt>
              <dd className="tabular font-medium text-danger">
                {invalidCount} 条引用未被系统校验通过
                {invalidIndices.length > 0 ? `（序号 ${invalidIndices.join("、")}）` : ""}
              </dd>
            </>
          ) : null}
        </dl>
      )}
    </div>
  );
}

function ErrorBlock({ error }: { readonly error: ChatStreamError }): React.ReactElement {
  const isIncomplete = error.code === STREAM_INCOMPLETE_CODE;
  return (
    <div
      role="alert"
      className="space-y-1 rounded-lg border border-danger bg-danger-weak px-3 py-2 text-[12px] leading-relaxed text-fg"
    >
      <p className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-semibold text-danger">{isIncomplete ? "回答不完整" : "请求失败"}</span>
        <span className="font-mono text-[11px] text-fg-muted">{error.code}</span>
        {error.retryable ? <span className="text-fg-muted">可重试</span> : null}
      </p>
      <p className="break-words text-fg-muted">{error.message}</p>
      {error.requestId === undefined || error.requestId === null ? null : (
        <p className="text-[11px] text-fg-subtle">
          请求 ID：<span className="font-mono tabular">{error.requestId}</span>
        </p>
      )}
    </div>
  );
}

function formatCount(value: number | null): string {
  return value === null ? "—" : value.toLocaleString("zh-CN");
}
