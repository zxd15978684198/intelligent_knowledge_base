import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "@/tests/msw/server";
import type { MembershipDetail } from "@/lib/contracts";
import { MemberAddDialog } from "@/features/members/MemberAddDialog";
import { MemberTable } from "@/features/members/MemberTable";
import { OwnerTransferDialog } from "@/features/members/OwnerTransferDialog";
import { useMembers } from "@/features/members/hooks";
import {
  makeMemberPage,
  makeMembership,
  makeRoles,
  TEST_MEMBER_ID,
  TEST_MEMBER_MEMBERSHIP_ID,
  TEST_OWNER_ID,
  TEST_WORKSPACE_ID,
} from "./fixtures";

/**
 * 组件用例。断言的是"用户看得到什么、能点什么"，所以钉的都是文案与可交互性：
 * - 表格里只有 user_id，**没有**姓名/邮箱（哪怕后端哪天多返回一个 username 字段）；
 * - 添加成员的客户端 UUID 校验在发请求之前拦下；
 * - 移除/转移所有权都需要第二段确认（勾选后才解锁危险按钮）；
 * - 没有 manage_members 时页面上不存在任何能改数据的控件。
 */

const TOKEN = "test-token";

function withQueryClient(ui: ReactNode): ReactNode {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

/** 用真实 hook 承载列表的宿主，用来验证"添加成功后列表被重新请求"。 */
function MembersHost(): ReactNode {
  const members = useMembers({ workspaceId: TEST_WORKSPACE_ID, token: TOKEN });
  const roles = makeRoles();

  if (!members.isSuccess) {
    return <p>加载中</p>;
  }

  return (
    <div>
      <MemberAddDialog
        workspaceId={TEST_WORKSPACE_ID}
        token={TOKEN}
        roles={roles}
        canManage
      />
      <MemberTable
        members={members.data.members}
        roles={roles}
        workspaceId={TEST_WORKSPACE_ID}
        token={TOKEN}
        canManage
        currentUserId={TEST_OWNER_ID}
      />
    </div>
  );
}

describe("features/members/MemberTable", () => {
  it("列表只显示 user_id 短值，即使数据里混进了 username 也不渲染", () => {
    const leaked: readonly (MembershipDetail & { readonly username: string })[] = [
      { ...makeMembership({ user_id: TEST_MEMBER_ID, role: "member" }), username: "张三" },
    ];

    render(
      withQueryClient(
        <MemberTable
          members={leaked}
          roles={makeRoles()}
          workspaceId={TEST_WORKSPACE_ID}
          token={TOKEN}
          canManage
          currentUserId={TEST_OWNER_ID}
        />,
      ),
    );

    expect(screen.getByRole("columnheader", { name: "用户 ID" })).toBeInTheDocument();
    expect(screen.getByText(TEST_MEMBER_ID.slice(0, 8))).toBeInTheDocument();
    expect(screen.getByTitle(TEST_MEMBER_ID)).toBeInTheDocument();
    expect(screen.queryByText("张三")).toBeNull();
    expect(screen.getByRole("button", { name: `复制用户 ID（${TEST_MEMBER_ID}）` })).toBeInTheDocument();
  });

  it("没有 manage_members 时不渲染改角色与移除控件，只说明无权限", () => {
    render(
      withQueryClient(
        <MemberTable
          members={makeMemberPage().members}
          roles={makeRoles()}
          workspaceId={TEST_WORKSPACE_ID}
          token={TOKEN}
          canManage={false}
          currentUserId={TEST_OWNER_ID}
        />,
      ),
    );

    expect(screen.queryByRole("combobox", { name: /修改成员/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /移除成员/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /退出成员/ })).toBeNull();
    expect(screen.getAllByText("无管理权限")).toHaveLength(2);
  });

  it("移除成员是两段式确认：勾选前危险按钮不可点，确认后只发一次 DELETE", async () => {
    const user = userEvent.setup();
    let deleteCalls = 0;
    server.use(
      http.delete("*/api/v1/workspaces/*/members/*", () => {
        deleteCalls += 1;
        return HttpResponse.json(null, { status: 204 });
      }),
    );

    render(
      withQueryClient(
        <MemberTable
          members={makeMemberPage().members}
          roles={makeRoles()}
          workspaceId={TEST_WORKSPACE_ID}
          token={TOKEN}
          canManage
          currentUserId={TEST_OWNER_ID}
        />,
      ),
    );

    await user.click(
      screen.getByRole("button", { name: `移除成员（${TEST_MEMBER_ID}）` }),
    );
    expect(await screen.findByRole("dialog", { name: "移除这个成员？" })).toBeInTheDocument();

    const confirm = screen.getByRole("button", { name: "确认移除" });
    expect(confirm).toBeDisabled();
    expect(deleteCalls).toBe(0);

    await user.click(
      screen.getByLabelText("我确认移除该成员，并接受他立刻失去本工作区的访问权。"),
    );
    expect(confirm).not.toBeDisabled();

    await user.click(confirm);

    await waitFor(() => expect(deleteCalls).toBe(1));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "移除这个成员？" })).toBeNull(),
    );
  });

  it("移除自己走「退出」，文案与移除他人类不同", async () => {
    const user = userEvent.setup();
    render(
      withQueryClient(
        <MemberTable
          members={makeMemberPage().members}
          roles={makeRoles()}
          workspaceId={TEST_WORKSPACE_ID}
          token={TOKEN}
          canManage
          currentUserId={TEST_MEMBER_ID}
        />,
      ),
    );

    expect(screen.getAllByText("我")).toHaveLength(1);
    await user.click(
      screen.getByRole("button", { name: `退出成员（${TEST_MEMBER_ID}）` }),
    );

    expect(await screen.findByRole("dialog", { name: "退出这个工作区？" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认退出" })).toBeDisabled();
  });
});

describe("features/members/MemberAddDialog", () => {
  it("principal_id 不是 UUID 时在客户端拦下，一个请求都不发", async () => {
    const user = userEvent.setup();
    let postCalls = 0;
    server.use(
      http.post("*/api/v1/workspaces/*/members", () => {
        postCalls += 1;
        return HttpResponse.json({}, { status: 201 });
      }),
    );

    render(
      withQueryClient(
        <MemberAddDialog
          workspaceId={TEST_WORKSPACE_ID}
          token={TOKEN}
          roles={makeRoles()}
          canManage
        />,
      ),
    );

    await user.click(screen.getByRole("button", { name: "按 principal_id 添加成员" }));
    expect(
      await screen.findByRole("dialog", { name: "按 principal_id 添加成员" }),
    ).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: /principal_id/ }), "not-a-uuid");
    await user.click(screen.getByRole("button", { name: "添加" }));

    expect(
      await screen.findByText(
        "principal_id 必须是 UUID（8-4-4-4-12 位十六进制，含四个连字符）。",
      ),
    ).toBeInTheDocument();
    expect(postCalls).toBe(0);
  });

  it("没有 manage_members 时触发器禁用并说明缺哪条权限", () => {
    render(
      withQueryClient(
        <MemberAddDialog
          workspaceId={TEST_WORKSPACE_ID}
          token={TOKEN}
          roles={makeRoles()}
          canManage={false}
        />,
      ),
    );

    const trigger = screen.getByRole("button", { name: "按 principal_id 添加成员" });
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveAttribute(
      "title",
      "需要「管理成员」权限（workspace.manage_members）",
    );
  });

  it("添加成功后关闭对话框，并让成员列表重新请求", async () => {
    const user = userEvent.setup();
    let listCalls = 0;
    let postCalls = 0;
    server.use(
      http.get("*/api/v1/workspaces/*/members", () => {
        listCalls += 1;
        return HttpResponse.json(makeMemberPage());
      }),
      http.post("*/api/v1/workspaces/*/members", async ({ request }) => {
        postCalls += 1;
        const body = (await request.json()) as { user_id?: string; role?: string };
        return HttpResponse.json(
          {
            membership_id: TEST_MEMBER_MEMBERSHIP_ID,
            workspace_id: TEST_WORKSPACE_ID,
            user_id: body.user_id ?? TEST_MEMBER_ID,
            role: body.role ?? "member",
          },
          { status: 201 },
        );
      }),
    );

    render(withQueryClient(<MembersHost />));

    await waitFor(() => expect(listCalls).toBe(1));

    await user.click(screen.getByRole("button", { name: "按 principal_id 添加成员" }));
    await user.type(await screen.findByRole("textbox", { name: /principal_id/ }), TEST_MEMBER_ID);
    await user.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() => expect(postCalls).toBe(1));
    await waitFor(() => expect(listCalls).toBe(2));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "按 principal_id 添加成员" })).toBeNull(),
    );
  });
});

describe("features/members/OwnerTransferDialog", () => {
  it("转移所有权是两段式：先选人、再勾选，才允许提交", async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    server.use(
      http.put("*/api/v1/workspaces/*/owner", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(null, { status: 204 });
      }),
    );

    render(
      withQueryClient(
        <OwnerTransferDialog
          workspaceId={TEST_WORKSPACE_ID}
          token={TOKEN}
          members={makeMemberPage().members}
          currentUserId={TEST_OWNER_ID}
          isOwner
        />,
      ),
    );

    await user.click(screen.getByRole("button", { name: "转移所有权" }));
    expect(await screen.findByRole("dialog", { name: "转移所有权？" })).toBeInTheDocument();

    const submit = screen.getByRole("button", { name: "确认转移" });
    expect(submit).toBeDisabled();

    await user.selectOptions(screen.getByRole("combobox", { name: /接任者/ }), TEST_MEMBER_ID);
    expect(submit).toBeDisabled();

    await user.click(
      screen.getByLabelText("我确认把所有者身份交给这位成员，并接受自己降为 admin。"),
    );
    expect(submit).not.toBeDisabled();

    await user.click(submit);

    await waitFor(() => expect(bodies).toHaveLength(1));
    // 后端收的是 owner_principal_id，不是 user_id；发错键名会 422。
    expect(bodies[0]).toEqual({ owner_principal_id: TEST_MEMBER_ID });
  });

  it("不是 owner 时触发器禁用，并说明只有当前所有者能转移", () => {
    render(
      withQueryClient(
        <OwnerTransferDialog
          workspaceId={TEST_WORKSPACE_ID}
          token={TOKEN}
          members={makeMemberPage().members}
          currentUserId={TEST_MEMBER_ID}
          isOwner={false}
        />,
      ),
    );

    expect(screen.getByRole("button", { name: "转移所有权" })).toBeDisabled();
    expect(screen.getByText("只有当前所有者可以转移所有权。")).toBeInTheDocument();
  });
});
