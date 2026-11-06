import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";
import type { MemberAdd, MemberPage, MembershipDetail } from "@/lib/contracts";
import {
  addMember,
  getMyPermissions,
  listMembers,
  listRoles,
  removeMember,
  transferOwnership,
  updateMemberRole,
  type MemberAuth,
  type PermissionSummary,
  type RoleCatalog,
} from "./api";
import { isConflict, isNotFound } from "./errors";
import {
  membersKey,
  myPermissionsKey,
  roleCatalogKey,
  type MemberWorkspaceId,
} from "./queryKeys";

/**
 * 成员管理的 TanStack Query 封装。
 *
 * 三条约定：
 * 1. `workspaceId` 为 null 或没有 token 时查询保持 disabled——没登录/身份未就绪就去打
 *    接口，只会拿到 401 再把页面变成错误态，那是假故障。
 * 2. 所有写操作成功后都失效成员列表；改角色/移除还会失效"我的权限"，因为这两条都能
 *    作用在自己身上。
 * 3. 改角色/移除的 404 既可能是"没权限"也可能是"记录没了"，两种情况都意味着本地列表
 *    过期了，所以失败时也失效一次列表。

 */

/** 查询作用域：工作区 + token + 是否允许立即发请求。 */
export interface MemberQueryScope extends MemberAuth {
  readonly workspaceId?: MemberWorkspaceId;
  readonly enabled?: boolean;
}

/** 写操作作用域：同上，但没有 `enabled`（mutation 本来就不会自己触发）。 */
export interface MemberMutationScope extends MemberAuth {
  readonly workspaceId?: MemberWorkspaceId;
}

/**
 * 工作区就绪判定。
 *
 * Cookie 化之后**不再要求 token**：身份在 HttpOnly Cookie 里、登录态由
 * `AuthProvider.status` 决定，前端没有可读令牌可判。只要页面传了 workspaceId
 * （`useAuth().identity.default_workspace_id`）就允许发请求；未登录时后端会 401，
 * 由 `apiRequest` 的 401 单飞刷新 / 跳登录接管，不会像旧版那样「因为没 token 而永远空列表」。
 */
function isScopeReady(workspaceId: string | null): boolean {
  return workspaceId !== null;
}

function scopeEnabled(scope: MemberQueryScope): boolean {
  return (scope.enabled ?? true) && isScopeReady(scope.workspaceId ?? null);
}

/** 成员列表。鉴权/工作区未就绪时 disabled，页面用 `isPending` 显示骨架。 */
export function useMembers(scope: MemberQueryScope = {}): UseQueryResult<MemberPage> {
  const workspaceId = scope.workspaceId ?? null;
  const token = scope.token ?? null;

  return useQuery<MemberPage>({
    queryKey: membersKey(workspaceId),
    queryFn: () => {
      if (workspaceId === null) {
        // 只有 enabled 为真才会走到这里；留着这行是为了让类型收窄，而不是非空断言。
        throw new Error("成员列表缺少工作区 id");
      }
      return listMembers(workspaceId, { token });
    },
    enabled: scopeEnabled(scope),
  });
}

/**
 * 角色目录。默认只在有「管理成员」时打开（后端对该接口直接回 403）。
 */
export function useRoleCatalog(scope: MemberQueryScope = {}): UseQueryResult<RoleCatalog> {
  const workspaceId = scope.workspaceId ?? null;
  const token = scope.token ?? null;

  return useQuery<RoleCatalog>({
    queryKey: roleCatalogKey(workspaceId),
    queryFn: () => {
      if (workspaceId === null) {
        throw new Error("角色目录缺少工作区 id");
      }
      return listRoles(workspaceId, { token });
    },
    enabled: scopeEnabled(scope),
  });
}

/** 我的权限点自省（不需要任何管理权限，非成员 404）。 */
export function useMyPermissions(scope: MemberQueryScope = {}): UseQueryResult<PermissionSummary> {
  const workspaceId = scope.workspaceId ?? null;
  const token = scope.token ?? null;

  return useQuery<PermissionSummary>({
    queryKey: myPermissionsKey(workspaceId),
    queryFn: () => {
      if (workspaceId === null) {
        throw new Error("权限自省缺少工作区 id");
      }
      return getMyPermissions(workspaceId, { token });
    },
    enabled: scopeEnabled(scope),
  });
}

/** 按 principal_id 添加成员。变量直接用后端的 `{user_id, role}`，不做无意义的字段改名。 */
export function useAddMember(
  scope: MemberMutationScope = {},
): UseMutationResult<MembershipDetail, Error, MemberAdd> {
  const workspaceId = scope.workspaceId ?? null;
  const token = scope.token ?? null;
  const queryClient = useQueryClient();

  return useMutation<MembershipDetail, Error, MemberAdd>({
    mutationFn: (input: MemberAdd) => {
      if (workspaceId === null) {
        throw new Error("添加成员缺少工作区 id");
      }
      return addMember(workspaceId, input, { token });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: membersKey(workspaceId) });
    },
  });
}

export interface UpdateMemberRoleVariables {
  readonly membershipId: string;
  readonly role: string;
}

export interface RemoveMemberVariables {
  readonly membershipId: string;
}

export interface TransferOwnershipVariables {
  readonly ownerPrincipalId: string;
}

/** 改角色（PATCH）。成功后列表与"我的权限"一起失效。 */
export function useUpdateMemberRole(
  scope: MemberMutationScope = {},
): UseMutationResult<MembershipDetail, Error, UpdateMemberRoleVariables> {
  const workspaceId = scope.workspaceId ?? null;
  const token = scope.token ?? null;
  const queryClient = useQueryClient();

  return useMutation<MembershipDetail, Error, UpdateMemberRoleVariables>({
    mutationFn: (variables: UpdateMemberRoleVariables) => {
      if (workspaceId === null) {
        throw new Error("修改角色缺少工作区 id");
      }
      return updateMemberRole(workspaceId, variables.membershipId, variables.role, { token });
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: membersKey(workspaceId) }),
        queryClient.invalidateQueries({ queryKey: myPermissionsKey(workspaceId) }),
      ]);
    },
    onError: async (error: Error) => {
      // 404 = 没权限或记录已没了，两种都说明本地列表过期。
      if (isNotFound(error)) {
        await queryClient.invalidateQueries({ queryKey: membersKey(workspaceId) });
      }
    },
  });
}

/** 移除成员（204）。移除自己等于主动退出，所以也要刷新"我的权限"。 */
export function useRemoveMember(
  scope: MemberMutationScope = {},
): UseMutationResult<void, Error, RemoveMemberVariables> {
  const workspaceId = scope.workspaceId ?? null;
  const token = scope.token ?? null;
  const queryClient = useQueryClient();

  return useMutation<void, Error, RemoveMemberVariables>({
    mutationFn: (variables: RemoveMemberVariables) => {
      if (workspaceId === null) {
        throw new Error("移除成员缺少工作区 id");
      }
      return removeMember(workspaceId, variables.membershipId, { token });
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: membersKey(workspaceId) }),
        queryClient.invalidateQueries({ queryKey: myPermissionsKey(workspaceId) }),
      ]);
    },
    onError: async (error: Error) => {
      if (isNotFound(error)) {
        await queryClient.invalidateQueries({ queryKey: membersKey(workspaceId) });
      }
    },
  });
}

/** 转移所有权（204）。原 owner 降为 admin，所以"我的权限"必须重取。 */
export function useTransferOwnership(
  scope: MemberMutationScope = {},
): UseMutationResult<void, Error, TransferOwnershipVariables> {
  const workspaceId = scope.workspaceId ?? null;
  const token = scope.token ?? null;
  const queryClient = useQueryClient();

  return useMutation<void, Error, TransferOwnershipVariables>({
    mutationFn: (variables: TransferOwnershipVariables) => {
      if (workspaceId === null) {
        throw new Error("转移所有权缺少工作区 id");
      }
      return transferOwnership(workspaceId, variables.ownerPrincipalId, { token });
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: membersKey(workspaceId) }),
        queryClient.invalidateQueries({ queryKey: myPermissionsKey(workspaceId) }),
      ]);
    },
    onError: async (error: Error) => {
      if (isNotFound(error) || isConflict(error)) {
        await queryClient.invalidateQueries({ queryKey: membersKey(workspaceId) });
      }
    },
  });
}
