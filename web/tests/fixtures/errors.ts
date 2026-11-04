/**
 * 错误信封夹具（N2）。
 *
 * 覆盖后端 17 个错误码里"前端真的要分流"的几种：
 * - 401 未登录（跳登录页）
 * - 403 无工作区权限（不是登录问题，别跳登录页）
 * - 409 乐观锁冲突（提示刷新后重试）
 * - 409 幂等冲突（同一个 Idempotency-Key 传了不同内容）
 * - 422 校验失败（`details.errors` 要能定位到字段）
 * - 428 缺 If-Match（前端 bug：写操作忘了带版本号）
 * - 503 依赖不可用（可重试）
 */

import type { ErrorEnvelope } from "@/lib/contracts";

import { errorEnvelopeBody, TEST_REQUEST_ID } from "../msw/handlers";

/** 401：token 缺失/过期。 */
export const UNAUTHENTICATED_ENVELOPE: ErrorEnvelope = errorEnvelopeBody({
  code: "UNAUTHENTICATED",
  message: "缺少或无法解析访问令牌",
});

/** 403：令牌有效但不在该工作区。与 401 的处理完全不同。 */
export const FORBIDDEN_ENVELOPE: ErrorEnvelope = errorEnvelopeBody({
  code: "FORBIDDEN",
  message: "当前身份不是该工作区成员",
});

/** 409：`If-Match` 给的版本不是当前版本。 */
export const REVISION_CONFLICT_ENVELOPE: ErrorEnvelope = errorEnvelopeBody({
  code: "REVISION_CONFLICT",
  message: "资源已被修改，请刷新后重试",
  details: { expected: 5, actual: 4 },
});

/** 409：同一个幂等键配了不同的请求体。 */
export const IDEMPOTENCY_CONFLICT_ENVELOPE: ErrorEnvelope = errorEnvelopeBody({
  code: "IDEMPOTENCY_CONFLICT",
  message: "该 Idempotency-Key 已用于不同的请求内容",
});

/** 428：写操作没带 `If-Match`。 */
export const REVISION_MISSING_ENVELOPE: ErrorEnvelope = errorEnvelopeBody({
  code: "REVISION_MISSING",
  message: "If-Match header is required",
});

/**
 * 422：`main.py` 把 `RequestValidationError` 也折成信封，
 * `details` 是 `{"errors":[{loc,type,msg}]}`（不是 pydantic 默认的 `detail` 数组）。
 */
export const VALIDATION_ENVELOPE: ErrorEnvelope = errorEnvelopeBody({
  code: "INVALID_REQUEST",
  message: "请求参数不合法",
  details: {
    errors: [{ loc: ["body", "top_k"], type: "less_than_equal", msg: "Input should be less than or equal to 50" }],
  },
});

/** 503：embedder 没配、或数据库连不上。`retryable=true` 才值得让 Query 层重试。 */
export const DEPENDENCY_UNAVAILABLE_ENVELOPE: ErrorEnvelope = errorEnvelopeBody({
  code: "DEPENDENCY_UNAVAILABLE",
  message: "向量检索后端不可用",
  retryable: true,
});

/** 500：兜底分支，`retryable=true`。 */
export const INTERNAL_ERROR_ENVELOPE: ErrorEnvelope = errorEnvelopeBody({
  code: "INTERNAL_ERROR",
  message: "服务内部错误",
  retryable: true,
});

export { TEST_REQUEST_ID };

/** 反代吐 HTML（不是信封）——用来验证 `apiRequest` 会降级成 `INVALID_RESPONSE`。 */
export const NON_ENVELOPE_BODY = "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>";

/** 少了外层 `error` 字段：形状像信封但不合法。 */
export const MALFORMED_ENVELOPE = { request_id: TEST_REQUEST_ID, code: "FORBIDDEN", message: "越权" };
