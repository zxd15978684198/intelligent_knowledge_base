/**
 * 后端进程（API / Worker）需要的环境变量，**只在这里定义一次**。
 *
 * `playwright.config.ts` 的 webServer 与 `global-setup.ts` 起的 worker 必须看到
 * 完全相同的配置：两边描述符不一致时，最典型的症状是"上传成功、作业一直失败"，
 * 而失败原因藏在 worker 的日志里，排查成本极高。
 *
 * 这些值都是**本仓库 dev 环境**的事实，不是产品默认值：
 * - 数据库宿主端口 5434 / 容器名 `pyknora-pg-p0`（见 `docs/项目结构说明.md` 与本机 docker）；
 * - `embedding_backend=hashing` 用 `test/hashing` 假嵌入，不下载 HF 模型；
 * - chat 上游指向本进程起的假 OpenAI 服务（`support/fake-chat-upstream.mjs`），
 *   因为 `chat_api_key` 为空时 `/chat` 在建流前就 503，测不到流式。
 */

export const API_PORT = "8000";

export const FAKE_CHAT_PORT = "8123";

/** 本机 dev 库；换机器时用 `E2E_DATABASE_URL` 覆盖，不要改这里的默认值以外的代码。 */
export const DATABASE_URL =
  process.env.E2E_DATABASE_URL ?? "postgresql+asyncpg://postgres:dev@127.0.0.1:5434/pyknora";

/** 从仓库根目录起的 API / Worker 共用环境变量。 */
export function backendEnv(): Record<string, string> {
  return {
    PYKNORA_DATABASE_URL: DATABASE_URL,
    // `build_embedder` 的 choice 分支名就是 "hashing"；对应 model_key = test/hashing。
    PYKNORA_EMBEDDING_BACKEND: "hashing",
    PYKNORA_CHAT_BASE_URL: `http://127.0.0.1:${FAKE_CHAT_PORT}/v1`,
    PYKNORA_CHAT_API_KEY: "e2e-fake-key",
    PYKNORA_CHAT_MODEL: "fake-model",
  };
}
