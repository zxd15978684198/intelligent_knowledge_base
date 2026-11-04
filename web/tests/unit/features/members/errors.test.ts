import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import {
  describeMemberError,
  isConflict,
  isForbidden,
  isInvalidRequest,
  isNotFound,
  memberErrorCode,
} from "@/features/members/errors";

/**
 * 错误码 → 中文的用例。
 *
 * 断言的是"用户最终看到哪句话"，所以每个用例都要求包含**关键区分词**，而不是整句相等：
 * 文案可以打磨，但"403 是权限、404 是记录或没权限、422 里 owner 要单独说"不能被润色掉。
 * 这些都是后端真分支（`src/pyknora/api/v1/workspaces.py`），不是想象出来的。
 */

const REQUEST_ID = "req-test-0001";

function apiError(
  status: number,
  code: string,
  details: unknown = null,
  requestId: string | null = REQUEST_ID,
): ApiError {
  return new ApiError({
    status,
    code,
    message: "backend message",
    details,
    requestId,
  });
}

describe("features/members/errors", () => {
  it("403 FORBIDDEN：说清缺的是「管理成员」权限，而不是复述后端英文", () => {
    const message = describeMemberError(apiError(403, "FORBIDDEN"), "load");

    expect(message).toContain("管理成员");
    expect(message).toContain("workspace.manage_members");
    expect(message).not.toContain("backend message");
  });

  it("404 RESOURCE_NOT_FOUND：改角色时同时说「记录不存在」与「没有权限」（后端回 404 不是 403）", () => {
    const message = describeMemberError(apiError(404, "RESOURCE_NOT_FOUND"), "update-role");

    expect(message).toContain("不存在");
    expect(message).toContain("管理成员");
    // 后端刻意不区分这两者，提示也不能替它区分。
    expect(message).not.toContain("已被删除");
  });

  it("404 出现在加成员时：是 principal 不存在或已停用，不是权限问题", () => {
    const message = describeMemberError(apiError(404, "RESOURCE_NOT_FOUND"), "add");

    expect(message).toContain("user_id");
    expect(message).toContain("停用");
    expect(message).not.toContain("管理成员");
  });

  it("409 CONFLICT：加成员时提示「已经是成员」", () => {
    const message = describeMemberError(
      apiError(409, "CONFLICT", { user_id: "33333333-3333-4333-8333-333333333333" }),
      "add",
    );

    expect(message).toContain("已经是本工作区成员");
  });

  it("409 出现在转移所有权时：提示撞并发，并带上请求标识", () => {
    const message = describeMemberError(apiError(409, "CONFLICT"), "transfer");

    expect(message).toContain("所有权");
    expect(message).toContain(REQUEST_ID);
  });

  it("422 带 {endpoint}：这是「想把角色设成 owner」，要指向转移所有权", () => {
    const message = describeMemberError(
      apiError(422, "INVALID_REQUEST", {
        endpoint: "PUT /api/v1/workspaces/22222222-2222-4222-8222-222222222222/owner",
      }),
      "add",
    );

    expect(message).toContain("owner");
    expect(message).toContain("转移所有权");
  });

  it("422 带 {role_key}：未知角色要回显角色名，而不是「参数错误」", () => {
    const message = describeMemberError(
      apiError(422, "INVALID_REQUEST", { role_key: "superuser" }),
      "add",
    );

    expect(message).toContain("superuser");
    expect(message).toContain("角色");
  });

  it("422 带 {membership_id}：owner 成员不能被删除，要写清正确路径", () => {
    const message = describeMemberError(
      apiError(422, "INVALID_REQUEST", { membership_id: "44444444-4444-4444-8444-444444444444" }),
      "remove",
    );

    expect(message).toContain("所有者");
    expect(message).toContain("转移所有权");
  });

  it("422 没有 details（改 owner 本人角色）：按 action 说不清的那条，也不能吞掉", () => {
    const message = describeMemberError(apiError(422, "INVALID_REQUEST"), "update-role");

    expect(message).toContain("所有者");
    expect(message).toContain("转移所有权");
  });

  it("5xx：带请求标识，方便用户截图后在日志里定位", () => {
    const message = describeMemberError(apiError(503, "DEPENDENCY_UNAVAILABLE"), "load");

    expect(message).toContain("服务端暂时不可用");
    expect(message).toContain(REQUEST_ID);
  });

  it("401：引导重新登录，而不是显示「加载失败」", () => {
    const message = describeMemberError(apiError(401, "UNAUTHENTICATED"), "load");

    expect(message).toContain("登录");
    expect(message).toContain("过期");
  });

  it("非 ApiError：统一说网络异常，不泄漏 error.message", () => {
    const message = describeMemberError(new Error("Failed to fetch"), "add");

    expect(message).toContain("网络异常");
    expect(message).not.toContain("Failed to fetch");
  });

  it("判别函数与 memberErrorCode 直接对 code/status 生效", () => {
    const notFound = apiError(404, "RESOURCE_NOT_FOUND");
    const conflict = apiError(409, "CONFLICT");

    expect(isNotFound(notFound)).toBe(true);
    expect(isConflict(conflict)).toBe(true);
    expect(isForbidden(apiError(403, "FORBIDDEN"))).toBe(true);
    expect(isInvalidRequest(apiError(422, "INVALID_REQUEST"))).toBe(true);
    expect(isNotFound(conflict)).toBe(false);
    expect(memberErrorCode(notFound)).toBe("RESOURCE_NOT_FOUND");
    expect(memberErrorCode(new Error("boom"))).toBeNull();
  });
});
