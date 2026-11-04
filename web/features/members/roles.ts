import { describePermission } from "@/lib/auth";
import type { RolePermissionView } from "./api";

/**
 * 角色词汇表。
 *
 * 角色集合来自后端 `roles` 表（`GET .../roles`），管理端可以新建，所以这里只提供
 * "已知角色的中文名"与"未知角色原样显示"的兜底——**不能**把 `role` 写成 union。
 */

/** 保留角色：不能通过加成员或改角色产生，只能走「转移所有权」。 */
export const OWNER_ROLE = "owner";

/** 加成员时的默认角色（后端 `MemberAdd.role` 的默认值也是 member）。 */
export const DEFAULT_MEMBER_ROLE = "member";

/** 提示里最多列几个权限点，剩下的折成"等 N 项"。 */
const MAX_LISTED_PERMISSIONS = 4;

const ROLE_LABELS: Readonly<Record<string, string>> = {
  owner: "所有者",
  admin: "管理员",
  member: "成员",
  guest: "访客",
};

/** 角色中文名；未知角色原样返回（便于发现后端新建了角色，而不是显示"未知角色"）。 */
export function describeRole(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

/** 后端 `OWNER_ROLE` 的唯一判定方式（`role` 是字符串，不是枚举）。 */
export function isOwnerRole(role: string): boolean {
  return role === OWNER_ROLE;
}

export type RoleBadgeTone = "neutral" | "info" | "warning";

/** 角色徽章色调：owner 用 warning（它是"唯一且危险"的角色），admin 用 info。 */
export function roleBadgeTone(role: string): RoleBadgeTone {
  if (role === OWNER_ROLE) return "warning";
  if (role === "admin") return "info";
  return "neutral";
}

/** 可在下拉里分配的角色：去掉 owner（它只能由转移所有权产生）。 */
export function selectableRoles(
  roles: readonly RolePermissionView[],
): readonly RolePermissionView[] {
  return roles.filter((role) => role.role_key !== OWNER_ROLE);
}

/** 默认选中角色：优先 member，其次目录里的第一个可分配角色。空目录时也要有值可显示。 */
export function defaultMemberRole(roles: readonly RolePermissionView[]): string {
  const selectable = selectableRoles(roles);
  const preferred = selectable.find((role) => role.role_key === DEFAULT_MEMBER_ROLE);
  return preferred?.role_key ?? selectable[0]?.role_key ?? DEFAULT_MEMBER_ROLE;
}

/**
 * 用 `GET .../roles` 的数据解释"这个角色能做什么"。
 *
 * 权限点走 `describePermission` 翻中文；空数组是一个真实的合法状态（新建角色还没配权限），
 * 要说清楚，而不是显示"可做："。
 */
export function describeRolePermissions(role: RolePermissionView): string {
  if (role.permissions.length === 0) {
    return `${describeRole(role.role_key)}：还没有配置任何权限点。`;
  }
  const listed = role.permissions.slice(0, MAX_LISTED_PERMISSIONS).map(describePermission);
  const rest = role.permissions.length - listed.length;
  const head = `可做：${listed.join("、")}`;
  return rest > 0 ? `${head} 等 ${role.permissions.length} 项` : head;
}
