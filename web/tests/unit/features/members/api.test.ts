import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import { server } from "@/tests/msw/server";
import {
  addMember,
  listMembers,
  listRoles,
  removeMember,
  transferOwnership,
  updateMemberRole,
} from "@/features/members/api";
import {
  errorEnvelope,
  makeMemberPage,
  makeMembership,
  makeRoles,
  TEST_MEMBER_ID,
  TEST_MEMBER_MEMBERSHIP_ID,
  TEST_WORKSPACE_ID,
} from "./fixtures";

/**
 * 接口层用例：把"发什么请求、带什么头、方法是什么"钉死。
 *
 * 这里断言的是**后端真代码**的口径（`src/pyknora/api/v1/workspaces.py`），不是 REST 习惯：
 * 改角色是 PATCH、转移所有权体键名是 `owner_principal_id`、204 没有响应体。
 * MSW 以 `onUnhandledRequest: "error"` 启动，路径写错会让用例直接失败。
 */

const AUTH = { token: "test-token" };

describe("features/members/api", () => {
  it("listMembers 打 GET /workspaces/{id}/members，带 Bearer 与 X-Workspace-ID，原样返回 members", async () => {
    const seen: { url: string; auth: string | null; workspace: string | null }[] = [];
    server.use(
      http.get("*/api/v1/workspaces/:wsId/members", ({ request }) => {
        seen.push({
          url: request.url,
          auth: request.headers.get("Authorization"),
          workspace: request.headers.get("X-Workspace-ID"),
        });
        return HttpResponse.json(makeMemberPage());
      }),
    );

    const page = await listMembers(TEST_WORKSPACE_ID, AUTH);

    expect(seen).toHaveLength(1);
    expect(seen[0]?.url).toContain(`/api/v1/workspaces/${TEST_WORKSPACE_ID}/members`);
    expect(seen[0]?.auth).toBe("Bearer test-token");
    expect(seen[0]?.workspace).toBe(TEST_WORKSPACE_ID);
    expect(page.members).toHaveLength(2);
    // 列表只有四个字段：没有 username/email，界面才不可能"顺手"显示人名。
    expect(Object.keys(page.members[0] ?? {}).sort()).toEqual([
      "membership_id",
      "role",
      "user_id",
      "workspace_id",
    ]);
  });

  it("addMember 以 POST 提交 {user_id, role} 并接住 201", async () => {
    let body: unknown = null;
    let method = "";
    server.use(
      http.post("*/api/v1/workspaces/:wsId/members", async ({ request }) => {
        method = request.method;
        body = await request.json();
        return HttpResponse.json(
          makeMembership({
            membership_id: TEST_MEMBER_MEMBERSHIP_ID,
            user_id: TEST_MEMBER_ID,
            role: "member",
          }),
          { status: 201 },
        );
      }),
    );

    const created = await addMember(
      TEST_WORKSPACE_ID,
      { user_id: TEST_MEMBER_ID, role: "member" },
      AUTH,
    );

    expect(method).toBe("POST");
    expect(body).toEqual({ user_id: TEST_MEMBER_ID, role: "member" });
    expect(created.membership_id).toBe(TEST_MEMBER_MEMBERSHIP_ID);
    expect(created.role).toBe("member");
  });

  it("updateMemberRole 用 PATCH（不是 PUT），体里只有 role", async () => {
    let method = "";
    let body: unknown = null;
    server.use(
      http.patch("*/api/v1/workspaces/:wsId/members/:membershipId", async ({ request }) => {
        method = request.method;
        body = await request.json();
        return HttpResponse.json(
          makeMembership({
            membership_id: TEST_MEMBER_MEMBERSHIP_ID,
            user_id: TEST_MEMBER_ID,
            role: "guest",
          }),
        );
      }),
    );

    const updated = await updateMemberRole(
      TEST_WORKSPACE_ID,
      TEST_MEMBER_MEMBERSHIP_ID,
      "guest",
      AUTH,
    );

    expect(method).toBe("PATCH");
    expect(body).toEqual({ role: "guest" });
    expect(updated.role).toBe("guest");
  });

  it("removeMember 打 DELETE，204 时 resolve 成 undefined", async () => {
    let method = "";
    let path = "";
    server.use(
      http.delete("*/api/v1/workspaces/:wsId/members/:membershipId", ({ request }) => {
        method = request.method;
        path = new URL(request.url).pathname;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const result = await removeMember(TEST_WORKSPACE_ID, TEST_MEMBER_MEMBERSHIP_ID, AUTH);

    expect(method).toBe("DELETE");
    expect(path).toBe(
      `/api/v1/workspaces/${TEST_WORKSPACE_ID}/members/${TEST_MEMBER_MEMBERSHIP_ID}`,
    );
    expect(result).toBeUndefined();
  });

  it("transferOwnership 用 PUT /owner，体键名是 owner_principal_id（不是 user_id）", async () => {
    let method = "";
    let body: unknown = null;
    server.use(
      http.put("*/api/v1/workspaces/:wsId/owner", async ({ request }) => {
        method = request.method;
        body = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );

    await transferOwnership(TEST_WORKSPACE_ID, TEST_MEMBER_ID, AUTH);

    expect(method).toBe("PUT");
    expect(body).toEqual({ owner_principal_id: TEST_MEMBER_ID });
    expect(body).not.toHaveProperty("user_id");
  });

  it("listRoles 打 GET /roles，接住 role_key（不是 role）", async () => {
    server.use(
      http.get("*/api/v1/workspaces/:wsId/roles", () =>
        HttpResponse.json({ roles: makeRoles() }),
      ),
    );

    const catalog = await listRoles(TEST_WORKSPACE_ID, AUTH);

    expect(catalog.roles.map((role) => role.role_key)).toContain("admin");
    expect(catalog.roles.find((role) => role.role_key === "admin")?.permissions).toContain(
      "workspace.manage_members",
    );
  });

  it("错误信封被翻成 ApiError：code 与 details 都保留（422 靠 details 区分原因）", async () => {
    server.use(
      http.post("*/api/v1/workspaces/:wsId/members", () =>
        HttpResponse.json(
          errorEnvelope("INVALID_REQUEST", "unknown role", { role_key: "superuser" }),
          { status: 422 },
        ),
      ),
    );

    const failure = await addMember(
      TEST_WORKSPACE_ID,
      { user_id: TEST_MEMBER_ID, role: "superuser" },
      AUTH,
    ).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    const apiError = failure as ApiError;
    expect(apiError.status).toBe(422);
    expect(apiError.code).toBe("INVALID_REQUEST");
    expect(apiError.details).toEqual({ role_key: "superuser" });
    expect(apiError.requestId).toBe("req-test-0001");
  });

  it("没有会话令牌时不加 Authorization（客户端不会凭空造一个身份）", async () => {
    let auth: string | null = "unset";
    server.use(
      http.get("*/api/v1/workspaces/:wsId/members", ({ request }) => {
        auth = request.headers.get("Authorization");
        return HttpResponse.json({ members: [] });
      }),
    );

    await listMembers(TEST_WORKSPACE_ID);

    expect(auth).toBeNull();
  });
});
