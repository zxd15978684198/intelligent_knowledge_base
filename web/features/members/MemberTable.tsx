"use client";

import { useId, useState } from "react";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogTrigger,
  Label,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui";
import type { MembershipDetail } from "@/lib/contracts";
import { shortId } from "@/lib/utils";
import type { RolePermissionView } from "./api";
import { describeMemberError } from "./errors";
import { useRemoveMember, useUpdateMemberRole } from "./hooks";
import { RoleSelect } from "./RoleSelect";
import { describeRole, isOwnerRole, roleBadgeTone } from "./roles";

export interface MemberTableProps {
  readonly members: readonly MembershipDetail[];
  readonly roles: readonly RolePermissionView[] | null;
  readonly workspaceId: string | null;
  readonly token: string | null;
  /** 是否有 `workspace.manage_members`。false 时整列操作只读，不渲染任何能点的按钮。 */
  readonly canManage: boolean;
  readonly currentUserId: string | null;
}

/**
 * 成员表。
 *
 * 只有三列：`user_id`（短 UUID + 复制）、角色、操作。没有姓名/邮箱列——后端不返回，
 * 前端也不猜（用户名的唯一来源是 `/auth/me`，而那是"我自己"）。
 */
export function MemberTable({
  members,
  roles,
  workspaceId,
  token,
  canManage,
  currentUserId,
}: MemberTableProps) {
  return (
    <Table aria-label="成员列表" containerClassName="rounded-lg border border-border">
      <TableHeader>
        <TableRow>
          <TableHead>用户 ID</TableHead>
          <TableHead>角色</TableHead>
          <TableHead className="text-right">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {members.map((member) => (
          <MemberRow
            key={member.membership_id}
            member={member}
            roles={roles}
            workspaceId={workspaceId}
            token={token}
            canManage={canManage}
            isSelf={currentUserId !== null && member.user_id === currentUserId}
          />
        ))}
      </TableBody>
    </Table>
  );
}

interface MemberRowProps {
  readonly member: MembershipDetail;
  readonly roles: readonly RolePermissionView[] | null;
  readonly workspaceId: string | null;
  readonly token: string | null;
  readonly canManage: boolean;
  readonly isSelf: boolean;
}

function MemberRow({ member, roles, workspaceId, token, canManage, isSelf }: MemberRowProps) {
  const roleOptions = roles ?? [];
  // owner 的角色与"没有角色目录"这两种情况都不能改：前者后端会 422，后者没有可选值。
  const canEditRole = canManage && roleOptions.length > 0 && !isOwnerRole(member.role);

  return (
    <TableRow>
      <TableCell>
        <div className="flex flex-wrap items-center gap-2">
          <code className="font-mono tabular text-[13px] text-fg" title={member.user_id}>
            {shortId(member.user_id)}
          </code>
          {isSelf ? <Badge tone="info">我</Badge> : null}
          <CopyUserIdButton userId={member.user_id} />
        </div>
      </TableCell>
      <TableCell>
        {canEditRole ? (
          <RoleCell
            member={member}
            roles={roleOptions}
            workspaceId={workspaceId}
            token={token}
          />
        ) : (
          <div className="flex flex-col gap-1">
            <Badge tone={roleBadgeTone(member.role)}>{describeRole(member.role)}</Badge>
            {isOwnerRole(member.role) ? (
            <span className="text-[12px] leading-relaxed text-fg-muted">所有者只能用「转移所有权」变更</span>
            ) : null}
            {canManage && roleOptions.length === 0 && !isOwnerRole(member.role) ? (
              <span className="text-[12px] leading-relaxed text-fg-muted">角色目录未就绪，暂时只读</span>
            ) : null}
          </div>
        )}
      </TableCell>
      <TableCell className="text-right">
        {canManage ? (
          <RemoveMemberAction
            member={member}
            workspaceId={workspaceId}
            token={token}
            isSelf={isSelf}
          />
        ) : (
          <span className="text-[12px] leading-relaxed text-fg-muted">无管理权限</span>
        )}
      </TableCell>
    </TableRow>
  );
}

interface RoleCellProps {
  readonly member: MembershipDetail;
  readonly roles: readonly RolePermissionView[];
  readonly workspaceId: string | null;
  readonly token: string | null;
}

/** 行内改角色：选中即提交（PATCH），失败就地显示原因，不回滚成假状态。 */
function RoleCell({ member, roles, workspaceId, token }: RoleCellProps) {
  const [error, setError] = useState<string | null>(null);
  const updateRole = useUpdateMemberRole({ workspaceId, token });

  async function handleChange(nextRole: string) {
    if (nextRole === member.role) return;
    setError(null);
    try {
      await updateRole.mutateAsync({ membershipId: member.membership_id, role: nextRole });
    } catch (cause) {
      setError(describeMemberError(cause, "update-role"));
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <RoleSelect
        id={`member-role-${member.membership_id}`}
        label={`修改成员 ${shortId(member.user_id)} 的角色`}
        value={member.role}
        roles={roles}
        pending={updateRole.isPending}
        onChange={(nextRole) => {
          void handleChange(nextRole);
        }}
      />
      {error !== null ? (
        <p role="alert" className="text-[12px] leading-relaxed text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

interface CopyUserIdButtonProps {
  readonly userId: string;
}

/** 复制完整 UUID：表格里只显示短 id，但排查问题时要的是全的。 */
function CopyUserIdButton({ userId }: CopyUserIdButtonProps) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  async function handleCopy() {
    const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;
    if (clipboard === undefined) {
      setState("failed");
      return;
    }
    try {
      await clipboard.writeText(userId);
      setState("copied");
    } catch {
      setState("failed");
    }
  }

  return (
    <span className="inline-flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        aria-label={`复制用户 ID（${userId}）`}
        onClick={() => {
          void handleCopy();
        }}
      >
        复制
      </Button>
      <span className="text-[12px] leading-relaxed text-fg-muted" aria-live="polite">
        {state === "copied" ? "已复制" : state === "failed" ? "复制失败，请手动选中" : ""}
      </span>
    </span>
  );
}

interface RemoveMemberActionProps {
  readonly member: MembershipDetail;
  readonly workspaceId: string | null;
  readonly token: string | null;
  readonly isSelf: boolean;
}

/**
 * 移除成员：两段式确认。
 *
 * 第一段是打开对话框（显式表达意图），第二段是勾选"我确认"后才解锁危险按钮——只做
 * 一次确认的删除，在列表里和"复制"按钮挨太近，误点代价不可逆。
 */
function RemoveMemberAction({ member, workspaceId, token, isSelf }: RemoveMemberActionProps) {
  const [open, setOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirmId = useId();
  const removeMember = useRemoveMember({ workspaceId, token });

  const actionLabel = isSelf ? "退出" : "移除";
  const dialogTitle = isSelf ? "退出这个工作区？" : "移除这个成员？";

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (next) {
      setConfirmed(false);
      setError(null);
    }
  }

  async function handleConfirm() {
    setError(null);
    try {
      await removeMember.mutateAsync({ membershipId: member.membership_id });
      setOpen(false);
    } catch (cause) {
      setError(describeMemberError(cause, "remove"));
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant={isSelf ? "secondary" : "ghost"} size="sm" aria-label={`${actionLabel}成员（${member.user_id}）`}>
          {actionLabel}
        </Button>
      </DialogTrigger>
      <DialogContent
        title={dialogTitle}
        description={
          isSelf
            ? "退出后你会立刻失去本工作区的访问权；再回来需要由管理员重新按 principal_id 添加。"
            : "移除后该成员立刻失去本工作区的访问权；成员行会被删除，不是禁用。"
        }
        width="sm"
      >
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <span className="text-sm text-fg">目标成员</span>
            <code className="font-mono text-sm text-fg-muted">{member.user_id}</code>
            <span className="text-xs text-fg-muted">
              角色：{describeRole(member.role)}（{member.role}）
            </span>
          </div>

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
              {isSelf
                ? "我确认退出，并接受立刻失去本工作区的访问权。"
                : "我确认移除该成员，并接受他立刻失去本工作区的访问权。"}
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
              variant="danger"
              disabled={!confirmed || removeMember.isPending}
              loading={removeMember.isPending}
              onClick={() => {
                void handleConfirm();
              }}
            >
              {isSelf ? "确认退出" : "确认移除"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
