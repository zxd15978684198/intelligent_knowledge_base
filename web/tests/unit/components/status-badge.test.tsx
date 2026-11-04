import "./local-storage-shim";

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { StatusBadge, type BadgeTone } from "@/components/ui";

const TONES: BadgeTone[] = ["neutral", "info", "success", "warning", "danger"];

describe("StatusBadge", () => {
  it("文字通道始终可见（不能只靠颜色区分状态）", () => {
    render(<StatusBadge label="处理中" tone="info" />);
    expect(screen.getByText("处理中")).toBeVisible();
  });

  it("按 tone 应用语义令牌色，并把 tone 暴露成 data 属性", () => {
    render(<StatusBadge label="已完成" tone="success" />);
    const badge = screen.getByText("已完成").closest("[data-tone]");

    expect(badge).not.toBeNull();
    expect(badge).toHaveAttribute("data-tone", "success");
    expect(badge).toHaveClass("bg-success-weak");
    expect(badge).toHaveClass("text-success");
  });

  it("五种 tone 都映射到弱底 + 同色文字（不允许漏项落到无色）", () => {
    const { container } = render(
      <div>
        {TONES.map((tone) => (
          <StatusBadge key={tone} label={tone} tone={tone} />
        ))}
      </div>,
    );

    for (const tone of TONES) {
      const badge = container.querySelector(`[data-tone="${tone}"]`);
      const expectedWeak = tone === "neutral" ? "bg-surface-muted" : `bg-${tone}-weak`;
      expect(badge).not.toBeNull();
      expect(badge).toHaveClass(expectedWeak);
    }
  });

  it("圆点只是装饰，对读屏隐藏", () => {
    const { container } = render(<StatusBadge label="失败" tone="danger" />);
    const dot = container.querySelector("[aria-hidden='true']");

    expect(dot).not.toBeNull();
    expect(dot).toHaveClass("bg-danger");
  });

  it("srLabel 给读屏补完整语义（视觉隐藏但可被查询）", () => {
    render(<StatusBadge label="处理中" tone="warning" srLabel="作业状态：处理中" />);
    const hint = screen.getByText("作业状态：处理中");

    expect(hint).toBeInTheDocument();
    expect(hint).toHaveClass("sr-only");
  });

  it("未传 srLabel 时不产生多余文本节点", () => {
    const { container } = render(<StatusBadge label="排队中" tone="neutral" />);
    expect(container.textContent).toBe("排队中");
  });
});
