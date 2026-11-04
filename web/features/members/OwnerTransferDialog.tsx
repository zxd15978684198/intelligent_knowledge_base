"use client";

import { useId, useState, type FormEvent } from "react";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogTrigger,
  Field,
  Label,
  Select,
} from "@/components/ui";
import type { MembershipDetail } from "@/lib/contracts";
import { shortId } from "@/lib/utils";
import { describeMemberError } from "./errors";
import { useTransferOwnership } from "./hooks";
import { describeRole, isOwnerRole } from "./roles";

export interface OwnerTransferDialogProps {
  readonly workspaceId: string | null;
  readonly token: string | null;
  readonly members: readonly MembershipDetail[];
  readonly currentUserId: string | null;
  /** 只有当前所有者能转移（后端对非 owner 回 403）。 */
  readonly isOwner: boolean;
}

/**
 * 转移所有权 —— 本页唯一的不可逆操作，所以做成两段式：
 *
 * 1. 先选接任者（不能是自己，后端 422 "already the owner"）；
 * 2. 再勾选确认，才解锁提交。
 *
 * 文案按后端 `transfer_ownership` 的真实语义写：三个 UPDATE 一起生效——原 owner 降为
 * `admin`、目标升为 `owner`、`workspaces.owner_principal_id` 换人。不是"加一个新 owner"，
 * 而是"换一个 owner，你留在工作区里当管理员"。
 */
export function OwnerTransferDialog({
  workspaceId,
  token,
  members,
  currentUserId,
  isOwner,
}: OwnerTransferDialogProps) {
  const [open, setOpen] = useState(false);
  const [targetId, setTargetId] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirmId = useId();
  const targetFieldId = useId();
  const transfer = useTransferOwnership({ workspaceId, token });

  const candidates = members.filter(
    (member) => member.user_id !== currentUserId && !isOwnerRole(member.role),
  );
  const canTransfer = isOwner && candidates.length > 0;

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (next) {
      setTargetId("");
      setConfirmed(false);
      setError(null);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (targetId.length === 0) {
      setError("请选择要接任所有者的成员。");
      return;
    }
    if (!confirmed) {
      setError("请先勾选确认。转移所有权是不可逆的管理动作。");
      return;
    }

    try {
      await transfer.mutateAsync({ ownerPrincipalId: targetId });
      setOpen(false);
    } catch (cause) {
      setError(describeMemberError(cause, "transfer"));
    }
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogTrigger asChild>
          <Button variant="danger" disabled={!canTransfer}>
            转移所有权
          </Button>
        </DialogTrigger>
        <DialogContent
          title="转移所有权？"
          description="这是对本工作区管理员权限的交接，提交后立即生效。"
          width="md"
        >
          <form
            className="flex flex-col gap-4"
            noValidate
            onSubmit={(event) => {
              void handleSubmit(event);
            }}
          >
            <div className="flex flex-col gap-1 rounded-md border border-border bg-surface-muted p-3">
              <p className="text-sm text-fg">提交后会发生三件事</p>
              <ul className="list-disc pl-5 text-xs text-fg-muted">
                <li>你的成员角色从 owner 降为 admin：你还在工作区里，但不能再加人、改角色。</li>
                <li>被选中的成员升为 owner，拿到所有管理权限。</li>
                <li>工作区的 owner_principal_id 指向新所有者，三处改动在同一事务里完成。</li>
              </ul>
            </div>

            <Field
              id={targetFieldId}
              label="接任者"
              required
              hint="只能选当前工作区里已经在册、且不是 owner 的成员。"
            >
              <Select
                value={targetId}
                onChange={(event) => {
                  setTargetId(event.target.value);
                  setError(null);
                }}
              >
                <option value="">请选择成员</option>
                {candidates.map((member) => (
                  <option key={member.membership_id} value={member.user_id}>
                    {shortId(member.user_id)}（{describeRole(member.role)}）
                  </option>
                ))}
              </Select>
            </Field>

            {targetId.length > 0 ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-fg-muted">选中：</span>
                <Badge tone="info">{shortId(targetId)}</Badge>
                <code className="font-mono text-xs text-fg-muted">{targetId}</code>
              </div>
            ) : null}

            <div className="flex items-start gap-2">
              <input
                id={confirmId}
                type="checkbox"
                className="mt-1 h-4 w-4"
                checked={confirmed}
                onChange={(event) => {
                  setConfirmed(event.target.checked);
                }}
              />
              <Label htmlFor={confirmId} className="text-sm text-fg">
                我确认把所有者身份交给这位成员，并接受自己降为 admin。
              </Label>
            </div>

            {error !== null ? (
              <p role="alert" className="text-sm text-danger">
                {error}
              </p>
            ) : null}

            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setOpen(false)}>
                取消
              </Button>
              <Button
                type="submit"
                variant="danger"
                loading={transfer.isPending}
                disabled={!confirmed || targetId.length === 0}
              >
                确认转移
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
      {!canTransfer ? (
        <p className="text-xs text-fg-muted">
          {isOwner ? "没有其他可接任的成员。" : "只有当前所有者可以转移所有权。"}
        </p>
      ) : null}
    </div>
  );
}
