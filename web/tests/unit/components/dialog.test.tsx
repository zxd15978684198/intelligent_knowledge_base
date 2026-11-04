import "./local-storage-shim";

import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button, Dialog, DialogContent, DialogTrigger } from "@/components/ui";

function DemoDialog() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button>打开</Button>
      </DialogTrigger>
      <DialogContent title="创建知识库" description="名称 3–50 字">
        <p>表单内容</p>
      </DialogContent>
    </Dialog>
  );
}

describe("Dialog", () => {
  it("由 DialogTrigger 打开，带可访问名称与 aria-modal", async () => {
    const user = userEvent.setup();
    render(<DemoDialog />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "打开" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAccessibleName("创建知识库");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText("表单内容")).toBeInTheDocument();
    expect(screen.getByText("名称 3–50 字")).toBeInTheDocument();
  });

  it("Esc 可关闭并卸载", async () => {
    const user = userEvent.setup();
    render(<DemoDialog />);

    await user.click(screen.getByRole("button", { name: "打开" }));
    await screen.findByRole("dialog");

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("打开后焦点落在对话框内部（焦点陷阱生效）", async () => {
    const user = userEvent.setup();
    render(<DemoDialog />);

    await user.click(screen.getByRole("button", { name: "打开" }));
    const dialog = await screen.findByRole("dialog");

    await waitFor(() => {
      expect(dialog.contains(document.activeElement)).toBe(true);
    });
  });

  it("关闭按钮有中文可访问名称，点击后关闭", async () => {
    const user = userEvent.setup();
    render(<DemoDialog />);

    await user.click(screen.getByRole("button", { name: "打开" }));
    await screen.findByRole("dialog");

    await user.click(screen.getByRole("button", { name: "关闭对话框" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});
