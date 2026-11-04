import type { RolePermissionView } from "@/features/members/api";
import type { MembershipDetail } from "@/lib/contracts";

/**
 * 成员用例的 fixture。
 *
 * 字段必须与后端 `src/pyknora/api/v1/workspaces.py` 的 pydantic 模型一致：
 * `MembershipDetail` 只有四个字段，**没有** username/email/created_at。这里照实伪造，
 * 免得用例替后端"想象"出多余字段，把真实缺口盖住。
 */

/** 用例统一使用的三个 UUID：工作区、owner、另一名成员。 */
export const TEST_WORKSPACE_ID = "22222222-2222-4222-8222-222222222222";
export const TEST_OWNER_ID = "11111111-1111-4111-8111-111111111111";
export const TEST_MEMBER_ID = "33333333-3333-4333-8333-333333333333";
export const TEST_OWNER_MEMBERSHIP_ID = "44444444-4444-4444-8444-444444444444";
export const TEST_MEMBER_MEMBERSHIP_ID = "55555555-5555-4555-8555-555555555555";

export function makeMembership(overrides: Partial<MembershipDetail> = {}): MembershipDetail {
  return {
    membership_id: TEST_OWNER_MEMBERSHIP_ID,
    workspace_id: TEST_WORKSPACE_ID,
    user_id: TEST_OWNER_ID,
    role: "owner",
    ...overrides,
  };
}

/** 一份典型的两成员列表：owner + member。 */
export function makeMemberPage(): { members: MembershipDetail[] } {
  return {
    members: [
      makeMembership(),
      makeMembership({
        membership_id: TEST_MEMBER_MEMBERSHIP_ID,
        user_id: TEST_MEMBER_ID,
        role: "member",
      }),
    ],
  };
}

/** `GET .../roles` 的真实形态：键名是 `role_key`，owner 也在目录里（不能被下拉选中而已）。 */
export function makeRoles(): RolePermissionView[] {
  return [
    { role_key: "admin", permissions: ["workspace.manage_members", "workspace.manage_roles"] },
    { role_key: "guest", permissions: ["knowledge_base.read"] },
    { role_key: "member", permissions: ["knowledge_base.read", "document.write"] },
    { role_key: "owner", permissions: ["workspace.manage_members", "workspace.manage_roles"] },
  ];
}

/**
 * 后端统一错误信封：`{"error":{code,message,retryable,details},"request_id":...}`。
 *
 * 比 kbs 的版本多一个 `details`：成员接口的 422/409 靠 `details` 区分原因
 * （`{role_key}` / `{user_id}` / `{membership_id}` / `{principal_id}` / `{endpoint}`），
 * 少了它就只能测到"某个 422"。
 */
export function errorEnvelope(
  code: string,
  message = "test error",
  details: unknown = null,
  requestId = "req-test-0001",
): {
  error: { code: string; message: string; retryable: boolean; details: unknown };
  request_id: string;
} {
  return {
    error: { code, message, retryable: false, details },
    request_id: requestId,
  };
}
