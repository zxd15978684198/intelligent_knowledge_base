import "./local-storage-shim";

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button, EmptyState } from "@/components/ui";

describe("EmptyState", () => {
  it("title 作为标题暴露，description 可选展示", () => {
    render(<EmptyState title="还没有文档" description="上传一份 PDF 开始索引。" />);

    expect(screen.getByRole("heading", { name: "还没有文档" })).toBeVisible();
    expect(screen.getByText("上传一份 PDF 开始索引。")).toBeVisible();
  });

  it("只给 title 时不渲染描述与操作区", () => {
    render(<EmptyState title="暂无成员" />);

    expect(screen.getByRole("heading", { name: "暂无成员" })).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("action 渲染为可点击、可键盘聚焦的操作", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(<EmptyState title="加载失败" description="请稍后重试" action={<Button onClick={onClick}>重试</Button>} />);

    const action = screen.getByRole("button", { name: "重试" });
    await user.tab();
    expect(action).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("容器无 role 冲突，标题层级是 h3", () => {
    render(<EmptyState title="没有检索结果" />);
    expect(screen.getByRole("heading", { level: 3, name: "没有检索结果" })).toBeVisible();
  });
});
