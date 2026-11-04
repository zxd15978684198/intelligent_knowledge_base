import { setupServer } from "msw/node";
import { handlers } from "./handlers";

/**
 * 全局 MSW 服务：`vitest.setup.ts` 里 listen / resetHandlers / close。
 *
 * `onUnhandledRequest: "error"` 是刻意的（见 setup 注释）：前端调到后端不存在的
 * 接口时，用例必须失败，而不是静默拿 404 当空数据继续跑。
 *
 * 各特征目录自己的用例用 `server.use(...)` 覆盖单条 handler，不要在这里堆业务分支。
 */
export const server = setupServer(...handlers);
