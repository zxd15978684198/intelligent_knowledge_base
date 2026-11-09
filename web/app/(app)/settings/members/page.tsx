"use client";

import { useMemo } from "react";
import { Button, Card, CardContent, EmptyState, Skeleton } from "@/components/ui";
import { DisabledReason, useAuth, usePermissions } from "@/features/auth";
import { PERMISSIONS } from "@/lib/auth";
import type { MembershipDetail } from "@/lib/contracts";
import { MemberAddDialog } from "@/features/members/MemberAddDialog";
import { MemberTable } from "@/features/members/MemberTable";
import { OwnerTransferDialog } from "@/features/members/OwnerTransferDialog";
import { describeMemberError } from "@/features/members/errors";
import { useMembers, useRoleCatalog } from "@/features/members/hooks";
import { isOwnerRole } from "@/features/members/roles";
import type { RolePermissionView } from "@/features/members/api";

/**
 * 成员管理页。
 *
 * 语义先说清楚：这里管的是「已有账号加入本工作区」的成员关系，**不是邀请制**——没有
 * 邀请邮件、没有待接受状态、没有过期。加人就是按 principal_id 直接建一行成员记录。
 *
 * 也没有姓名/邮箱：后端 `GET .../members` 只返回 `{members: [{membership_id, workspace_id,
 * user_id, role}]}`，刻意不给用户名（否则成员列表就成了撞库接口）。所以页面一律显示
 * 短 UUID + 复制完整 ID。
 */
export default function MembersPage() {
  const { identity } = useAuth();
  const permissions = usePermissions();

  const workspaceId = identity?.default_workspace_id ?? null;
  const currentUserId = identity?.principal_id ?? null;
  const canManage = permissions.ready && permissions.can(PERMISSIONS.workspaceManageMembers);

  // 列表与角色目录都需要 workspace.manage_members（后端对两者都回 403）；
  // 没有权限时保持 disabled，避免把 403 渲染成"加载失败"。
  const membersQuery = useMembers({ workspaceId, enabled: canManage });
  const rolesQuery = useRoleCatalog({ workspaceId, enabled: canManage });

  const members = membersQuery.data?.members ?? null;
  const roles = rolesQuery.data?.roles ?? null;

  // 角色以列表为准，列表还没回来时退回 /auth/me 的 role。转移所有权之后列表会失效重取，
  // 所以这里能自己修好，不必等重新登录。
  const myRole = useMemo(() => {
    if (members !== null && currentUserId !== null) {
      const mine = members.find((member) => member.user_id === currentUserId);
      if (mine !== undefined) return mine.role;
    }
    return identity?.role ?? null;
  }, [members, currentUserId, identity?.role]);

  const isOwner = myRole !== null && isOwnerRole(myRole);
  const memberCount = members?.length ?? 0;

  return (
    <main className="flex flex-col gap-6">
      <header className="flex flex-col gap-1.5">
        <h1 className="text-[28px]/[32px] font-semibold tracking-tight leading-tight text-fg">成员</h1>
        <p className="max-w-[65ch] text-[13px] leading-relaxed text-fg-muted">
          本页管理的是「已有账号加入本工作区」的成员关系，不是邀请制：按 principal_id 直接建立成员行，不发邮件、也没有待接受状态。列表只显示 user_id，不显示姓名和邮箱（后端刻意不返回）。角色来自本工作区的 roles 表；owner 只能通过「转移所有权」变更，加人和改角色都设不出 owner。
        </p>
      </header>

      {workspaceId === null ? (
        <p role="status" className="text-[13px] leading-relaxed text-fg-muted">
          正在读取当前工作区…
        </p>
      ) : !permissions.ready ? (
        <p role="status" className="text-[13px] leading-relaxed text-fg-muted">
          正在确认你的工作区权限…
        </p>
      ) : !canManage ? (
        <DisabledReason permission={PERMISSIONS.workspaceManageMembers}>
          成员列表也依赖这条权限：后端对 GET .../members 直接回 403，所以这里不会替你发请求。
        </DisabledReason>
      ) : (
        <>
          <section
            aria-label="成员操作"
            className="flex flex-wrap items-start justify-between gap-3"
          >
            <div className="flex flex-wrap items-start gap-3">
              <MemberAddDialog
                workspaceId={workspaceId}
                token={null}
                roles={roles}
                canManage={canManage}
              />
              <OwnerTransferDialog
                workspaceId={workspaceId}
                token={null}
                members={members ?? []}
                currentUserId={currentUserId}
                isOwner={isOwner}
              />
            </div>
            <span className="tabular text-[12px] text-fg-muted">共 {memberCount} 名成员</span>
          </section>

          {rolesQuery.isError ? (
            <p role="alert" className="flex flex-wrap items-baseline gap-x-1.5 rounded-lg border border-danger bg-danger-weak px-3 py-2 text-[13px] leading-relaxed text-danger">
              <span className="font-semibold">角色目录读取失败：</span>
              <span>{describeMemberError(rolesQuery.error, "roles")}</span>
              <span className="text-fg-muted">成员列表不受影响，但角色下拉会退化成只读；请稍后重试或联系管理员。</span>
            </p>
          ) : null}

          <MemberListSection
            members={members}
            isPending={membersQuery.isPending}
            isError={membersQuery.isError}
            error={membersQuery.error}
            onRetry={() => {
              void membersQuery.refetch();
            }}
            roles={roles}
            workspaceId={workspaceId}
            canManage={canManage}
            currentUserId={currentUserId}
          />
        </>
      )}
    </main>
  );
}

interface MemberListSectionProps {
  readonly members: readonly MembershipDetail[] | null;
  readonly isPending: boolean;
  readonly isError: boolean;
  readonly error: unknown;
  readonly onRetry: () => void;
  readonly roles: readonly RolePermissionView[] | null;
  readonly workspaceId: string | null;
  readonly canManage: boolean;
  readonly currentUserId: string | null;
}

function MemberListSection({
  members,
  isPending,
  isError,
  error,
  onRetry,
  roles,
  workspaceId,
  canManage,
  currentUserId,
}: MemberListSectionProps) {
  if (isPending) {
    return (
      <div className="flex flex-col gap-2" aria-busy="true" aria-live="polite">
        <span className="sr-only">正在加载成员列表…</span>
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <Card>
        <CardContent className="flex flex-col gap-3">
          <p role="alert" className="text-[13px] leading-relaxed text-danger">
            {describeMemberError(error, "load")}
          </p>
          <div>
            <Button variant="secondary" onClick={onRetry}>
              重试
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (members === null || members.length === 0) {
    return (
      <EmptyState
        title="还没有成员记录"
        description="用上方「按 principal_id 添加成员」把已有账号加入本工作区；加完就能在这里看到他的角色。"
      />
    );
  }

  return (
    <section className="flex flex-col gap-3" aria-label="成员列表">
      <MemberTable
        members={members}
        roles={roles}
        workspaceId={workspaceId}
        token={null}
        canManage={canManage}
        currentUserId={currentUserId}
      />
    </section>
  );
}
