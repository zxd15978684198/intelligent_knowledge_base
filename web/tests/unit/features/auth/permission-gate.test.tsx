import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import "./browser-storage";

import { DisabledReason } from "@/features/auth/components/disabled-reason";
import { PermissionGate } from "@/features/auth/components/permission-gate";

/**
 * 权限栅栏只做体验层：有权限渲染子树，没权限渲染解释，权限未知先不画。
 *
 * `usePermissions` 被整体替换成假实现——这里要验证的是**栅栏的分支**，
 * 而不是"权限从哪来"（那是 `use-permissions` 与 `AuthProvider` 的测试范围）。
 */
const permissionApi = vi.hoisted(() => ({
  current: {
    permissions: [] as readonly string[],
    ready: false,
    can: (_permission: string): boolean => false,
    canAny: (_permissions: readonly string[]): boolean => false,
  },
}));

vi.mock("@/lib/auth/use-permissions", () => ({
  usePermissions: () => permissionApi.current,
}));

function grant(granted: readonly string[], ready = true): void {
  permissionApi.current = {
    permissions: granted,
    ready,
    can: (permission) => granted.includes(permission),
    canAny: (candidates) => candidates.some((permission) => granted.includes(permission)),
  };
}

describe("features/auth/components/PermissionGate", () => {
  it("权限未就绪时默认什么都不渲染（避免禁用态闪一下又可用）", () => {
    grant([], false);
    const { container } = render(
      <PermissionGate permission="document.write">
        <button type="button">上传</button>
      </PermissionGate>,
    );

    expect(container.textContent).toBe("");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("权限未就绪时可指定 loading 占位", () => {
    grant([], false);
    render(
      <PermissionGate permission="document.write" loading={<span>检查权限…</span>}>
        <button type="button">上传</button>
      </PermissionGate>,
    );

    expect(screen.getByText("检查权限…")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("有权限时渲染子树，不渲染 fallback", () => {
    grant(["document.write"]);
    render(
      <PermissionGate permission="document.write" fallback={<span>无权限</span>}>
        <button type="button">上传</button>
      </PermissionGate>,
    );

    expect(screen.getByRole("button", { name: "上传" })).toBeTruthy();
    expect(screen.queryByText("无权限")).toBeNull();
  });

  it("缺权限时渲染 fallback 而不是子树", () => {
    grant(["document.read"]);
    const { container } = render(
      <PermissionGate
        permission="document.write"
        fallback={<DisabledReason permission="document.write" />}
      >
        <button type="button">上传</button>
      </PermissionGate>,
    );

    expect(screen.queryByRole("button")).toBeNull();
    expect(container.textContent).toContain("上传或修改文档");
    expect(container.textContent).toContain("document.write");
  });

  it("缺权限且没给 fallback 时静默不渲染", () => {
    grant([]);
    const { container } = render(
      <PermissionGate permission="document.write">
        <button type="button">上传</button>
      </PermissionGate>,
    );

    expect(container.textContent).toBe("");
  });
});

describe("features/auth/components/DisabledReason", () => {
  it("用中文说明 + 原始权限点解释原因，并标注 role=note（不是 alert）", () => {
    render(<DisabledReason permission="knowledge_base.delete">请联系管理员申请权限。</DisabledReason>);

    const note = screen.getByRole("note");
    expect(note.textContent).toContain("删除知识库");
    expect(note.textContent).toContain("knowledge_base.delete");
    expect(note.textContent).toContain("请联系管理员申请权限。");
  });

  it("未知权限点原样显示（暴露拼写错误，而不是显示『未知权限』）", () => {
    render(<DisabledReason permission="document.writ" />);

    expect(screen.getByRole("note").textContent).toContain("document.writ");
  });
});
