/**
 * E2E 的地址与常量。
 *
 * 这里只有两件事：把"测试直连后端"和"浏览器走 Next 同源代理"分开写清楚，
 * 以及锁定一组不随机器变化的默认值。
 *
 * 为什么测试要直连后端：`web/lib/api/client.ts` 给请求补 `/api/v1`，而
 * `next.config.ts` 的 rewrites 再把 `/api/*` 转给 `API_PROXY_TARGET`。测试若也走
 * 代理，一旦代理配置错，用例会以"页面没反应"失败，看不出是代理错还是接口错。
 * 所以**布置数据与断言事实**一律直连 `http://127.0.0.1:8000`，只有浏览器内的
 * 交互走 `http://127.0.0.1:3000`——代理链路的正确性由浏览器用例本身来证明。
 */

/** 后端（FastAPI）直连地址：给测试自己调 API 用，不经过 Next。 */
export const API_BASE_URL = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:8000";

/** 前端（Next）地址：浏览器访问它，`use.baseURL` 也用同一个值。 */
export const UI_BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";

/** 假上游（OpenAI 兼容）端口，与 `playwright.config.ts` 里 webServer 的 env 保持一致。 */
export const FAKE_CHAT_PORT = process.env.FAKE_CHAT_PORT ?? "8123";

/** E2E 账号的密码：满足后端 `min_length=8` 与 bcrypt 72 字节上限。 */
export const E2E_PASSWORD = "Passw0rd!2026";

/** 单次 HTTP 调用的超时；后端在本地，超过这个数就是真卡住了。 */
export const API_TIMEOUT_MS = 15_000;

/** 作业从 pending 到 succeeded 的最长等待；本地 worker 一般 1–3 秒跑完。 */
export const JOB_TIMEOUT_MS = 60_000;

/**
 * 建知识库用的嵌入配置。
 *
 * 必须与后端进程的 `PYKNORA_EMBEDDING_BACKEND=hashing` 对上：hashing 后端的
 * `model_key` 是 `test/hashing`、维度取 `PYKNORA_EMBEDDING_DIM`（默认 512）。
 * 换成别的 model_key 也能建成库，但 worker 侧 `check_profile_compat` 会拒绝入库，
 * 表现为"作业失败"——那是配置不匹配，不是产品缺陷。
 */
export const E2E_EMBEDDING_PROFILE = {
  embedding_model_key: "test/hashing",
  embedding_dim: 512,
  chunk_size: 512,
  chunk_overlap: 64,
  normalize: true,
} as const;
