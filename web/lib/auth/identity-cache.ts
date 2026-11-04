import { readStored, removeStored, writeStored } from "./storage";

/**
 * 登录名缓存。
 *
 * 为什么需要它：`GET /api/v1/auth/me` 的真实响应是
 * `{principal_id, display_name, workspace_id, role, permissions}`——
 * **不含 username**（见 `src/pyknora/api/v1/auth.py` 的 `MeResponse`），
 * 而 `Identity.username` 是前端自己的契约。刷新页面后只剩令牌，
 * 登录名只能靠登录/注册那一刻记下来。
 *
 * 这里只存登录名，不存邮箱、不存显示名——`display_name` 每次由 `/auth/me` 现取。
 */

/** localStorage 键名。 */
export const USERNAME_STORAGE_KEY = "pyknora.username";

export function readCachedUsername(): string | null {
  const value = readStored(USERNAME_STORAGE_KEY);
  if (value === null) return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

export function cacheUsername(username: string): void {
  const trimmed = username.trim();
  if (trimmed.length === 0) {
    clearCachedUsername();
    return;
  }
  writeStored(USERNAME_STORAGE_KEY, trimmed);
}

export function clearCachedUsername(): void {
  removeStored(USERNAME_STORAGE_KEY);
}
