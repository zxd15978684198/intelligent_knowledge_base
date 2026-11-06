import { apiRequest } from "@/lib/api/client";
import type { MemberAdd, MemberPage, MembershipDetail } from "@/lib/contracts";

/**
 * 成员管理接口层。
 *
 * 契约以 `src/pyknora/api/v1/workspaces.py` 为准（2026-11-02 逐行核对）。这个后端有四处
 * 与"常见的 REST 习惯"不同，照着习惯写必错，所以逐条写在这里：
 * 1. 改角色是 `PATCH`，不是 `PUT`；
 * 2. 转移所有权的请求体键名是 `owner_principal_id`，不是 `user_id` / `principal_id`；
 * 3. 改角色、删成员在无权限时回 **404**（`not_found("membership")`）而不是 403——不承认
 *    那一行存在；只有加成员、读角色目录、转移所有权是 403；
 * 4. 列表响应的键是 `members`，没有分页，**不含用户名/邮箱**（后端刻意只回 `user_id`，
 *    避免把成员列表变成撞库接口）。
 *
 * 认证：浏览器走会话 Cookie（`apiRequest` 省略 `token` 时带 credentials + CSRF 头），
 * 因此页面不必再传身份。`MemberAuth.token` 只保留给命令行 / 评测 / 换账号的 Bearer
 * 兼容通道：传字符串时由 `apiRequest` 加 `Authorization`，其余情况一律走 Cookie。
 */

const WORKSPACES_PATH = "/api/v1/workspaces";

/** 调用方身份。token 缺失时后端回 401，前端不替它猜。 */
export interface MemberAuth {
  readonly token?: string | null;
}

/** `GET .../roles` 的一行。键名是 `role_key`，不是 `role`。 */
export interface RolePermissionView {
  readonly role_key: string;
  readonly permissions: readonly string[];
}

/** `GET .../roles` 的响应。角色集合由 `roles` 表决定（管理端可新建），前端不写死枚举。 */
export interface RoleCatalog {
  readonly roles: readonly RolePermissionView[];
}

/**
 * `GET .../permissions` 的响应：**只描述我自己**。
 *
 * 它不能回答"admin 能做什么"——那是 `/roles` 的事。两者别混用。
 */
export interface PermissionSummary {
  readonly workspace_id: string;
  readonly role: string;
  readonly permissions: readonly string[];
}

/** 转移所有权的请求体。键名必须是 `owner_principal_id`。 */
export interface OwnerTransferInput {
  readonly owner_principal_id: string;
}

function membersPath(workspaceId: string): string {
  return `${WORKSPACES_PATH}/${workspaceId}/members`;
}

function membershipPath(workspaceId: string, membershipId: string): string {
  return `${membersPath(workspaceId)}/${membershipId}`;
}

/**
 * 把 token 与 workspace 头收进请求初始化。
 *
 * `workspaceId` 同时进路径与 `X-Workspace-ID` 头：后端两个都校验，少了头会被当跨区请求。
 */
function authInit(
  workspaceId: string,
  auth: MemberAuth,
): { workspaceId: string; token?: string } {
  const init: { workspaceId: string; token?: string } = { workspaceId };
  if (auth.token) {
    init.token = auth.token;
  }
  return init;
}

/**
 * 成员列表。
 *
 * 响应是 `{members: [...]}`，没有 `items`、没有 `total`、没有分页参数——成员不分页。
 * 需要 `workspace.manage_members`，否则 403。
 */
export function listMembers(workspaceId: string, auth: MemberAuth = {}): Promise<MemberPage> {
  return apiRequest<MemberPage>(membersPath(workspaceId), {
    ...authInit(workspaceId, auth),
    method: "GET",
  });
}

/**
 * 角色目录：解释"每个角色有哪些权限点"，用来渲染角色下拉与权限提示。
 *
 * 需要 `workspace.manage_members`（403）。没有权限点的角色也会出现，`permissions` 是空数组。
 */
export function listRoles(workspaceId: string, auth: MemberAuth = {}): Promise<RoleCatalog> {
  return apiRequest<RoleCatalog>(`${WORKSPACES_PATH}/${workspaceId}/roles`, {
    ...authInit(workspaceId, auth),
    method: "GET",
  });
}

/** 我在这个工作区的权限点自省。任何人可读；非成员 404。 */
export function getMyPermissions(
  workspaceId: string,
  auth: MemberAuth = {},
): Promise<PermissionSummary> {
  return apiRequest<PermissionSummary>(`${WORKSPACES_PATH}/${workspaceId}/permissions`, {
    ...authInit(workspaceId, auth),
    method: "GET",
  });
}

/**
 * 按 principal_id 添加成员 → 201。
 *
 * 没有邀请流程：`user_id` 必须是**已经存在且 active** 的 principal，否则 404（后端刻意
 * 不区分"不存在"与"已停用"，区分等于确认这个 id 存在过）。`role` 为 `owner` 会被 422
 * 挡回，并提示走转移所有权。
 */
export function addMember(
  workspaceId: string,
  input: MemberAdd,
  auth: MemberAuth = {},
): Promise<MembershipDetail> {
  return apiRequest<MembershipDetail>(membersPath(workspaceId), {
    ...authInit(workspaceId, auth),
    method: "POST",
    body: input,
  });
}

/**
 * 改角色 → 200。方法是 PATCH。
 *
 * 只收 `{role}`；走 `owner` 会被 422，改 owner 本人的角色也会被 422（后端没有 details
 * 区分这两条，提示文案在 `errors.ts` 里按 action 给）。
 */
export function updateMemberRole(
  workspaceId: string,
  membershipId: string,
  role: string,
  auth: MemberAuth = {},
): Promise<MembershipDetail> {
  return apiRequest<MembershipDetail>(membershipPath(workspaceId, membershipId), {
    ...authInit(workspaceId, auth),
    method: "PATCH",
    body: { role },
  });
}

/**
 * 移除成员 → 204（无响应体）。
 *
 * 删自己等于主动退出（后端 `_can_remove` 对"目标就是自己"单独放行）；删 owner 一律 422。
 */
export async function removeMember(
  workspaceId: string,
  membershipId: string,
  auth: MemberAuth = {},
): Promise<void> {
  await apiRequest<void>(membershipPath(workspaceId, membershipId), {
    ...authInit(workspaceId, auth),
    method: "DELETE",
  });
}

/**
 * 转移所有权 → 204。
 *
 * 后端在同一事务里改三处：原 owner 的成员行降为 `admin`、目标成员行升为 `owner`、
 * `workspaces.owner_principal_id` 换人（带乐观并发）。所以这里的 UI 文案是确定的：
 * 转移之后你会是 admin，不是 member，人还在工作区里。
 */
export async function transferOwnership(
  workspaceId: string,
  ownerPrincipalId: string,
  auth: MemberAuth = {},
): Promise<void> {
  const body: OwnerTransferInput = { owner_principal_id: ownerPrincipalId };
  await apiRequest<void>(`${WORKSPACES_PATH}/${workspaceId}/owner`, {
    ...authInit(workspaceId, auth),
    method: "PUT",
    body,
  });
}
