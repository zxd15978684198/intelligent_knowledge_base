"use client";

/**
 * 上传面板：拖拽 + 选择文件 → 校验 → 上传 → 按 202 / 200 两个分支收尾。
 *
 * 几个刻意的取舍：
 *
 * 1. **进度是不确定的**。浏览器只知道"请求发出去了、还没回来"，拿不到服务端解析/切片的
 *    百分比。画一个 37% 的假进度条比不画更糟——用户会以为它在动，然后永远停在 37%。
 *    所以这里用 `role="progressbar"` 且**不带 `aria-valuenow`**（ARIA 里这就是"不确定"），
 *    视觉上是一条脉冲条，并明说"进度未知"。
 * 2. **202 与 200 两个分支**。202 = 新建、有 `job_id`，要轮询；200 + `deduped: true` =
 *    内容哈希命中，`job_id` 为 null，**没有作业可等**。`apiUpload` 会回状态码，所以这里
 *    按真实状态码分支（`deduped` 只作为一致性校验）。
 * 3. **重传复用同一个 Idempotency-Key**。换新键会把重试变成新上传，同一份内容可能多建
 *    一个版本。键在"选中文件"时生成，重传原样复用，只有换文件才重新生成。
 * 4. 前端校验只是提前反馈，真正的白名单/大小校验在后端（见 `upload-constraints.ts`）。
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import type { ChangeEvent, DragEvent } from "react";

import { Button } from "@/components/ui";
import { cn, formatBytes, shortId } from "@/lib/utils";

import { describeApiError, formatErrorLabel } from "../jobs/errors";
import { JobPoller } from "../jobs/JobPoller";
import type { JobDetail } from "../jobs/types";
import { uploadDocument, type UploadDocumentResult } from "./api";
import { newIdempotencyKey } from "./idempotency";
import { documentKeys } from "./queryKeys";
import {
  ACCEPT_ATTRIBUTE,
  MAX_UPLOAD_BYTES,
  SUPPORTED_SUFFIXES_LABEL,
  validateUploadFile,
  type UploadRejection,
} from "./upload-constraints";

interface SelectedFile {
  file: File;
  /** 一次"上传意图"的幂等键；重传复用，换文件才重新生成。 */
  idempotencyKey: string;
}

export interface UploadPanelProps {
  kbId: string;
  /** 缓存键第二段（`qk` 的硬要求），同时进 `X-Workspace-ID`。 */
  workspaceId: string;
  className?: string;
}

export function UploadPanel({ kbId, workspaceId, className }: UploadPanelProps) {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);

  const [selected, setSelected] = useState<SelectedFile | null>(null);
  const [rejection, setRejection] = useState<UploadRejection | null>(null);
  const [dragging, setDragging] = useState(false);
  const [result, setResult] = useState<UploadDocumentResult | null>(null);

  const mutation = useMutation({
    mutationFn: (input: SelectedFile) =>
      uploadDocument({
        kbId,
        workspaceId,
        file: input.file,
        idempotencyKey: input.idempotencyKey,
      }),
    onSuccess: (uploaded) => {
      setResult(uploaded);
      // 文档已入册（无论 dedupe 与否），列表要立刻能看到它。
      void queryClient.invalidateQueries({ queryKey: documentKeys.listScope(workspaceId, kbId) });
    },
  });

  const refreshAfterJob = useCallback(
    (job: JobDetail) => {
      void queryClient.invalidateQueries({ queryKey: documentKeys.listScope(workspaceId, kbId) });
      void queryClient.invalidateQueries({
        queryKey: documentKeys.detail(workspaceId, job.document_id),
      });
    },
    [queryClient, workspaceId, kbId],
  );

  function resetOutcome(): void {
    setResult(null);
    mutation.reset();
  }

  function chooseFile(file: File): void {
    const problem = validateUploadFile(file);
    if (problem !== null) {
      setRejection(problem);
      setSelected(null);
      resetOutcome();
      return;
    }
    setRejection(null);
    resetOutcome();
    // 新文件 = 新的上传意图 = 新键。同一文件的每次重传都复用这个键。
    setSelected({ file, idempotencyKey: newIdempotencyKey() });
  }

  function handleInputChange(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.currentTarget.files?.item(0) ?? null;
    // 清空 value：否则连续选同一个文件不会再触发 change，用户会以为"点了没反应"。
    event.currentTarget.value = "";
    if (file !== null) chooseFile(file);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files.item(0);
    if (file !== null) chooseFile(file);
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    setDragging(true);
  }

  function startUpload(): void {
    if (selected === null) return;
    mutation.mutate(selected);
  }

  function clearSelection(): void {
    setSelected(null);
    setRejection(null);
    resetOutcome();
  }

  const errorDescription = mutation.isError ? describeApiError(mutation.error) : null;
  const accepted = result?.accepted ?? null;
  // 状态码是契约（202 新建 / 200 去重），`deduped` 用来交叉校验——两者不一致时以状态码为准并说明。
  const deduped = result !== null && (result.status === 200 || accepted?.deduped === true);
  const jobId = accepted?.job_id ?? null;

  return (
    <section
      className={cn("rounded-lg border border-border/60 bg-surface p-4 shadow-sm", className)}
      aria-labelledby="upload-panel-title"
    >
      <h2 id="upload-panel-title" className="text-sm font-medium text-fg">
        上传文档
      </h2>

      <div
        className={cn(
          "mt-3 flex flex-col items-start gap-2 rounded-md border border-dashed border-border-strong px-4 py-6",
          dragging && "border-primary bg-primary-weak",
        )}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={() => setDragging(false)}
      >
        <p className="text-sm text-fg">把文件拖到这里，或</p>
        <input
          ref={inputRef}
          id="document-upload-input"
          type="file"
          accept={ACCEPT_ATTRIBUTE}
          className="sr-only"
          aria-label="选择要上传的文档"
          aria-describedby="upload-panel-constraints"
          onChange={handleInputChange}
        />
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="w-fit"
          onClick={() => inputRef.current?.click()}
        >
          选择文件
        </Button>
        <p id="upload-panel-constraints" className="max-w-[65ch] text-xs leading-relaxed text-fg-muted">
          支持 {SUPPORTED_SUFFIXES_LABEL}；单个文件不超过 {formatBytes(MAX_UPLOAD_BYTES)}。
          文件类型与大小最终由服务端复核。
        </p>
      </div>

      {rejection !== null ? (
        <p
          role="alert"
          className="mt-3 max-w-[65ch] rounded-md border border-danger bg-danger-weak px-3 py-2 text-sm leading-relaxed text-danger"
        >
          {rejection.message}
        </p>
      ) : null}

      {selected !== null ? (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
          <span className="text-fg">{selected.file.name}</span>
          <span className="tabular text-xs text-fg-muted">{formatBytes(selected.file.size)}</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="w-fit text-xs text-fg-muted"
            onClick={clearSelection}
            disabled={mutation.isPending}
          >
            移除
          </Button>
          <Button
            type="button"
            size="sm"
            className="w-fit"
            onClick={startUpload}
            loading={mutation.isPending}
          >
            {mutation.isPending ? "上传中…" : "开始上传"}
          </Button>
        </div>
      ) : null}

      {mutation.isPending ? (
        <div className="mt-3" role="status" aria-live="polite">
          <p className="text-xs text-fg-muted">
            正在上传 {selected?.file.name ?? "文件"} … 服务端尚未返回，进度未知（不显示百分比：
            拿不到服务端的解析进度，编一个数字只会误导）。
          </p>
          <div
            role="progressbar"
            aria-label="上传进度（不确定）"
            className="mt-1 h-1 w-full overflow-hidden rounded-full bg-surface-muted"
          >
            <div className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
          </div>
        </div>
      ) : null}

      {errorDescription !== null ? (
        <div
          role="alert"
          className="mt-3 rounded-md border border-danger bg-danger-weak px-3 py-2 text-sm text-danger"
        >
          <p className="max-w-[65ch] leading-relaxed">我们没能上传这份文档：{formatErrorLabel(errorDescription)}</p>
          <p className="mt-1 text-xs">
            错误码 <code className="font-mono">{errorDescription.code === "" ? "（无）" : errorDescription.code}</code>
            {errorDescription.requestId !== null ? (
              <>
                {" "}
                · 请求标识 <code className="font-mono">{errorDescription.requestId}</code>
              </>
            ) : null}
          </p>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="mt-2 w-fit border-danger text-danger"
            onClick={startUpload}
            disabled={selected === null || mutation.isPending}
          >
            重传
          </Button>
          <p className="mt-1 max-w-[65ch] text-xs leading-relaxed">
            重传会复用同一个 <code className="font-mono">Idempotency-Key</code>
            ，服务端不会把重试当成第二次上传。
          </p>
        </div>
      ) : null}

      {accepted !== null && deduped ? (
        <div
          role="status"
          className="mt-3 rounded-md border border-info bg-info-weak px-3 py-2 text-sm text-info"
        >
          <p className="max-w-[65ch] leading-relaxed">
            这份内容此前已经上传过（服务端返回 {result?.status ?? 200} + deduped，内容哈希命中），
            没有新建作业，也不用等待。
          </p>
          <p className="mt-1 text-xs">
            文档 <span className="font-mono">{shortId(accepted.document_id)}</span> · 状态{" "}
            <code className="font-mono">{accepted.status}</code>
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Link
              className="text-xs underline underline-offset-2"
              href={`/kbs/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(accepted.document_id)}`}
            >
              查看文档详情
            </Link>
            <Button type="button" variant="ghost" size="sm" className="w-fit text-xs" onClick={clearSelection}>
              上传另一个文件
            </Button>
          </div>
        </div>
      ) : null}

      {accepted !== null && !deduped ? (
        <div className="mt-3">
          <p className="max-w-[65ch] text-sm leading-relaxed text-fg">
            已受理（{result?.status ?? 202}），作业已入队。文档{" "}
            <span className="font-mono">{shortId(accepted.document_id)}</span> 在摄取完成前不可检索。
          </p>
          {jobId !== null ? (
            <JobPoller jobId={jobId} workspaceId={workspaceId} onTerminal={refreshAfterJob} className="mt-2" />
          ) : (
            // 202 理论上必带 job_id；万一没有，如实说明而不是假装在等作业。
            <p className="mt-1 text-xs text-warning">服务端没有返回 job_id，无法跟踪这次上传的进度。</p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Link
              className="text-xs underline underline-offset-2"
              href={`/kbs/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(accepted.document_id)}`}
            >
              查看文档详情
            </Link>
            <Button type="button" variant="ghost" size="sm" className="w-fit text-xs" onClick={clearSelection}>
              上传另一个文件
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
