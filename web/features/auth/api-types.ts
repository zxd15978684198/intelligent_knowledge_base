/**
 * 认证接口的**线格式**（wire types），逐字对应 `src/pyknora/api/v1/auth.py`。
 *
 * 为什么手写而不是从 OpenAPI 生成：N2 的 `lib/contracts/openapi.d.ts` 覆盖业务面，
 * 认证这两条接口的形状在这里只需要四个字段，且注释要写清"后端没有返回什么"
 * （例如 `/auth/me` 不返回 username），生成类型会把这类事实丢掉。
 */

/** `POST /api/v1/auth/login` 与 `POST /api/v1/auth/register` 的 200/201 响应。 */
export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  principal_id: string;
  workspace_id: string;
  display_name: string | null;
}

/**
 * `GET /api/v1/auth/me` 的响应。
 *
 * 注意**没有** `username`、**没有** `email`、**没有** `default_workspace_id`：
 * 登录名由前端在登录时缓存（见 `lib/auth/identity-cache.ts`），
 * `default_workspace_id` 映射自这里的 `workspace_id`。
 */
export interface MeResponse {
  principal_id: string;
  display_name: string | null;
  workspace_id: string;
  role: string;
  permissions: string[];
}
