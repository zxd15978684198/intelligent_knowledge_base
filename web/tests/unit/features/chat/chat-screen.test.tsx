import { http } from "msw";
import { describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { errorResponse, sseResponse, TEST_REQUEST_ID } from "@/tests/msw/handlers";
import { server } from "@/tests/msw/server";
import {
  chatHappyPathFrames,
  DONE_FRAME,
  ERROR_FRAME,
  DONE_AFTER_ERROR_FRAME,
  RETRIEVAL_FRAME,
  sampleDone,
  sseFrame,
} from "@/tests/fixtures/sse";

import { ChatScreen } from "@/features/chat/ChatScreen";

/**
 * 整页用例：证明"帧 → 状态机 → 界面"这条链在真实渲染下是对的。
 *
 * 每条用例都只声明一个 handler（MSW 全局是 `onUnhandledRequest: "error"`，
 * 声明漏了会直接失败），并且都对着后端的真实帧序：
 * `retrieval → token* → done`，出错时 `error` **之后还会有一帧 done**。
 */

const CHAT_PATH = "*/api/v1/knowledge-bases/:kbId/chat";

async function ask(user: ReturnType<typeof userEvent.setup>, query: string): Promise<void> {
  await user.type(screen.getByLabelText("问题"), query);
  await user.click(screen.getByRole("button", { name: "发送" }));
}

function answerBody(): HTMLElement {
  return screen.getByTestId("answer-body");
}

/**
 * 一个"发完就挂着"的 SSE 响应：用来在流中途点停止。
 *
 * 不能用 `sseResponse`——它会立刻关闭流，也就没有"半截"可言了。
 */
function heldSseResponse(frames: readonly string[]): { response: Response; close: () => void } {
  const encoder = new TextEncoder();
  let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      streamController = controller;
      controller.enqueue(encoder.encode(frames.join("")));
    },
  });
  return {
    response: new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
    }),
    close: () => {
      try {
        streamController?.close();
      } catch {
        // 用例已经中止并取消了流：再 close 会抛 TypeError，这里无需处理。
      }
    },
  };
}

describe("features/chat/ChatScreen", () => {
  it("完整流：retrieval → token → token → done，正文逐字拼接并展示 run_id", async () => {
    const user = userEvent.setup();
    let requestBody: unknown = null;
    server.use(
      http.post(CHAT_PATH, async ({ request }) => {
        requestBody = await request.json();
        return sseResponse(chatHappyPathFrames());
      }),
    );

    render(<ChatScreen kbId="kb-1" />);
    await ask(user, "电源怎么接？");

    await waitFor(() => {
      expect(answerBody()).toHaveTextContent("先接电源。");
    });
    expect(requestBody).toEqual({ query: "电源怎么接？" });
    expect(answerBody()).toHaveAttribute("data-citation-count", "1");
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("证据支持")).toBeInTheDocument();
    expect(screen.getByText("run-20261101-0001")).toBeInTheDocument();
    expect(screen.getByText("rag-answer-v3")).toBeInTheDocument();
    // 流结束后不再有光标，也不会出现"不完整"提示。
    expect(screen.queryByTestId("stream-cursor")).not.toBeInTheDocument();
    expect(screen.queryByText(/回答不完整/)).not.toBeInTheDocument();
  });

  it("error 之后仍会收到 done：错误可见且终态是 FAILED，不会永远停在'失败中'", async () => {
    const user = userEvent.setup();
    server.use(http.post(CHAT_PATH, () => sseResponse([ERROR_FRAME, DONE_AFTER_ERROR_FRAME])));

    render(<ChatScreen kbId="kb-1" />);
    await ask(user, "电源怎么接？");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("向量检索不可用，本次只走了关键词路");
    expect(alert).toHaveTextContent("DEPENDENCY_UNAVAILABLE");
    // 终态来自 error 之后的那帧 done（run_id 只可能从 done 拿到）。
    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText("run-20261101-0003")).toBeInTheDocument();
    expect(screen.getByText("（未走到模型）")).toBeInTheDocument();
    // 有 done 就不是断流。
    expect(screen.queryByText(/回答不完整（连接中断）/)).not.toBeInTheDocument();
  });

  it("ABSTAINED：拒答要说明是'证据不足'，而不是伪装成一条正常回答", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(CHAT_PATH, () =>
        sseResponse([
          RETRIEVAL_FRAME,
          sseFrame("token", { text: "检索到的资料不足以支撑这个问题的答案，因此不作答。" }),
          sseFrame("done", {
            ...sampleDone,
            status: "ABSTAINED",
            verdict: "INSUFFICIENT",
            run_id: "run-20261101-0002",
          }),
        ]),
      ),
    );

    render(<ChatScreen kbId="kb-1" />);
    await ask(user, "保修期几年？");

    expect(await screen.findByText("证据不足·已拒答")).toBeInTheDocument();
    expect(screen.getByText("证据不足")).toBeInTheDocument();
    expect(answerBody()).toHaveTextContent("资料不足以支撑");
  });

  it("BUDGET_EXCEEDED：预算中止单独成态，不写成'失败'也不写成'拒答'", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(CHAT_PATH, () =>
        sseResponse([
          RETRIEVAL_FRAME,
          sseFrame("token", { text: "（预算已用尽，输出被截断）" }),
          sseFrame("done", {
            ...sampleDone,
            status: "BUDGET_EXCEEDED",
            verdict: null,
            prompt_tokens: null,
            completion_tokens: null,
            run_id: "run-20261101-0004",
          }),
        ]),
      ),
    );

    render(<ChatScreen kbId="kb-1" />);
    await ask(user, "把整本手册总结一遍");

    expect(await screen.findByText("预算中止·超限")).toBeInTheDocument();
    expect(screen.getByText(/token 预算上限/)).toBeInTheDocument();
    // 预算中止时后端不发 verdict，界面不许编一个出来。
    expect(screen.queryByText("证据支持")).not.toBeInTheDocument();
    expect(screen.queryByText("证据冲突")).not.toBeInTheDocument();
  });

  it("主动中止：保留半截回答并显式标记不完整，不假装续传", async () => {
    const user = userEvent.setup();
    const held = heldSseResponse([
      RETRIEVAL_FRAME,
      sseFrame("token", { text: "先把电源" }),
    ]);
    server.use(http.post(CHAT_PATH, () => held.response));

    try {
      render(<ChatScreen kbId="kb-1" />);
      await ask(user, "电源怎么接？");

      await waitFor(() => {
        expect(answerBody()).toHaveTextContent("先把电源");
      });

      await user.click(screen.getByRole("button", { name: "停止" }));

      expect(await screen.findByText("已停止")).toBeInTheDocument();
      expect(screen.getByText(/回答不完整（连接中断）/)).toBeInTheDocument();
      expect(answerBody()).toHaveTextContent("先把电源");
      // 停止后回到可再次提问的状态。
      expect(screen.getByRole("button", { name: "发送" })).toBeInTheDocument();
    } finally {
      held.close();
    }
  });

  it("建流前 503：把后端错误信封原样展示，且不标'不完整'、不进入流式态", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(CHAT_PATH, () =>
        errorResponse(503, {
          code: "DEPENDENCY_UNAVAILABLE",
          message: "chat model is not configured",
          retryable: false,
        }),
      ),
    );

    render(<ChatScreen kbId="kb-1" />);
    await ask(user, "电源怎么接？");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("DEPENDENCY_UNAVAILABLE");
    expect(alert).toHaveTextContent("chat model is not configured");
    expect(alert).toHaveTextContent(`请求 ID：${TEST_REQUEST_ID}`);
    // 一个字都没收到：没有半截正文，也不该出现"不完整"或"已停止"。
    expect(answerBody().textContent).toBe("");
    expect(screen.queryByText(/回答不完整/)).not.toBeInTheDocument();
    expect(screen.queryByText("已停止")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发送" })).toBeInTheDocument();
  });

  it("越界引用标红：正文标记、状态栏与证据栏三处都说清'未被系统校验通过'", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(CHAT_PATH, () =>
        sseResponse([
          RETRIEVAL_FRAME,
          sseFrame("token", { text: "先接电源" }),
          sseFrame("token", { text: "[资料 9]。" }),
          sseFrame("done", { ...sampleDone, invalid_citations: 1 }),
        ]),
      ),
    );

    render(<ChatScreen kbId="kb-1" />);
    await ask(user, "电源怎么接？");

    await waitFor(() => {
      expect(answerBody()).toHaveTextContent("先接电源[资料 9]。");
    });

    const marker = within(answerBody()).getByRole("button", { name: "[资料 9]" });
    expect(marker).toHaveAttribute("data-invalid", "true");
    expect(screen.getByText(/1 条引用未被系统校验通过（序号 9）/)).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /证据/ }));
    expect(screen.getByText(/该引用未被系统校验通过：本次检索没有返回第 9 条资料/)).toBeInTheDocument();
  });

  it("点击证据高亮正文：证据栏选中后切回回答，对应 [资料 N] 被聚焦并按下", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(CHAT_PATH, () =>
        sseResponse([
          RETRIEVAL_FRAME,
          sseFrame("token", { text: "先接电源" }),
          sseFrame("token", { text: "[资料 1]。" }),
          DONE_FRAME,
        ]),
      ),
    );

    render(<ChatScreen kbId="kb-1" />);
    await ask(user, "电源怎么接？");

    await waitFor(() => {
      expect(answerBody()).toHaveTextContent("先接电源[资料 1]。");
    });

    await user.click(screen.getByRole("tab", { name: /证据/ }));
    await user.click(screen.getByRole("button", { name: /\[资料 1\]/ }));

    expect(screen.getByRole("tab", { name: "回答" })).toHaveAttribute("aria-selected", "true");
    const marker = within(answerBody()).getByRole("button", { name: "[资料 1]" });
    await waitFor(() => {
      expect(marker).toHaveAttribute("aria-pressed", "true");
      expect(marker).toHaveFocus();
    });
  });
});
