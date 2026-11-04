import "./local-storage-shim";

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button } from "@/components/ui";

describe("Button", () => {
  it("默认渲染为 type=button，并带 44px 触控高度与主色变体", () => {
    render(<Button>保存</Button>);
    const button = screen.getByRole("button", { name: "保存" });

    expect(button).toHaveAttribute("type", "button");
    expect(button).toHaveClass("min-h-hit");
    expect(button).toHaveClass("bg-primary");
    expect(button).not.toBeDisabled();
  });

  it("variant/size 会切换样式类，size 不牺牲触控高度", () => {
    const { rerender } = render(
      <Button variant="secondary" size="sm">
        取消
      </Button>,
    );
    const button = screen.getByRole("button", { name: "取消" });
    expect(button).toHaveClass("bg-surface");
    expect(button).toHaveClass("px-3");
    expect(button).toHaveClass("min-h-hit");

    rerender(
      <Button variant="danger" size="md">
        删除
      </Button>,
    );
    expect(screen.getByRole("button", { name: "删除" })).toHaveClass("bg-danger");
  });

  it("点击会触发 onClick", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(<Button onClick={onClick}>提交</Button>);

    await user.click(screen.getByRole("button", { name: "提交" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("键盘可聚焦，Enter 可激活", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(<Button onClick={onClick}>键盘</Button>);
    const button = screen.getByRole("button", { name: "键盘" });

    await user.tab();
    expect(button).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("disabled 时不可点击、不触发 onClick", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(
      <Button disabled onClick={onClick}>
        禁用
      </Button>,
    );
    const button = screen.getByRole("button", { name: "禁用" });

    expect(button).toBeDisabled();
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("loading 时 aria-busy=true、禁用交互并显示转圈", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(
      <Button loading onClick={onClick}>
        处理中
      </Button>,
    );
    const button = screen.getByRole("button", { name: "处理中" });

    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
    expect(screen.getByTestId("button-spinner")).toBeInTheDocument();
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("asChild 把样式与 aria 合并到子元素（渲染成链接），并保留 44px 高度", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(
      <Button asChild>
        <a
          href="/kbs"
          onClick={(event) => {
            event.preventDefault();
            onClick();
          }}
        >
          去知识库
        </a>
      </Button>,
    );

    const link = screen.getByRole("link", { name: "去知识库" });
    expect(link).toHaveAttribute("href", "/kbs");
    expect(link).toHaveClass("min-h-hit");
    expect(link).toHaveClass("bg-primary");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();

    await user.click(link);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("asChild + loading 时阻止重复提交", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(
      <Button asChild loading>
        <a
          href="/kbs"
          onClick={(event) => {
            event.preventDefault();
            onClick();
          }}
        >
          去知识库
        </a>
      </Button>,
    );

    const link = screen.getByRole("link", { name: "去知识库" });
    expect(link).toHaveAttribute("aria-busy", "true");
    expect(link).toHaveAttribute("aria-disabled", "true");
    await user.click(link);
    expect(onClick).not.toHaveBeenCalled();
  });
});
