/**
 * 权限点目录——**必须与后端 `src/pyknora/services/auth/permissions.py` 的常量逐字一致**。
 *
 * 拼错一个字符的后果不是报错，而是静默 403（后端查不到这个权限点 = 没有权限）。
 * 因此这里只做三件事：声明常量、判权、给人看的说明文案；**不做**角色推导，
 * 也不假设 `document.write` 蕴含 `document.read`——后端 `role_grants` 是逐条授予的，
 * 没有通配、没有继承。
 *
 * 判权口径：前端只控制**体验**（显示/禁用按钮），不替代服务端授权。
 * 隐藏一个按钮不等于接口放行，反之亦然；任何越权请求都由服务端 403/401 兜底。
 */

/** 权限点常量（键名为前端 camelCase，值为后端字符串）。 */
export const PERMISSIONS = {
  knowledgeBaseRead: "knowledge_base.read",
  knowledgeBaseWrite: "knowledge_base.write",
  knowledgeBaseDelete: "knowledge_base.delete",

  documentRead: "document.read",
  documentWrite: "document.write",
  documentDelete: "document.delete",
  chunkRead: "chunk.read",

  jobRead: "job.read",
  jobCancel: "job.cancel",
  queryRun: "query.run",
  chatRun: "chat.run",
  lineageRead: "lineage.read",

  indexRebuild: "index.rebuild",
  workspaceManageMembers: "workspace.manage_members",
  workspaceManageRoles: "workspace.manage_roles",

  /** 平台面：当前没有任何角色持有，写端点只有平台运维能碰。 */
  platformManageRoles: "platform.manage_roles",
} as const;

export type Permission = (typeof PERMISSIONS)[keyof typeof PERMISSIONS];

/** 与后端 `ALL_PERMISSIONS`（工作区面 15 条）+ `PLATFORM_PERMISSIONS`（1 条）对应。 */
export const ALL_PERMISSIONS: readonly Permission[] = [
  PERMISSIONS.knowledgeBaseRead,
  PERMISSIONS.knowledgeBaseWrite,
  PERMISSIONS.knowledgeBaseDelete,
  PERMISSIONS.documentRead,
  PERMISSIONS.documentWrite,
  PERMISSIONS.documentDelete,
  PERMISSIONS.chunkRead,
  PERMISSIONS.jobRead,
  PERMISSIONS.jobCancel,
  PERMISSIONS.queryRun,
  PERMISSIONS.chatRun,
  PERMISSIONS.lineageRead,
  PERMISSIONS.indexRebuild,
  PERMISSIONS.workspaceManageMembers,
  PERMISSIONS.workspaceManageRoles,
  PERMISSIONS.platformManageRoles,
];

/** 中文说明：用于权限栅栏的禁用原因、403 提示，避免把 `document.write` 直接抛给用户。 */
export const PERMISSION_LABELS: Readonly<Record<Permission, string>> = {
  [PERMISSIONS.knowledgeBaseRead]: "查看知识库",
  [PERMISSIONS.knowledgeBaseWrite]: "创建或修改知识库",
  [PERMISSIONS.knowledgeBaseDelete]: "删除知识库",
  [PERMISSIONS.documentRead]: "查看文档与切片",
  [PERMISSIONS.documentWrite]: "上传或修改文档",
  [PERMISSIONS.documentDelete]: "删除文档",
  [PERMISSIONS.chunkRead]: "查看切片内容",
  [PERMISSIONS.jobRead]: "查看作业状态",
  [PERMISSIONS.jobCancel]: "取消作业",
  [PERMISSIONS.queryRun]: "执行检索调试",
  [PERMISSIONS.chatRun]: "发起带引用的问答",
  [PERMISSIONS.lineageRead]: "查看血缘与版本",
  [PERMISSIONS.indexRebuild]: "重建索引",
  [PERMISSIONS.workspaceManageMembers]: "管理成员",
  [PERMISSIONS.workspaceManageRoles]: "调整角色权限",
  [PERMISSIONS.platformManageRoles]: "管理平台角色目录",
};

/**
 * 判权：`granted` 是 `/auth/me` 原样返回的字符串数组。
 *
 * 空数组、`undefined` 元素、未知字符串一律按"没有权限"处理；不抛错——
 * 权限数据脏了应该少显示入口，而不是白屏。
 */
export function hasPermission(granted: readonly string[], permission: string): boolean {
  if (permission.length === 0) return false;
  return granted.includes(permission);
}

/** 任一命中即为真（用于"至少要有一个"的场景，例如知识库入口）。 */
export function hasAnyPermission(
  granted: readonly string[],
  permissions: readonly string[],
): boolean {
  return permissions.some((permission) => hasPermission(granted, permission));
}

/** 缺失的权限点清单；403 时用来告诉用户"缺的是哪一条"。 */
export function missingPermissions(
  granted: readonly string[],
  required: readonly string[],
): string[] {
  return required.filter((permission) => !hasPermission(granted, permission));
}

/** 权限点的中文说明；未知权限点原样返回（便于发现拼写错误，而不是显示"未知权限"）。 */
export function describePermission(permission: string): string {
  const label = PERMISSION_LABELS[permission as Permission];
  return label ?? permission;
}

/** 把任意来源的权限列表收敛成只含字符串的数组（脏数据直接丢弃，不猜补）。 */
export function normalizePermissions(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.filter((item): item is string => typeof item === "string");
}
