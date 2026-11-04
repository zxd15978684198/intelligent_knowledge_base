import { describe, expect, it } from "vitest";

import { sampleDone, sampleDoneAfterError, sampleRetrieval, sampleStreamError } from "@/tests/fixtures/sse";

import { chatReducer, canStop, initialChatState, isAwaitingDoneAfterError, type ChatState } from "@/features/chat/machine";
import type { ChatEvent } from "@/features/chat/events";

/**
 * 状态机是纯函数，单独测它比"整页点一遍"能更快钉死那些容易漏的组合：
 * `error` 之后必须继续等 `done`、`closed:failed` 不能标"不完整"、
 * 收尾之后的迟到事件不许翻案。整页用例（`chat-screen.test.tsx`）只负责证明
 * 组件确实按这些转移在渲染。
 */

function run(events: readonly ChatEvent[]): ChatState {
  const started = chatReducer(initialChatState, { type: "START" });
  return events.reduce<ChatState>((state, event) => chatReducer(state, { type: "EVENT", event }), started);
}

describe("features/chat/machine", () => {
  it("START 进入 retrieving 且流是开着的", () => {
    const state = chatReducer(initialChatState, { type: "START" });

    expect(state.phase).toBe("retrieving");
    expect(state.awaitingDone).toBe(true);
    expect(canStop(state)).toBe(true);
    expect(state.answer).toBe("");
  });

  it("retrieval → token* → done：正文逐块拼接，终态 done 且 incomplete=false", () => {
    const state = run([
      { kind: "retrieval", citations: sampleRetrieval.citations, degraded: sampleRetrieval.degraded },
      { kind: "token", text: "先" },
      { kind: "token", text: "接电源。" },
      { kind: "done", done: sampleDone },
    ]);

    expect(state.phase).toBe("done");
    expect(state.answer).toBe("先接电源。");
    expect(state.citations).toHaveLength(1);
    expect(state.done?.status).toBe("COMPLETED");
    expect(state.tokenEvents).toBe(2);
    expect(state.awaitingDone).toBe(false);
    expect(state.incomplete).toBe(false);
  });

  it("收到 error 后仍处于'等 done'：errored 但 awaitingDone 保持 true", () => {
    const state = run([{ kind: "error", error: { ...sampleStreamError, retryable: true } }]);

    expect(state.phase).toBe("errored");
    expect(state.streamError?.code).toBe("DEPENDENCY_UNAVAILABLE");
    // 这条断言就是"已收 error 但还在等 done"的可表达性：
    expect(state.awaitingDone).toBe(true);
    expect(isAwaitingDoneAfterError(state)).toBe(true);
    expect(state.done).toBeNull();
  });

  it("error 之后补到的 done 才是终态，且错误信息保留下来", () => {
    const state = run([
      { kind: "error", error: { ...sampleStreamError, retryable: true } },
      { kind: "done", done: sampleDoneAfterError },
    ]);

    expect(state.phase).toBe("done");
    expect(state.done?.status).toBe("FAILED");
    expect(state.awaitingDone).toBe(false);
    // 终态到齐后错误仍然可见（页面要能同时说"失败"和"为什么失败"）。
    expect(state.streamError).not.toBeNull();
  });

  it("closed:connection 标 incomplete；closed:failed（建流前失败）不标", () => {
    const interrupted = run([
      { kind: "token", text: "半截" },
      { kind: "closed", reason: "connection" },
    ]);
    expect(interrupted.phase).toBe("errored");
    expect(interrupted.answer).toBe("半截");
    expect(interrupted.incomplete).toBe(true);

    const failedBeforeStream = run([
      { kind: "error", error: { code: "DEPENDENCY_UNAVAILABLE", message: "未配置模型", retryable: false } },
      { kind: "closed", reason: "failed" },
    ]);
    expect(failedBeforeStream.phase).toBe("errored");
    expect(failedBeforeStream.answer).toBe("");
    // 一个字都没收到，标"不完整"会让用户以为答案丢了一半。
    expect(failedBeforeStream.incomplete).toBe(false);
  });

  it("ABORT 立即收尾为 aborted，且迟到的 done 不许翻案", () => {
    const aborted = chatReducer(
      run([{ kind: "token", text: "半截" }]),
      { type: "ABORT" },
    );

    expect(aborted.phase).toBe("aborted");
    expect(aborted.incomplete).toBe(true);
    expect(aborted.awaitingDone).toBe(false);

    const late = chatReducer(aborted, { type: "EVENT", event: { kind: "done", done: sampleDone } });
    expect(late).toBe(aborted);
  });

  it("RESET 回到 idle，连引用与错误一起清空", () => {
    const state = chatReducer(
      run([
        { kind: "retrieval", citations: sampleRetrieval.citations, degraded: { vector: "DEPENDENCY_UNAVAILABLE" } },
        { kind: "token", text: "先" },
      ]),
      { type: "RESET" },
    );

    expect(state).toEqual(initialChatState);
  });
});
