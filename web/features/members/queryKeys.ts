import { qk } from "@/lib/api/hooks";

/** 还没拿到 identity 时的占位；与 `features/kbs/hooks.ts` 的占位值保持一致。 */
export const DEFAULT_WORKSPACE_KEY = "default";

export type MemberWorkspaceId = string | null;

function scopeOf(workspaceId: MemberWorkspaceId): string {
  return workspaceId ?? DEFAULT_WORKSPACE_KEY;
}

/**
 * 成员列表的 key —— **复用** `qk.members(ws)`，不新增前缀。
 *
 * `tests/contract/query-keys.test.ts` 已经断言过这个 key，另起一个前缀会让契约测试红。
 */
export function membersKey(workspaceId: MemberWorkspaceId): readonly unknown[] {
  return qk.members(scopeOf(workspaceId));
}

/**
 * 角色目录。
 *
 * 挂在 `members` 前缀下面是有意的：成员角色改了，目录里的权限点解释也可能过期，跟着列表
 * 一起失效最省心。同时它比 `membersKey` 长，不会反过来让失效范围变大。
 */
export function roleCatalogKey(workspaceId: MemberWorkspaceId): readonly unknown[] {
  return [...qk.members(scopeOf(workspaceId)), "roles"];
}

/**
 * 我的权限点自省。
 *
 * 改自己的角色、移除自己、转移所有权之后都要重取：这三个操作都会改变"我能做什么"。
 */
export function myPermissionsKey(workspaceId: MemberWorkspaceId): readonly unknown[] {
  return [...qk.members(scopeOf(workspaceId)), "permissions"];
}
