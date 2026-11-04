/**
 * E2E 全局准备：确认三个进程都活着，然后**注册一个本次运行专属的账号**。
 *
 * 顺序与职责：
 * 1. 后端（uvicorn）与假上游由 `playwright.config.ts` 的 webServer 拉起，这里只探活
 *    并核对配置——探活失败时的报错要能直接指向"库没起"这种最常见的环境问题；
 * 2. worker 没有端口可探，由这里起（见 `support/worker.ts`）；
 * 3. 账号每个 run 全新注册：库里 `principals` 里的测试夹具账号 `password_hash`
 *    是占位串（`test-fixture-not-a-login-hash`），**不能用来登录**，只能真注册。
 */

import { register } from "./api";
import { API_BASE_URL, FAKE_CHAT_PORT } from "./env";
import { REPO_ROOT } from "./paths";
import { newCredentials, uniqueSuffix, writeState } from "./state";
import { ensureWorker } from "./worker";

const READY_TIMEOUT_MS = 40_000;
const READY_INTERVAL_MS = 500;

async function waitForHttp(url: string): Promise<Response> {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  let lastError = "未知错误";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(3_000) });
      if (response.ok) return response;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, READY_INTERVAL_MS));
  }
  throw new Error(`${url} 在 ${READY_TIMEOUT_MS}ms 内没有就绪（最后一次：${lastError}）`);
}

/** 后端探活 + 配置核对。 */
async function checkBackend(): Promise<void> {
  const response = await waitForHttp(`${API_BASE_URL}/health`).catch((error: unknown) => {
    throw new Error(
      `${API_BASE_URL}/health 探活失败：${error instanceof Error ? error.message : String(error)}\n` +
        "排查顺序：1) docker ps 看 pyknora-pg-p0 是否 Up（没起就 docker start pyknora-pg-p0，等 10 秒）；" +
        "2) 端口 8000 是否被别的进程占用；3) 后端累计日志见 playwright 输出里的 [WebServer] 前缀行。",
    );
  });

  const health: unknown = await response.json();
  const embedder =
    typeof health === "object" && health !== null && "embedder" in health
      ? (health as { embedder: unknown }).embedder
      : undefined;

  // 用例建的库写死 `test/hashing`（见 support/env.ts）：后端不是这个后端，
  // 入库会被 `check_profile_compat` 拒绝，表现为"作业失败"而不是"配置错了"。
  if (embedder !== "test/hashing") {
    throw new Error(
      `后端 embedder=${String(embedder)}，与用例约定的 test/hashing 不一致。` +
        "后端必须带 PYKNORA_EMBEDDING_BACKEND=hashing 启动（见 tests/e2e/support/backend-env.ts）。",
    );
  }
}

async function checkFakeUpstream(): Promise<void> {
  await waitForHttp(`http://127.0.0.1:${FAKE_CHAT_PORT}/health`).catch((error: unknown) => {
    throw new Error(
      `假上游（OpenAI 兼容）未就绪：${error instanceof Error ? error.message : String(error)}\n` +
        "它是 playwright.config.ts 里的一条 webServer；单独起：node tests/e2e/support/fake-chat-upstream.mjs",
    );
  });
}

export default async function globalSetup(): Promise<void> {
  await checkBackend();
  await checkFakeUpstream();

  const worker = await ensureWorker(REPO_ROOT);
  process.stdout.write(`[e2e] worker pid=${String(worker.pid)}（本次启动=${String(worker.owned)}）\n`);

  const suffix = uniqueSuffix();
  const credentials = newCredentials(suffix);

  const tokens = await register(
    credentials.username,
    credentials.email,
    credentials.password,
    `E2E ${suffix}`,
  ).catch((error: unknown) => {
    throw new Error(
      `注册 E2E 账号失败：${error instanceof Error ? error.message : String(error)}\n` +
        "最常见原因是数据库不可达——docker ps 确认 pyknora-pg-p0 在 Up，且 E2E_DATABASE_URL 的端口对得上。",
    );
  });

  writeState({
    username: credentials.username,
    email: credentials.email,
    password: credentials.password,
    token: tokens.access_token,
    principalId: tokens.principal_id,
    workspaceId: tokens.workspace_id,
    createdAt: new Date().toISOString(),
  });

  process.stdout.write(
    `[e2e] 账号 ${credentials.username}（principal=${tokens.principal_id} workspace=${tokens.workspace_id}）\n`,
  );
}
