/**
 * `identity` 的只读窄化。
 *
 * `Identity` 的类型由 N5 定稿（`@/features/auth/auth-context`），并行开发期间字段名
 * 可能是 `display_name`（后端口径）或 `displayName`（前端习惯）之一。这里把 identity
 * 当 `unknown` 处理，按候选字段名依次读取，读不到就走可读回落——既不假设 N5 的最终
 * 形状，也绝不让 `any` 泄进组件。
 */

type UnknownRecord = Record<string, unknown>;

function toRecord(value: unknown): UnknownRecord | null {
  return typeof value === "object" && value !== null ? (value as UnknownRecord) : null;
}

function readString(source: UnknownRecord | null, ...keys: readonly string[]): string | null {
  if (source === null) return null;
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}

export type IdentityView = {
  /** 一定是可展示的字符串：缺字段时回落"当前用户"，不留空白。 */
  readonly displayName: string;
  /** 当前工作区 id；`null` 表示身份里没有这个字段（或尚未登录）。 */
  readonly workspaceId: string | null;
};

/**
 * 从 `useAuth().identity` 取出壳层需要的两项信息。
 *
 * 工作区优先取 `default_workspace_id`（DAG 约定：登录令牌签发时后端一起回的那个），
 * 再回落 `workspace_id`（`/auth/me` 的字段名）。两者都没有时返回 `null`，由调用方
 * 显示"未绑定工作区"——不猜、不用空串糊过去。
 */
export function readIdentityView(identity: unknown): IdentityView {
  const record = toRecord(identity);
  return {
    displayName: readString(record, "display_name", "displayName", "username") ?? "当前用户",
    workspaceId: readString(record, "default_workspace_id", "workspace_id"),
  };
}
