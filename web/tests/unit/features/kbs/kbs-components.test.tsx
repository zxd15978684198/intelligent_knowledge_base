import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { KbCreateDialog } from "@/features/kbs/KbCreateDialog";

/**
 * 组件级冒烟：不测样式，只钉"表单默认值来自创建时固化的 profile"和
 * "名称为空时在前端就拦下、不发一个注定 422 的请求"。
 */
function withQueryClient(ui: ReactNode): ReactNode {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("features/kbs/KbCreateDialog", () => {
  it("打开即带默认嵌入与切片参数，名称为空时拦下提交", async () => {
    const user = userEvent.setup();
    render(withQueryClient(<KbCreateDialog />));

    await user.click(screen.getByRole("button", { name: "新建知识库" }));

    expect(await screen.findByRole("dialog", { name: "新建知识库" })).toBeInTheDocument();
    expect(screen.getByLabelText(/嵌入模型/)).toHaveValue("BAAI/bge-small-zh-v1.5");
    expect(screen.getByLabelText(/向量维度/)).toHaveValue(512);
    expect(screen.getByLabelText(/切片长度/)).toHaveValue(512);
    expect(screen.getByLabelText(/切片重叠/)).toHaveValue(64);
    expect(screen.getByLabelText("归一化向量")).toBeChecked();

    await user.click(screen.getByRole("button", { name: "创建" }));

    expect(await screen.findByText("请输入知识库名称。")).toBeInTheDocument();
  });
});
