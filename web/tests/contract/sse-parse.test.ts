import { describe, expect, it } from "vitest";

import type { SseEvent } from "@/lib/contracts/sse";
import { parseSseFrame, parseSseStream, SseParseError } from "@/lib/contracts/sse";

import {
  chatErrorThenDoneFrames,
  chatHappyPathFrames,
  DONE_AFTER_ERROR_FRAME,
  DONE_FRAME,
  ERROR_FRAME,
  RETRIEVAL_FRAME,
  sseFrame,
  sseRawFrame,
  sseText,
  TOKEN_FRAME_ONE,
  TOKEN_FRAME_TWO,
} from "../fixtures/sse";

const encoder = new TextEncoder();

/** 把若干**字符串**按顺序灌进流（每个字符串是独立 chunk）。 */
function streamOfStrings(chunks: readonly string[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

/**
 * 把**字节**切成固定大小灌进流。
 * 与 `streamOfStrings` 的区别是关键：只有按字节切，才会真的把一个汉字的 UTF-8
 * 序列切到两个 chunk 里（按字符串切永远切不开一个码点的编码）。
 */
function streamOfBytes(text: string, chunkSize: number): ReadableStream<Uint8Array> {
  const bytes = encoder.encode(text);
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (let offset = 0; offset < bytes.length; offset += chunkSize) {
        controller.enqueue(bytes.slice(offset, offset + chunkSize));
      }
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<SseEvent[]> {
  const events: SseEvent[] = [];
  for await (const event of parseSseStream(stream)) {
    events.push(event);
  }
  return events;
}

describe("parseSseStream：正常帧", () => {
  it("retrieval → token* → done，顺序与内容都对", async () => {
    const events = await collect(streamOfStrings(chatHappyPathFrames()));

    expect(events.map((event) => event.event)).toEqual(["retrieval", "token", "token", "done"]);

    const retrieval = events[0];
    expect(retrieval?.event).toBe("retrieval");
    if (retrieval?.event !== "retrieval") {
      throw new Error("第一帧不是 retrieval");
    }
    expect(retrieval.data.citations).toHaveLength(1);
    expect(retrieval.data.citations[0]?.page_no).toBe(2);
    expect(retrieval.data.degraded).toEqual({});

    const firstToken = events[1];
    if (firstToken?.event !== "token") {
      throw new Error("第二帧不是 token");
    }
    expect(firstToken.data.text).toBe("先");

    const last = events[3];
    if (last?.event !== "done") {
      throw new Error("最后一帧不是 done");
    }
    expect(last.data.status).toBe("COMPLETED");
    expect(last.data.run_id).toBe("run-20261101-0001");
    expect(last.data.verdict).toBe("SUPPORTED");
    expect(last.data.invalid_citations).toBe(0);
  });

  it("多帧挤在一个 chunk 里也能逐帧切出来", async () => {
    const events = await collect(streamOfStrings([sseText(chatHappyPathFrames())]));
    expect(events).toHaveLength(4);
  });

  it("空流不产出事件", async () => {
    const events = await collect(streamOfStrings([]));
    expect(events).toEqual([]);
  });
});

describe("parseSseStream：跨 chunk 半帧", () => {
  it("按 5 字节切（会把汉字 UTF-8 切成两半）仍能还原全部帧", async () => {
    const text = sseText(chatHappyPathFrames());
    // 先确认这条用例真的有杀伤力：文本里既有中文、字节数也远大于切片大小。
    expect(text).toContain("接电源");
    expect(encoder.encode(text).length).toBeGreaterThan(40);

    const events = await collect(streamOfBytes(text, 5));

    expect(events.map((event) => event.event)).toEqual(["retrieval", "token", "token", "done"]);
    const secondToken = events[2];
    if (secondToken?.event !== "token") {
      throw new Error("第三帧不是 token");
    }
    // 汉字被切在 chunk 边界上时，TextDecoder({stream:true}) 必须把它拼回来，
    // 拼错的表现就是这里出现 U+FFFD（替换字符）。
    expect(secondToken.data.text).toBe("接电源。");
    expect(secondToken.data.text).not.toContain("\uFFFD");
  });

  it("逐字节切（最极端的半帧）依然不丢帧", async () => {
    const text = sseText(chatHappyPathFrames());
    const events = await collect(streamOfBytes(text, 1));
    expect(events).toHaveLength(4);
  });
});

describe("parseSseStream：换行与注释", () => {
  it("\\r\\n 行结尾（含空行）可解析", async () => {
    const crlf = chatHappyPathFrames().map((frame) => frame.replace(/\n/g, "\r\n"));
    const events = await collect(streamOfStrings(crlf));
    expect(events.map((event) => event.event)).toEqual(["retrieval", "token", "token", "done"]);
  });

  it("\\r\\n 正好被 chunk 边界切开也不误判", async () => {
    const text = sseText(chatHappyPathFrames()).replace(/\n/g, "\r\n");
    const bytes = encoder.encode(text);
    // 找到第一个 `\r\n\r\n` 里第一个 `\r` 后面的位置，从 `\r` 与 `\n` 之间切开。
    const pivot = text.indexOf("\r\n") + 1;
    const head = bytes.slice(0, pivot);
    const tail = bytes.slice(pivot);

    const events = await collect(streamOfStrings([new TextDecoder().decode(head), new TextDecoder().decode(tail)]));
    expect(events).toHaveLength(4);
  });

  it("单独的 \\r 作行结尾也认（规范允许）", async () => {
    const crOnly = DONE_FRAME.replace(/\n/g, "\r");
    const events = await collect(streamOfStrings([crOnly]));
    expect(events).toHaveLength(1);
    expect(events[0]?.event).toBe("done");
  });

  it("注释/心跳行被忽略，不影响后续帧", async () => {
    const chunk = `: keep-alive\n\n${RETRIEVAL_FRAME}: ping\n\n${DONE_FRAME}`;
    const events = await collect(streamOfStrings([chunk]));
    expect(events.map((event) => event.event)).toEqual(["retrieval", "done"]);
  });

  it("末帧没有结尾空行（被截断）时仍然交付这一帧", async () => {
    const truncated = DONE_FRAME.slice(0, -2);
    const events = await collect(streamOfStrings([truncated]));
    expect(events).toHaveLength(1);
    expect(events[0]?.event).toBe("done");
  });

  it("流在帧中间断掉（半个 data）时既不抛错也不产出半帧", async () => {
    // 切在 data 的 JSON 中间：这一帧既没有空行收尾，内容也不完整。
    // 断流的责任不在解析器，所以静默丢弃，让消费方按"流结束"处理（D9 不自动重放）。
    const halfFrame = RETRIEVAL_FRAME.slice(0, Math.floor(RETRIEVAL_FRAME.length / 2));
    expect(halfFrame).not.toContain("\n\n");

    const events = await collect(streamOfStrings([halfFrame]));
    expect(events).toEqual([]);
  });
});

describe("parseSseStream：error 之后仍会来 done", () => {
  it("error 与它后面的 done 都要交付，且顺序不变", async () => {
    const events = await collect(streamOfStrings(chatErrorThenDoneFrames()));

    expect(events.map((event) => event.event)).toEqual(["error", "done"]);

    const error = events[0];
    if (error?.event !== "error") {
      throw new Error("第一帧不是 error");
    }
    expect(error.data.code).toBe("DEPENDENCY_UNAVAILABLE");
    expect(error.data.retryable).toBe(true);

    const done = events[1];
    if (done?.event !== "done") {
      throw new Error("error 之后没有 done");
    }
    // 后端用 FAILED 收尾；前端据此把"生成中"改成"失败"，而不是永远转圈。
    expect(done.data.status).toBe("FAILED");
    expect(done.data.verdict).toBeNull();
    expect(done.data.prompt_tokens).toBeNull();
  });

  it("只含 error 和 done 的两帧文本按字节切开也完整", async () => {
    const text = `${ERROR_FRAME}${DONE_AFTER_ERROR_FRAME}`;
    const events = await collect(streamOfBytes(text, 7));
    expect(events.map((event) => event.event)).toEqual(["error", "done"]);
  });
});

describe("parseSseStream：未知事件与坏帧", () => {
  it("未知 event 名跳过（前向兼容），已知帧照常交付", async () => {
    const chunk = `${sseFrame("ping", { at: 1 })}${RETRIEVAL_FRAME}${sseFrame("citation", { x: 1 })}${TOKEN_FRAME_ONE}`;
    const events = await collect(streamOfStrings([chunk]));
    expect(events.map((event) => event.event)).toEqual(["retrieval", "token"]);
  });

  it("没有 data 的帧被忽略", async () => {
    const events = await collect(streamOfStrings([`: comment only\n\n`, `event: token\n\n`]));
    expect(events).toEqual([]);
  });

  it("已知事件的 data 不是合法 JSON → SseParseError", async () => {
    const broken = sseRawFrame("done", "{not json");
    await expect(collect(streamOfStrings([broken]))).rejects.toBeInstanceOf(SseParseError);
  });

  it("已知事件但形状不符（done 缺 status）→ SseParseError，且带原始帧", async () => {
    const broken = sseRawFrame("done", JSON.stringify({ run_id: "run-x" }));
    const error = await collect(streamOfStrings([broken])).catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(SseParseError);
    if (!(error instanceof SseParseError)) {
      throw new Error("期望抛出 SseParseError");
    }
    expect(error.message).toContain("done");
    expect(error.raw).toContain("run-x");
  });

  it("retrieval 的 citations 不是数组 → SseParseError", async () => {
    const broken = sseRawFrame("retrieval", JSON.stringify({ citations: "nope", degraded: {} }));
    await expect(collect(streamOfStrings([broken]))).rejects.toBeInstanceOf(SseParseError);
  });

  it("done 的 verdict 是未知值 → 降级成 null，而不是让整条流失败", async () => {
    // 后端 `DoneEvent.verdict` 声明为 `str | None`（不是 Verdict 枚举），新增结论时
    // 不能连累 run_id / token 统计一起丢掉。
    const frame = sseFrame("done", {
      status: "COMPLETED",
      run_id: "run-x",
      prompt_version: null,
      verdict: "PARTIALLY_SUPPORTED",
      invalid_citations: 0,
      prompt_tokens: 10,
      completion_tokens: 2,
    });
    const events = await collect(streamOfStrings([frame]));
    const done = events[0];
    if (done?.event !== "done") {
      throw new Error("没有 done 帧");
    }
    expect(done.data.verdict).toBeNull();
    expect(done.data.run_id).toBe("run-x");
  });
});

describe("parseSseStream：资源释放", () => {
  it("消费方提前 break 会 cancel 底层流", async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(RETRIEVAL_FRAME));
        controller.enqueue(encoder.encode(TOKEN_FRAME_TWO));
        // 刻意不 close：模拟还在持续的生成流。
      },
      cancel() {
        cancelled = true;
      },
    });

    for await (const event of parseSseStream(stream)) {
      expect(event.event).toBe("retrieval");
      break;
    }

    expect(cancelled).toBe(true);
  });
});

describe("parseSseFrame：单帧解析", () => {
  it("纯注释帧返回 null", () => {
    expect(parseSseFrame(": ping")).toBeNull();
  });

  it("data 里的冒号与空格被保留", () => {
    const frame = `event: token\ndata: {"text":"a: b"}`;
    const parsed = parseSseFrame(frame);
    if (parsed?.event !== "token") {
      throw new Error("没有解析出 token");
    }
    expect(parsed.data.text).toBe("a: b");
  });
});
