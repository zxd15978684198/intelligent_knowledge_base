import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "@/tests/msw/server";
import { errorEnvelopeBody } from "@/tests/msw/handlers";
import { QueryConsole } from "@/features/query";

import { TEST_KB_ID, TEST_WORKSPACE_ID, makeChunk, makeQueryResponse } from "./fixtures";

/**
 * 检索调试台用例（MSW 打假接口，不碰真后端）。
 *
 * 覆盖任务书要求的五条路径：正常渲染排名 / degraded 提示 / 403 文案 /
 * 加载态 / 空查询拦截；另加一条"示例问题与 top_k 档位"的交互约束。
 *
 * 每条用例自建 QueryClient（`retry:false`），避免缓存串味——检索是 POST，
 * 走 `useMutation`，本来也不进 query 缓存。
 */

const QUERY_URL = "*/api/v1/knowledge-bases/:kbId/query";

function renderConsole() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });

  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  return render(<QueryConsole kbId={TEST_KB_ID} workspaceId={TEST_WORKSPACE_ID} />, {
    wrapper: Wrapper,
  });
}

async function submitQuery(user: ReturnType<typeof userEvent.setup>, text = "检索是怎么实现的？") {
  await user.type(screen.getByLabelText("检索问题"), text);
  await user.click(screen.getByRole("button", { name: "运行检索" }));
}

describe("features/query/QueryConsole", () => {
  it("按 rrf_score 降序渲染排名，null 的通道排名显示 —", async () => {
    server.use(
      http.post(QUERY_URL, () =>
        HttpResponse.json(
          makeQueryResponse({
            results: [
              makeChunk({
                chunk_id: "chunk-low",
                rrf_score: 0.01,
                vector_rank: 3,
                keyword_rank: null,
                content: "低分切片内容",
                heading_path: "附录 > 术语",
              }),
              makeChunk({
                chunk_id: "chunk-high",
                rrf_score: 0.05,
                vector_rank: 1,
                keyword_rank: 2,
                content: "高分切片内容",
              }),
            ],
          }),
        ),
      ),
    );

    const user = userEvent.setup();
    renderConsole();
    await submitQuery(user);

    const rows = await screen.findAllByTestId("retrieval-row");
    expect(rows).toHaveLength(2);

    const first = rows.at(0);
    const second = rows.at(1);
    if (!first || !second) throw new Error("检索结果应有 2 行");

    expect(within(first).getByText("0.050000")).toBeVisible();
    expect(within(first).getByText("高分切片内容")).toBeVisible();
    expect(within(first).getByText("#1")).toBeVisible();
    expect(within(first).getByText("#2")).toBeVisible();

    expect(within(second).getByText("0.010000")).toBeVisible();
    expect(within(second).getByText("#3")).toBeVisible();
    // keyword_rank 为 null → 显示 —，不是 0，也不是空白。
    expect(within(second).getByText("—")).toBeVisible();

    // 策略徽标：显示 baseline，并把"切换需重启 API"写进 title。
    const strategy = screen.getByTestId("strategy-badge");
    expect(within(strategy).getByText("baseline")).toBeVisible();
    expect(strategy).toHaveAttribute("title", "由服务端环境变量决定，切换需重启 API。");
  });

  it("degraded 非空时用 role=status 明确提示降级，逐键列出通道与原因", async () => {
    server.use(
      http.post(QUERY_URL, () =>
        HttpResponse.json(
          makeQueryResponse({ degraded: { vector: "DEPENDENCY_UNAVAILABLE" } }),
        ),
      ),
    );

    const user = userEvent.setup();
    renderConsole();
    await submitQuery(user);

    const banner = await screen.findByTestId("degraded-banner");
    expect(banner).toHaveAttribute("role", "status");
    expect(within(banner).getByText(/降级检索/)).toBeVisible();
    expect(within(banner).getByText("向量通道")).toBeVisible();
    expect(within(banner).getByText("依赖不可用")).toBeVisible();
    expect(within(banner).getByText("DEPENDENCY_UNAVAILABLE")).toBeVisible();
    // 有结果也要提示：降级 ≠ 失败，但也不能当成功。
    expect(screen.getAllByTestId("retrieval-row")).toHaveLength(1);
  });

  it("403 渲染权限文案（而不是笼统的「加载失败」）", async () => {
    server.use(
      http.post(QUERY_URL, () =>
        HttpResponse.json(
          errorEnvelopeBody({ code: "FORBIDDEN", message: "missing permission query.run" }),
          { status: 403 },
        ),
      ),
    );

    const user = userEvent.setup();
    renderConsole();
    await submitQuery(user);

    const panel = await screen.findByTestId("query-error");
    expect(within(panel).getByText("没有检索权限（403）")).toBeVisible();
    expect(within(panel).getByText(/query\.run/)).toBeVisible();
    // 权限缺失重试没有意义，因此不给重试按钮。
    expect(screen.queryByRole("button", { name: "重试本次检索" })).not.toBeInTheDocument();
  });

  it("提交中显示加载态并禁用提交按钮", async () => {
    const gate: { release?: () => void } = {};
    server.use(
      http.post(QUERY_URL, async () => {
        await new Promise<void>((resolve) => {
          gate.release = resolve;
        });
        return HttpResponse.json(makeQueryResponse());
      }),
    );

    const user = userEvent.setup();
    renderConsole();
    await submitQuery(user);

    expect(await screen.findByTestId("query-loading")).toBeVisible();
    expect(screen.getByRole("button", { name: /检索中/ })).toBeDisabled();

    gate.release?.();

    await waitFor(() => {
      expect(screen.queryByTestId("query-loading")).not.toBeInTheDocument();
    });
    expect(await screen.findAllByTestId("retrieval-row")).toHaveLength(1);
  });

  it("空查询在本地拦下，不发请求也不进入加载态", async () => {
    const user = userEvent.setup();
    renderConsole();

    await user.click(screen.getByRole("button", { name: "运行检索" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("请输入检索问题。");
    expect(screen.queryByTestId("query-loading")).not.toBeInTheDocument();
  });

  it("示例问题只填入输入框；top_k 只给 5/10/20/50 四档", async () => {
    const user = userEvent.setup();
    renderConsole();

    await user.click(screen.getByRole("button", { name: "支持哪些文档格式？" }));
    expect(screen.getByLabelText("检索问题")).toHaveValue("支持哪些文档格式？");
    expect(screen.queryByTestId("query-loading")).not.toBeInTheDocument();

    const select = screen.getByLabelText("返回条数（top_k）");
    expect(select).toHaveValue("10");
    const optionValues = Array.from(select.querySelectorAll<HTMLOptionElement>("option")).map(
      (option) => option.value,
    );
    expect(optionValues).toEqual(["5", "10", "20", "50"]);
  });

  it("404 提示知识库不存在或无权限，并说明后端为何合并两者", async () => {
    server.use(
      http.post(QUERY_URL, () =>
        HttpResponse.json(
          errorEnvelopeBody({ code: "RESOURCE_NOT_FOUND", message: "knowledge base not found" }),
          { status: 404 },
        ),
      ),
    );

    const user = userEvent.setup();
    renderConsole();
    await submitQuery(user);

    const panel = await screen.findByTestId("query-error");
    expect(within(panel).getByText("知识库不存在或不属于当前工作区（404）")).toBeVisible();
    expect(within(panel).getByText(/统一返回 404/)).toBeVisible();
  });

  it("500 展示服务异常与请求标识（便于查日志）", async () => {
    server.use(
      http.post(QUERY_URL, () =>
        HttpResponse.json(
          errorEnvelopeBody({ code: "INTERNAL_ERROR", message: "boom" }),
          { status: 500 },
        ),
      ),
    );

    const user = userEvent.setup();
    renderConsole();
    await submitQuery(user);

    const panel = await screen.findByTestId("query-error");
    expect(within(panel).getByText("服务异常（HTTP 500）")).toBeVisible();
    expect(within(panel).getByText(/请求标识：req-test-0001/)).toBeVisible();
  });

  it("展开全文：aria-expanded 切换，全文与真实定位字段都出现（不伪造字符偏移）", async () => {
    const longContent = `${"检索调试用的长切片内容。".repeat(15)}TAIL-MARKER`;
    server.use(
      http.post(QUERY_URL, () =>
        HttpResponse.json(makeQueryResponse({ results: [makeChunk({ content: longContent })] })),
      ),
    );

    const user = userEvent.setup();
    renderConsole();
    await submitQuery(user);

    // 折叠时只有摘要（被截断），Marker 不该出现。
    expect(screen.queryByText(/TAIL-MARKER/)).not.toBeInTheDocument();

    const toggle = await screen.findByRole("button", { name: "展开全文" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);

    expect(screen.getByRole("button", { name: "收起全文" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/TAIL-MARKER/)).toBeVisible();
    // 展开区如实说明：检索响应没有字符偏移，所以不做高亮。
    expect(screen.getByText(/char_start\/char_end/)).toBeVisible();
    expect(screen.getByText("chunk-0001")).toBeVisible();
  });
});
