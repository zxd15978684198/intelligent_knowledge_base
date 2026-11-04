"use client";

import { useId, useState, type FormEvent } from "react";
import { Button, Dialog, DialogContent, DialogTrigger, Field, Input } from "@/components/ui";
import type { KbSummary } from "./api";
import { describeKbError } from "./errors";
import { useDeleteKb, useRenameKb, type KbWorkspaceId } from "./hooks";

/**
 * 列表行的写操作：改名 / 删除。
 *
 * 两个操作都带 `If-Match`（当前行的 `row_version`）。冲突（409 `REVISION_CONFLICT`）时
 * **不关闭对话框**：mutation 的 onError 已经重新拉过列表，父组件下发的 `kb` 会带着新的
 * `row_version` 重渲染，用户看清提示后再点一次即可成功——这就是"重新读取最新版本，不静默覆盖"。
 */

/** 后端 `KnowledgeBasePatch.name` 约束：1–200 字符。 */
const NAME_MAX_LENGTH = 200;

export interface KbRowActionsProps {
  kb: KbSummary;
  workspaceId?: KbWorkspaceId;
}

export function KbRowActions({ kb, workspaceId = null }: KbRowActionsProps) {
  return (
    <div className="flex items-center justify-end gap-1">
      <RenameKbAction kb={kb} workspaceId={workspaceId} />
      <DeleteKbAction kb={kb} workspaceId={workspaceId} />
    </div>
  );
}

function RenameKbAction({ kb, workspaceId }: KbRowActionsProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(kb.name);
  const [error, setError] = useState<string | null>(null);
  const fieldId = useId();
  const rename = useRenameKb(workspaceId);

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen);
    if (nextOpen) {
      setName(kb.name);
      setError(null);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = name.trim();
    if (trimmed.length === 0) {
      setError("知识库名称不能为空。");
      return;
    }
    if (trimmed.length > NAME_MAX_LENGTH) {
      setError(`知识库名称不能超过 ${NAME_MAX_LENGTH} 个字符。`);
      return;
    }
    if (trimmed === kb.name) {
      setOpen(false);
      return;
    }

    setError(null);
    try {
      await rename.mutateAsync({ kbId: kb.kb_id, name: trimmed, rowVersion: kb.row_version });
      setOpen(false);
    } catch (submitError) {
      setError(describeKbError(submitError, "rename"));
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        {/*
          可见文字是「改名」，无障碍名里必须包含它，否则 axe 的
          label-content-name-mismatch（serious）会报「看得见的名字和读屏读出来的对不上」。
          这里用「改名「X」」而不是「重命名「X」」：读屏先读可见词，再补上下文。
        */}
        <Button variant="ghost" size="sm" aria-label={`改名「${kb.name}」`}>
          改名
        </Button>
      </DialogTrigger>
      <DialogContent
        title="重命名知识库"
        description={`当前版本 v${kb.row_version}。若已有其他人改过，本次保存会被拒绝并重新读取最新版本。`}
        width="sm"
      >
        <form className="flex flex-col gap-4" onSubmit={handleSubmit} noValidate>
          <Field
            id={fieldId}
            label="名称"
            required
            hint={`1–${NAME_MAX_LENGTH} 个字符。`}
            error={error ?? undefined}
          >
            <Input
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                setError(null);
              }}
              autoComplete="off"
              autoFocus
            />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button type="submit" loading={rename.isPending}>
              保存
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteKbAction({ kb, workspaceId }: KbRowActionsProps) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = useDeleteKb(workspaceId);

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen);
    if (nextOpen) setError(null);
  }

  async function handleConfirm() {
    setError(null);
    try {
      await remove.mutateAsync({ kbId: kb.kb_id, rowVersion: kb.row_version });
      setOpen(false);
    } catch (submitError) {
      setError(describeKbError(submitError, "delete"));
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm" aria-label={`删除「${kb.name}」`}>
          删除
        </Button>
      </DialogTrigger>
      <DialogContent
        title={`删除「${kb.name}」？`}
        description="删除后，该知识库下的文档与切片会一并下线，且无法恢复。"
        width="sm"
      >
        <div className="flex flex-col gap-4">
          <p className="text-sm text-fg-muted">
            回收在后台异步执行（接口返回 202 受理）。确认期间若有其他人修改过该知识库，
            本次删除会被拒绝，请重新确认。
          </p>
          {error ? (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              variant="danger"
              loading={remove.isPending}
              onClick={() => {
                void handleConfirm();
              }}
            >
              删除知识库
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
