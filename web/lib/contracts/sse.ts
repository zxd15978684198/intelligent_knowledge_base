/**
 * SSE 帧解析（N2）。
 *
 * 为什么手写而不用库：后端的帧格式极窄（`api/v1/chat.py:53` 一个 `_sse()` 拼
 * `event: X\ndata: {json}\n\n`），而流式解析最容易错的地方恰好都是"边界"——
 * 跨 chunk 的半帧、`\r\n`、注释/心跳行、末帧没有空行收尾、UTF-8 多字节被切断。
 * 这些用浏览器自带的 `TextDecoder` + 一个显式状态机就能处理干净，不必引入依赖。
 *
 * 容错口径（决定消费方怎么写）：
 * - **未知 `event` 名跳过**：后端要加新事件时会先加，前端晚一拍上线不该整条流崩掉。
 * - **已知事件但帧体坏了 → 抛 `SseParseError`**：宁可显式失败，也不能把坏帧当成功
 *   （把 `done` 丢掉会让 UI 永远停在"生成中"）。
 * - **`error` 之后仍会来一帧 `done`**：本解析器不会在 `error` 处收流，消费方也必须读到最后。
 *
 * 与 `./index.ts` 的关系是单向的：sse 依赖 index，index 不 re-export sse
 * （避免值级循环导入，Next 的 RSC 边界上循环导入会变成 undefined）。
 */

import { chatSseEventSchema, SSE_EVENT_NAMES, type ChatSseEvent, type SseEventName } from "./index";

/**
 * 帧解析失败。带上原始帧（截断）与原因，否则线上只能看到一句
 * "Unexpected token < in JSON" 而不知道是哪一帧坏了。
 */
export class SseParseError extends Error {
  /** 出错的原始帧（已截断到 512 字符）。 */
  readonly raw: string;

  constructor(message: string, raw: string) {
    super(message);
    this.name = "SseParseError";
    this.raw = raw.length > 512 ? `${raw.slice(0, 512)}…` : raw;
  }
}

/**
 * 事件判别联合。形状等价于 `ChatSseEvent`（由 `chatSseEventSchema` 推出），
 * 这里起别名只是为了让消费方 import 的路径保持 `@/lib/contracts/sse`。
 */
export type SseEvent = ChatSseEvent;

const KNOWN_EVENTS: ReadonlySet<string> = new Set<string>(SSE_EVENT_NAMES);

/**
 * 行终止符长度：SSE 规范允许 `\r\n`、`\n`、`\r` 三种。
 *
 * 单独抽出 `\r` 的处理是必需的：`\r` 可能正好是缓冲区最后一个字符，
 * 此时无法判断它是独立的 CR 还是被切开的 CRLF 前半个——交给调用方等待下一个 chunk。
 */
function terminatorLength(buffer: string, index: number): number {
  const ch = buffer.charAt(index);
  if (ch === "\r") {
    return index + 1 < buffer.length && buffer.charAt(index + 1) === "\n" ? 2 : 1;
  }
  return ch === "\n" ? 1 : 0;
}

/**
 * 从缓冲区头部切出一个完整帧（以空行结尾，即连续两个行终止符）。
 *
 * 返回 `null` 表示"还不够一帧"，调用方应继续 read；而不是错误。
 */
function takeCompleteFrame(buffer: string): { block: string; rest: string } | null {
  let index = 0;
  while (index < buffer.length) {
    const first = terminatorLength(buffer, index);
    if (first === 0) {
      index += 1;
      continue;
    }
    // 缓冲区的最后一个字符是 `\r`：可能是被切开的 `\r\n`，等下一个 chunk 再判。
    if (buffer.charAt(index) === "\r" && index + 1 >= buffer.length) {
      return null;
    }
    const afterFirst = index + first;
    // 第一个终止符之后就没数据了：空行是否紧随其后还未知，继续等。
    if (afterFirst >= buffer.length) {
      return null;
    }
    const second = terminatorLength(buffer, afterFirst);
    if (second === 0) {
      // 这一行不是空行，继续往后找下一个终止符。
      index = afterFirst;
      continue;
    }
    if (buffer.charAt(afterFirst) === "\r" && afterFirst + 1 >= buffer.length) {
      return null;
    }
    return { block: buffer.slice(0, index), rest: buffer.slice(afterFirst + second) };
  }
  return null;
}

/** 按 `\r\n` / `\n` / `\r` 切行。只在已确认完整的一帧内部调用，不必担心半行。 */
function splitLines(block: string): string[] {
  return block.split(/\r\n|\n|\r/);
}

/**
 * 解析单帧。返回 `null` 表示"这帧不用管"（注释行、心跳、未知事件名）。
 *
 * 抛 `SseParseError` 表示事件名认识但帧体坏了——这种情况必须让调用方知道。
 */
export function parseSseFrame(block: string): SseEvent | null {
  let eventName = "";
  const dataLines: string[] = [];

  for (const line of splitLines(block)) {
    if (line === "") {
      continue;
    }
    // `: keep-alive` 之类：整行注释，按规范必须忽略。
    if (line.startsWith(":")) {
      continue;
    }
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    // 规范：冒号后紧跟一个空格要去掉，其余空格保留（`data:  x` 的第二个空格属于数据）。
    const rawValue = colon === -1 ? "" : line.slice(colon + 1);
    const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;

    if (field === "event") {
      eventName = value.trim();
    } else if (field === "data") {
      // 多行 data 按规范用 `\n` 连接。
      dataLines.push(value);
    }
    // `id:` / `retry:` 用不到（后端不发、也不做 Last-Event-ID 重放），忽略。
  }

  if (eventName === "" || dataLines.length === 0) {
    // 纯注释帧、或只有 event 没有 data：没有可交付的东西。
    return null;
  }
  if (!KNOWN_EVENTS.has(eventName)) {
    // 前向兼容：后端加了新事件，前端这一版先忽略。
    return null;
  }

  const raw = dataLines.join("\n");
  let payload: unknown;
  try {
    payload = JSON.parse(raw);
  } catch (cause) {
    throw new SseParseError(
      `SSE 帧 "${eventName}" 的 data 不是合法 JSON：${cause instanceof Error ? cause.message : String(cause)}`,
      block,
    );
  }

  const parsed = chatSseEventSchema.safeParse({ event: eventName as SseEventName, data: payload });
  if (!parsed.success) {
    const issues = parsed.error.issues
      .map((issue) => `${issue.path.join(".") || "data"}: ${issue.message}`)
      .join("; ");
    throw new SseParseError(`SSE 帧 "${eventName}" 不符合契约：${issues}`, block);
  }
  return parsed.data;
}

/**
 * 把 SSE 字节流解析成事件序列。
 *
 * 关键点：
 * - `TextDecoder` 用 `{stream: true}`，否则一个汉字被 chunk 边界切开时会被解成 U+FFFD，
 *   而且**不可恢复**——后续 chunk 已经错位了。
 * - 缓冲区在整个读循环里累积，所以半帧天然安全；只在确认完整帧后才前进。
 * - 读到 EOF 后补一次 `decode()`，把解码器里残留的字节吐出来。
 * - 末帧可能没有结尾空行（`_sse()` 一定会补 `\n\n`，但反代/网关可能截断），
 *   EOF 时把剩余缓冲区当作最后一帧解析，不丢数据。
 * - 消费方提前 `break` / 抛错时 `finally` 会 cancel 底层流，避免连接悬挂。
 */
export async function* parseSseStream(stream: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let drained = false;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        drained = true;
        break;
      }
      buffer += decoder.decode(value, { stream: true });

      for (;;) {
        const frame = takeCompleteFrame(buffer);
        if (frame === null) {
          break;
        }
        buffer = frame.rest;
        const event = parseSseFrame(frame.block);
        if (event !== null) {
          yield event;
        }
      }
    }

    // 解码器残留字节（最后一个多字节字符恰好跨 chunk）。
    buffer += decoder.decode();

    // 没有空行收尾的末帧：反代/网关截断连接时，半个帧会留在这里。
    if (buffer.trim() !== "") {
      try {
        const event = parseSseFrame(buffer);
        if (event !== null) {
          yield event;
        }
      } catch {
        // EOF 处的半帧只可能是"没写完"（JSON 被切断），不是后端的契约违规：
        // 静默丢弃，让消费方按"流结束"处理（D9：保留已收到的半截、不自动重放）。
        // 流中间带完整空行的坏帧仍然会抛 SseParseError，不会被这里吞掉。
      }
    }
  } finally {
    if (!drained) {
      // 消费方提前退出：取消底层流，别让连接卡在 read 上。
      await reader.cancel().catch(() => undefined);
    }
    reader.releaseLock();
  }
}
