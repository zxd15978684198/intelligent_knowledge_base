import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "@/tests/msw/server";
import {
  useAddMember,
  useMembers,
  useMyPermissions,
  useRemoveMember,
  useRoleCatalog,
  useUpdateMemberRole,
} from "@/features/members/hooks";
import {
  errorEnvelope,
  makeMemberPage,
  makeRoles,
  TEST_MEMBER_ID,
  TEST_MEMBER_MEMBERSHIP_ID,
  TEST_WORKSPACE_ID,
} from "./fixtures";

/**
 * Hook 用例。只钉四件与后端契约直接相关、坏了就会让页面"看起来正常但数据是旧的"的事：
 * 1. 身份未就绪时不发请求（否则页面会先闪一次 401）；
 * 2. 写操作成功后列表被**重新请求**，而不是只改本地缓存；
 * 3. 改角色/移除的 404 也失效列表（后端用 404 同时表达"没权限"和"记录没了"）；
 * 4. 移除自己会连带刷新"我的权限"。
 *
 * 成员 api 不转发 AbortSignal，所以这里不需要 kbs 用例里那段剥 signal 的 fetch 垫片。
 */

const SCOPE = { workspaceId: TEST_WORKSPACE_ID, token: "test-token" } as const;

function createWrapper(): ({ children }: { children: ReactNode }) => ReactNode {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("features/members/hooks", () => {
  it("没有 token 时三个查询都保持 disabled，一个请求也不发", async () => {
    let calls = 0;
    server.use(
      http.get("*/api/v1/workspaces/*/members", () => {
        calls += 1;
        return HttpResponse.json(makeMemberPage());
      }),
    );

    const { result } = renderHook(
      () => ({
        members: useMembers({ workspaceId: TEST_WORKSPACE_ID, token: null }),
        roles: useRoleCatalog({ workspaceId: TEST_WORKSPACE_ID, token: null }),
        permissions: useMyPermissions({ workspaceId: TEST_WORKSPACE_ID, token: null }),
      }),
      { wrapper: createWrapper() },
    );

    expect(result.current.members.fetchStatus).toBe("idle");
    expect(result.current.roles.fetchStatus).toBe("idle");
    expect(result.current.permissions.fetchStatus).toBe("idle");
    expect(calls).toBe(0);
  });

  it("useMembers 读 members 键并带上工作区头；enabled:false 时同样不发请求", async () => {
    const workspaceHeaders: (string | null)[] = [];
    server.use(
      http.get("*/api/v1/workspaces/*/members", ({ request }) => {
        workspaceHeaders.push(request.headers.get("X-Workspace-ID"));
        return HttpResponse.json(makeMemberPage());
      }),
    );

    const enabled = renderHook(() => useMembers(SCOPE), { wrapper: createWrapper() });
    await waitFor(() => expect(enabled.result.current.isSuccess).toBe(true));

    expect(workspaceHeaders).toEqual([TEST_WORKSPACE_ID]);
    expect(enabled.result.current.data?.members).toHaveLength(2);
    expect(enabled.result.current.data?.members[0]?.role).toBe("owner");
    // 后端只给 user_id；这里是"没有用户名可用"的前端侧证据。
    expect(Object.keys(enabled.result.current.data?.members[0] ?? {})).toEqual([
      "membership_id",
      "workspace_id",
      "user_id",
      "role",
    ]);

    const disabled = renderHook(
      () => useMembers({ ...SCOPE, enabled: false }),
      { wrapper: createWrapper() },
    );
    expect(disabled.result.current.fetchStatus).toBe("idle");
    expect(workspaceHeaders).toHaveLength(1);
  });

  it("添加成员成功后成员列表被重新请求（invalidateQueries 真的打到线上）", async () => {
    let listCalls = 0;
    let addCalls = 0;
    server.use(
      http.get("*/api/v1/workspaces/*/members", () => {
        listCalls += 1;
        return HttpResponse.json(makeMemberPage());
      }),
      http.post("*/api/v1/workspaces/*/members", async ({ request }) => {
        addCalls += 1;
        const body = await request.json();
        return HttpResponse.json(
          {
            membership_id: TEST_MEMBER_MEMBERSHIP_ID,
            workspace_id: TEST_WORKSPACE_ID,
            user_id: TEST_MEMBER_ID,
            role: (body as { role?: string }).role ?? "member",
          },
          { status: 201 },
        );
      }),
    );

    const { result } = renderHook(
      () => ({ members: useMembers(SCOPE), add: useAddMember(SCOPE) }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(result.current.members.isSuccess).toBe(true));
    expect(listCalls).toBe(1);

    await act(async () => {
      await result.current.add.mutateAsync({ user_id: TEST_MEMBER_ID, role: "member" });
    });

    await waitFor(() => expect(listCalls).toBe(2));
    expect(addCalls).toBe(1);
  });

  it("改角色拿到 404 时失败并失效列表（404 也可能是「没权限」，本地数据已过期）", async () => {
    let listCalls = 0;
    server.use(
      http.get("*/api/v1/workspaces/*/members", () => {
        listCalls += 1;
        return HttpResponse.json(makeMemberPage());
      }),
      http.patch("*/api/v1/workspaces/*/members/*", () =>
        HttpResponse.json(errorEnvelope("RESOURCE_NOT_FOUND", "membership not found"), {
          status: 404,
        }),
      ),
    );

    const { result } = renderHook(
      () => ({ members: useMembers(SCOPE), update: useUpdateMemberRole(SCOPE) }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(result.current.members.isSuccess).toBe(true));
    expect(listCalls).toBe(1);

    await act(async () => {
      await expect(
        result.current.update.mutateAsync({
          membershipId: TEST_MEMBER_MEMBERSHIP_ID,
          role: "guest",
        }),
      ).rejects.toMatchObject({ status: 404, code: "RESOURCE_NOT_FOUND" });
    });

    await waitFor(() => expect(listCalls).toBe(2));
  });

  it("移除成员成功后成员列表与「我的权限」一起被重新请求", async () => {
    let listCalls = 0;
    let permissionCalls = 0;
    server.use(
      http.get("*/api/v1/workspaces/*/members", () => {
        listCalls += 1;
        return HttpResponse.json(makeMemberPage());
      }),
      http.get("*/api/v1/workspaces/*/permissions", () => {
        permissionCalls += 1;
        return HttpResponse.json({
          workspace_id: TEST_WORKSPACE_ID,
          role: "owner",
          permissions: ["workspace.manage_members", "knowledge_base.read"],
        });
      }),
      http.delete("*/api/v1/workspaces/*/members/*", () =>
        HttpResponse.json(null, { status: 204 }),
      ),
    );

    const { result } = renderHook(
      () => ({
        members: useMembers(SCOPE),
        permissions: useMyPermissions(SCOPE),
        remove: useRemoveMember(SCOPE),
      }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(result.current.members.isSuccess).toBe(true));
    await waitFor(() => expect(result.current.permissions.isSuccess).toBe(true));
    expect(listCalls).toBe(1);
    expect(permissionCalls).toBe(1);

    await act(async () => {
      await result.current.remove.mutateAsync({ membershipId: TEST_MEMBER_MEMBERSHIP_ID });
    });

    await waitFor(() => expect(listCalls).toBe(2));
    // myPermissionsKey 嵌在 membersKey 之下，失效成员列表会连带重取权限。
    await waitFor(() => expect(permissionCalls).toBeGreaterThan(1));
  });

  it("角色目录读的是 role_key，并按后端给的顺序保留（不排序、不合并）", async () => {
    server.use(
      http.get("*/api/v1/workspaces/*/roles", () => HttpResponse.json({ roles: makeRoles() })),
    );

    const { result } = renderHook(() => useRoleCatalog(SCOPE), { wrapper: createWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.roles.map((role) => role.role_key)).toEqual([
      "admin",
      "guest",
      "member",
      "owner",
    ]);
    expect(result.current.data?.roles[0]?.permissions).toContain("workspace.manage_members");
  });
});
