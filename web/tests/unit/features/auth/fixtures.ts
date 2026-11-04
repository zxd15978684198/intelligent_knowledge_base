/**
 * 认证用例共用的固定数据。
 *
 * 数值与 `src/pyknora/api/v1/auth.py` 的真实响应形状一致（UUID 是合法格式，字段名逐字对齐）。
 * 不放 handlers：各用例需要不同的失败注入，handler 一律在用例里 `server.use` 局部声明。
 */

export const PRINCIPAL_ID = "11111111-1111-4111-8111-111111111111";
export const WORKSPACE_ID = "22222222-2222-4222-8222-222222222222";
export const ACCESS_TOKEN = "tok-test-1";

/** `POST /auth/login` 与 `POST /auth/register` 的 200/201 响应（`TokenResponse`）。 */
export const TOKEN_RESPONSE = {
  access_token: ACCESS_TOKEN,
  token_type: "bearer",
  expires_in: 3600,
  principal_id: PRINCIPAL_ID,
  workspace_id: WORKSPACE_ID,
  display_name: "Alice",
};

/** `GET /auth/me` 的响应（`MeResponse`）——注意没有 username。 */
export const ME_RESPONSE = {
  principal_id: PRINCIPAL_ID,
  display_name: "Alice",
  workspace_id: WORKSPACE_ID,
  role: "editor",
  permissions: ["document.read", "document.write"],
};

/** 401 错误信封（后端 `UNAUTHENTICATED`）。 */
export const UNAUTHENTICATED_BODY = {
  error: {
    code: "UNAUTHENTICATED",
    message: "invalid credentials",
    retryable: false,
    details: {},
  },
  request_id: "req-test-401",
};

/** 409 错误信封（后端注册撞名 `CONFLICT`）。 */
export const CONFLICT_BODY = {
  error: {
    code: "CONFLICT",
    message: "account already exists",
    retryable: false,
    details: {},
  },
  request_id: "req-test-409",
};
