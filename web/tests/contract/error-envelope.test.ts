import { describe, expect, it } from "vitest";

import { ERROR_CODES, errorEnvelopeSchema, isErrorCode, safeParseErrorEnvelope } from "@/lib/contracts";

import {
  DEPENDENCY_UNAVAILABLE_ENVELOPE,
  FORBIDDEN_ENVELOPE,
  IDEMPOTENCY_CONFLICT_ENVELOPE,
  MALFORMED_ENVELOPE,
  NON_ENVELOPE_BODY,
  REVISION_CONFLICT_ENVELOPE,
  REVISION_MISSING_ENVELOPE,
  TEST_REQUEST_ID,
  UNAUTHENTICATED_ENVELOPE,
  VALIDATION_ENVELOPE,
} from "../fixtures/errors";

/**
 * 错误信封是 UI 分流的唯一依据（401 跳登录、403 提示无权限、409 提示刷新、428 是前端 bug），
 * 所以这里的用例围绕"什么形状算合法"和"非法形状必须被挡住"，而不是逐字段复述。
 */
describe("errorEnvelopeSchema：合法信封", () => {
  it("17 个错误码全部能解析（闭集不参与解析，但每个码都必须是合法输入）", () => {
    expect(ERROR_CODES).toHaveLength(17);

    for (const code of ERROR_CODES) {
      const parsed = errorEnvelopeSchema.safeParse({
        error: { code, message: `${code} 的说明`, retryable: false, details: null },
        request_id: TEST_REQUEST_ID,
      });
      expect(parsed.success, `错误码 ${code} 应当可解析`).toBe(true);
      if (parsed.success) {
        expect(parsed.data.error.code).toBe(code);
      }
    }
  });

  it("四种真实响应形状都能解析（401 / 403 / 409 / 422）", () => {
    for (const envelope of [
      UNAUTHENTICATED_ENVELOPE,
      FORBIDDEN_ENVELOPE,
      REVISION_CONFLICT_ENVELOPE,
      VALIDATION_ENVELOPE,
      REVISION_MISSING_ENVELOPE,
      IDEMPOTENCY_CONFLICT_ENVELOPE,
      DEPENDENCY_UNAVAILABLE_ENVELOPE,
    ]) {
      const parsed = errorEnvelopeSchema.safeParse(envelope);
      expect(parsed.success, `信封 ${envelope.error.code} 应当可解析`).toBe(true);
    }
  });

  it("422 的 details.errors 能定位到字段", () => {
    const parsed = errorEnvelopeSchema.parse(VALIDATION_ENVELOPE);
    expect(parsed.error.code).toBe("INVALID_REQUEST");
    // details 是 unknown：使用方按 code 决定怎么读（422 时是 {errors:[{loc,type,msg}]}）。
    expect(parsed.error.details).toEqual({
      errors: [{ loc: ["body", "top_k"], type: "less_than_equal", msg: "Input should be less than or equal to 50" }],
    });
  });

  it("retryable 缺失时默认 false，request_id 缺失时默认 null", () => {
    const parsed = errorEnvelopeSchema.parse({ error: { code: "FORBIDDEN", message: "越权" } });
    expect(parsed.error.retryable).toBe(false);
    expect(parsed.request_id).toBeNull();
  });

  it("request_id 允许显式 null", () => {
    const parsed = errorEnvelopeSchema.parse({
      error: { code: "INTERNAL_ERROR", message: "boom", retryable: true, details: null },
      request_id: null,
    });
    expect(parsed.request_id).toBeNull();
  });

  it("error 里多出的字段被忽略（后端加字段不该让前端解析失败）", () => {
    const parsed = errorEnvelopeSchema.safeParse({
      error: { code: "CONFLICT", message: "冲突", retryable: false, details: null, trace_id: "t-1" },
      request_id: TEST_REQUEST_ID,
    });
    expect(parsed.success).toBe(true);
  });

  it("顶层多出的字段被忽略", () => {
    const parsed = errorEnvelopeSchema.safeParse({
      error: { code: "CONFLICT", message: "冲突" },
      request_id: TEST_REQUEST_ID,
      version: 2,
    });
    expect(parsed.success).toBe(true);
  });
});

describe("errorEnvelopeSchema：非法形状必须被挡住", () => {
  it("空 code 不合法", () => {
    expect(errorEnvelopeSchema.safeParse({ error: { code: "", message: "x" } }).success).toBe(false);
  });

  it("缺 error 外层不合法", () => {
    expect(errorEnvelopeSchema.safeParse(MALFORMED_ENVELOPE).success).toBe(false);
  });

  it("retryable 类型错了不合法", () => {
    expect(
      errorEnvelopeSchema.safeParse({ error: { code: "FORBIDDEN", message: "x", retryable: "yes" } }).success,
    ).toBe(false);
  });

  it("message 缺失不合法", () => {
    expect(errorEnvelopeSchema.safeParse({ error: { code: "FORBIDDEN" } }).success).toBe(false);
  });

  it("错误路径指向 error.code，排障时能直接看到是哪一层", () => {
    const parsed = errorEnvelopeSchema.safeParse({ error: { code: "", message: "x" } });
    expect(parsed.success).toBe(false);
    if (parsed.success) {
      throw new Error("期望解析失败");
    }
    expect(parsed.error.issues.some((issue) => issue.path.join(".") === "error.code")).toBe(true);
  });
});

describe("safeParseErrorEnvelope：网络层降级", () => {
  it("反代返回的 HTML 不抛错，返回 null", () => {
    expect(safeParseErrorEnvelope(NON_ENVELOPE_BODY)).toBeNull();
  });

  it("null / undefined / 数组 都安全返回 null", () => {
    expect(safeParseErrorEnvelope(null)).toBeNull();
    expect(safeParseErrorEnvelope(undefined)).toBeNull();
    expect(safeParseErrorEnvelope([{ error: {} }])).toBeNull();
  });

  it("合法信封原样返回", () => {
    const parsed = safeParseErrorEnvelope(FORBIDDEN_ENVELOPE);
    expect(parsed?.error.code).toBe("FORBIDDEN");
    expect(parsed?.request_id).toBe(TEST_REQUEST_ID);
  });
});

describe("isErrorCode：闭集只做文案映射", () => {
  it("已知码返回 true", () => {
    expect(isErrorCode("REVISION_CONFLICT")).toBe(true);
    expect(isErrorCode("DEPENDENCY_UNAVAILABLE")).toBe(true);
  });

  it("后端将来新增的码返回 false（不抛错，走通用文案）", () => {
    expect(isErrorCode("SOMETHING_NEW")).toBe(false);
    expect(isErrorCode("")).toBe(false);
  });
});
