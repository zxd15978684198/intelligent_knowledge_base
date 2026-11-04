import { isApiError, type ApiError } from "@/lib/api/client";
import { OWNER_ROLE } from "./roles";

/**
 * 把成员接口的错误翻成人话。
 *
 * 三条与直觉不同的规则（都以 `src/pyknora/api/v1/workspaces.py` 为准）：
 * 1. **改角色/删成员的越权是 404**，不是 403；所以"记录不存在"与"没有权限"必须一起说，
 *    不能写成"没有权限"——那等于替后端确认了那行存在。
 * 2. 同一个 422 下面藏着四种原因（设 owner、改 owner、未知角色、删 owner），后端用
 *    `details` 区分了大部分，只有"改 owner 本人角色"这条**没有 details**，只能按 action 说。
 * 3. 409 只出现在"已经是成员"与"转移所有权撞并发"两处，提示是行动建议而不是报错堆。
 *
 * `ACTION_LABEL` 让每条提示都能说清"哪个操作失败了"。
 */

export type MemberErrorAction =
  | "load"
  | "add"
  | "update-role"
  | "remove"
  | "roles"
  | "permissions"
  | "transfer";

const ACTION_LABEL: Readonly<Record<MemberErrorAction, string>> = {
  load: "加载成员列表",
  add: "添加成员",
  "update-role": "修改成员角色",
  remove: "移除成员",
  roles: "读取角色目录",
  permissions: "读取我的权限",
  transfer: "转移所有权",
};

/** 权限点常量在 `lib/auth` 里；这里只做文案，不重复定义字符串字面量。 */
const MANAGE_MEMBERS_HINT = "需要「管理成员」权限（workspace.manage_members）。";

/** 后端 `error.code`；非 ApiError 一律 null。 */
export function memberErrorCode(error: unknown): string | null {
  return isApiError(error) ? error.code : null;
}

export function isForbidden(error: unknown): boolean {
  return isApiError(error) && (error.status === 403 || error.code === "FORBIDDEN");
}

export function isNotFound(error: unknown): boolean {
  return isApiError(error) && (error.status === 404 || error.code === "RESOURCE_NOT_FOUND");
}

export function isConflict(error: unknown): boolean {
  return isApiError(error) && (error.status === 409 || error.code === "CONFLICT");
}

export function isInvalidRequest(error: unknown): boolean {
  return (
    isApiError(error) &&
    (error.code === "INVALID_REQUEST" || error.code === "UNSUPPORTED_FORMAT")
  );
}

export function isUnauthenticated(error: unknown): boolean {
  return isApiError(error) && (error.status === 401 || error.code === "UNAUTHENTICATED");
}

function detailValue(details: unknown, key: string): string | null {
  if (typeof details !== "object" || details === null) return null;
  const value = (details as Record<string, unknown>)[key];
  return typeof value === "string" ? value : null;
}

function withRequestId(message: string, error: ApiError): string {
  return error.requestId ? `${message}（请求标识：${error.requestId}）` : message;
}

function forbiddenMessage(action: MemberErrorAction): string {
  if (action === "transfer") {
    return `只有当前所有者可以转移所有权。${MANAGE_MEMBERS_HINT}`;
  }
  if (action === "roles") {
    return `读不到角色目录：${MANAGE_MEMBERS_HINT}`;
  }
  if (action === "add") {
    return `添加成员失败：${MANAGE_MEMBERS_HINT}`;
  }
  if (action === "load") {
    // GET .../members 直接带 require_permission，所以列表的 403 就是缺这条权限。
    return `看不到成员列表：${MANAGE_MEMBERS_HINT}`;
  }
  if (action === "permissions") {
    return `读不到你的权限点：${MANAGE_MEMBERS_HINT}`;
  }
  return `${ACTION_LABEL[action]}失败：${MANAGE_MEMBERS_HINT}`;
}

function notFoundMessage(action: MemberErrorAction): string {
  switch (action) {
    case "load":
      return "看不到这个工作区的成员：工作区不存在，或者你已不在其中。";
    case "add":
      // POST 的 404 是 principal 不存在或已停用；后端刻意不区分。
      return "没有找到这个 user_id 对应的可用账号（不存在或已停用）。";
    case "update-role":
      // PATCH 的 404 同时覆盖"无管理权限"与"记录不存在"。
      return "改不动这条成员记录：它不存在，或者你没有「管理成员」权限。";
    case "remove":
      return "移不掉这条成员记录：它不存在，或者你没有权限移除它（只能移除自己，或由管理员移除）。";
    case "transfer":
      return "工作区不存在，或者目标成员不在本工作区里。";
    default:
      return "工作区不存在，或者当前身份已失效。";
  }
}

function conflictMessage(error: ApiError, action: MemberErrorAction): string {
  if (action === "add") {
    return "这个账号已经是本工作区成员，不需要重复添加。";
  }
  if (action === "transfer") {
    return withRequestId("所有权刚刚被别处改掉了，请刷新后再决定是否转移。", error);
  }
  return withRequestId("这条成员记录刚被其他人改过，请刷新后重试。", error);
}

function invalidRequestMessage(error: ApiError, action: MemberErrorAction): string {
  const endpoint = detailValue(error.details, "endpoint");
  if (endpoint !== null) {
    return "不能把成员角色设成 owner：所有者只能通过「转移所有权」产生。";
  }

  const roleKey = detailValue(error.details, "role_key") ?? detailValue(error.details, "role");
  if (roleKey !== null) {
    if (roleKey === OWNER_ROLE) {
      return "不能把成员角色设成 owner：所有者只能通过「转移所有权」产生。";
    }
    return `未知的角色「${roleKey}」，请刷新角色目录后重试。`;
  }

  if (detailValue(error.details, "membership_id") !== null) {
    return "所有者成员不能被移除；换所有者请用「转移所有权」（原所有者会降为 admin）。";
  }

  if (detailValue(error.details, "principal_id") !== null) {
    return "目标无效：不能转移给自己，且目标必须是本工作区的成员。";
  }

  // 只剩一种没有 details 的 422：改 owner 本人的角色（后端 "owner role cannot be changed here"）。
  if (action === "update-role") {
    return "所有者成员的角色不能在这里改；换所有者请用「转移所有权」，其余角色请选择非 owner。";
  }
  if (action === "remove") {
    return "所有者成员不能被移除；换所有者请用「转移所有权」。";
  }
  return `${ACTION_LABEL[action]}失败：请求参数不被接受。`;
}

/**
 * 主入口：把任意错误翻成一句可以直接放进 `role="alert"` 的中文。
 *
 * 顺序是 code 优先、status 兜底：后端把语义写在 `code` 里，`status` 只是它的投影。
 */
export function describeMemberError(error: unknown, action: MemberErrorAction = "load"): string {
  const label = ACTION_LABEL[action];

  if (!isApiError(error)) {
    return `${label}失败：网络异常，请检查连接后重试。`;
  }

  if (isUnauthenticated(error)) {
    return "登录状态已过期，请重新登录后再试。";
  }
  if (isForbidden(error)) {
    return forbiddenMessage(action);
  }
  if (isNotFound(error)) {
    return notFoundMessage(action);
  }
  if (isConflict(error)) {
    return conflictMessage(error, action);
  }
  if (isInvalidRequest(error)) {
    return invalidRequestMessage(error, action);
  }
  if (error.status >= 500) {
    return withRequestId(`${label}失败：服务端暂时不可用，请稍后重试。`, error);
  }
  return `${label}失败：HTTP ${error.status}，${error.code}。`;
}
