import "./local-storage-shim";

import { describe, expect, it, vi } from "vitest";
import { useEffect } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button, Toast, ToastProvider, useToast } from "@/components/ui";

function Emitter() {
  const { toast } = useToast();
  return (
    <div>
      <Button
        onClick={() => {
          toast({ title: "已保存", description: "版本已发布", tone: "success" });
        }}
      >
        保存
      </Button>
      <Button
        onClick={() => {
          toast({ title: "发布失败", tone: "danger" });
        }}
      >
        失败
      </Button>
    </div>
  );
}

/** 挂载即弹一条短提示：配合假计时器验证自动关闭，避免真实等待的竞态。 */
function AutoEmitter() {
  const { toast } = useToast();
  useEffect(() => {
    toast({ title: "自动消失", durationMs: 50 });
  }, [toast]);
  return null;
}

describe("Toast", () => {
  it("toast() 把内容放进礼貌播报区域", async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <Emitter />
      </ToastProvider>,
    );

    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toBeEmptyDOMElement();

    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(screen.getByText("已保存")).toBeInTheDocument();
    expect(screen.getByText("版本已发布")).toBeInTheDocument();
    expect(region).toContainElement(screen.getByText("已保存"));
  });

  it("danger 走强打断区域（role=alert / aria-live=assertive）", async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <Emitter />
      </ToastProvider>,
    );

    await user.click(screen.getByRole("button", { name: "失败" }));

    const alert = screen.getByRole("alert");
    expect(alert).toHaveAttribute("aria-live", "assertive");
    expect(alert).toContainElement(screen.getByText("发布失败"));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("关闭按钮点击后提示消失", async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <Emitter />
      </ToastProvider>,
    );

    await user.click(screen.getByRole("button", { name: "保存" }));
    const close = screen.getByRole("button", { name: "关闭提示" });
    expect(close).toHaveClass("size-11");

    await user.click(close);

    await waitFor(() => {
      expect(screen.queryByText("已保存")).not.toBeInTheDocument();
    });
  });

  it("durationMs 到期后自动消失（假计时器，不依赖真实等待）", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      render(
        <ToastProvider>
          <AutoEmitter />
        </ToastProvider>,
      );

      expect(screen.getByText("自动消失")).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(60);
      });

      expect(screen.queryByText("自动消失")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("独立使用的 Toast 不渲染死按钮（未传 onDismiss 时没有关闭按钮）", () => {
    render(<Toast title="静态提示" />);

    expect(screen.getByText("静态提示")).toBeVisible();
    expect(screen.queryByRole("button", { name: "关闭提示" })).not.toBeInTheDocument();
  });

  it("useToast 在 Provider 外使用时立即报错，而不是静默失效", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      expect(() => render(<Emitter />)).toThrow(/ToastProvider/);
    } finally {
      spy.mockRestore();
    }
  });
});
