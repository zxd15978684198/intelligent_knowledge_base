/**
 * 假的 OpenAI 兼容上游（只实现 `/v1/chat/completions` 的流式分支）。
 *
 * 为什么需要它：`PYKNORA_CHAT_API_KEY` 为空时后端在建流**之前**就抛
 * `DEPENDENCY_UNAVAILABLE`（见 `services/rag/answer.py`），`/chat` 根本无法
 * 进入流式分支。真实上游要联网、要钱、且回答不固定——三条都不适合 E2E。
 * 这里用本进程顶替上游，让「后端真起 HTTP → 前端真收 SSE」这条链路保持真实，
 * 只有最远端那个模型是假的。
 *
 * 帧形状与 `answer.py::stream_answer` 的解析口径逐字对齐：
 * - 逐行 `data: <json>`；`data: [DONE]` 结束；
 * - token 取 `choices[0].delta.content`；
 * - 用量取顶层 `usage`（请求里带 `stream_options.include_usage`）。
 *
 * 文本刻意与 `fixtures/power-module.txt` 呼应，方便人工核对「回答里的信息来自文档」。
 */

import http from "node:http";

const PORT = Number(process.env.FAKE_CHAT_PORT ?? 8123);

/** 逐帧吐出的内容；拼起来是一句完整回答。 */
const ANSWER_CHUNKS = ["电源接反会", "导致保险丝熔断", "，请先断电再更换同规格保险丝。"];

const FRAME_DELAY_MS = 60;

function chunkFrame(delta, finishReason = null) {
  return `data: ${JSON.stringify({
    id: "chatcmpl-e2e",
    object: "chat.completion.chunk",
    created: 0,
    model: "fake-model",
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  })}\n\n`;
}

function usageFrame(promptTokens, completionTokens) {
  return `data: ${JSON.stringify({
    id: "chatcmpl-e2e",
    object: "chat.completion.chunk",
    created: 0,
    model: "fake-model",
    choices: [],
    usage: { prompt_tokens: promptTokens, completion_tokens: completionTokens },
  })}\n\n`;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const server = http.createServer((req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ status: "ok", service: "fake-chat-upstream" }));
    return;
  }

  if (req.method !== "POST" || !(req.url ?? "").startsWith("/v1/chat/completions")) {
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { message: `no route for ${req.method} ${req.url}` } }));
    return;
  }

  // 必须把请求体读完再回：不读会让部分客户端在写完请求前就收到响应而报 EPIPE。
  const bodyChunks = [];
  req.on("data", (chunk) => bodyChunks.push(chunk));
  req.on("end", async () => {
    let stream = true;
    try {
      const parsed = JSON.parse(Buffer.concat(bodyChunks).toString("utf8"));
      stream = parsed.stream !== false;
    } catch {
      // 解析不了就按流式处理：后端永远发 stream=true，坏请求不该改变这条主路径。
    }

    res.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache",
      connection: "keep-alive",
    });

    if (!stream) {
      res.end(
        `data: ${JSON.stringify({
          id: "chatcmpl-e2e",
          object: "chat.completion",
          model: "fake-model",
          choices: [{ index: 0, message: { role: "assistant", content: ANSWER_CHUNKS.join("") } }],
          usage: { prompt_tokens: 120, completion_tokens: 28 },
        })}\n\ndata: [DONE]\n\n`,
      );
      return;
    }

    res.write(chunkFrame({ role: "assistant" }));
    for (const text of ANSWER_CHUNKS) {
      await sleep(FRAME_DELAY_MS);
      if (res.writableEnded) return;
      res.write(chunkFrame({ content: text }));
    }
    await sleep(FRAME_DELAY_MS);
    if (res.writableEnded) return;
    res.write(chunkFrame({}, "stop"));
    res.write(usageFrame(120, 28));
    res.write("data: [DONE]\n\n");
    res.end();
  });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`fake-chat-upstream listening on http://127.0.0.1:${PORT}\n`);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    server.close(() => process.exit(0));
  });
}
