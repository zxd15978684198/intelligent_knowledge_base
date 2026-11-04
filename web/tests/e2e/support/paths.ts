/**
 * 目录定位，只在这里算一次。
 *
 * 为什么不用 Playwright 的 `config.rootDir`：它给的是 **testDir**（`web/tests/e2e`），
 * 不是配置文件所在的目录。按它拼 `..` 会得到 `web/tests`，于是 worker 的 python 路径
 * 变成 `web/tests/.venv/Scripts/python.exe` —— 一个"能通过类型检查、跑起来才炸"的坑。
 *
 * 本文件由 Playwright 的 CJS 转译加载，`__dirname` 可用（config 文件里同样可用）。
 * 万一将来改成 ESM，`typeof` 守卫会退到 `process.cwd()`；`ensureWorker` 里还有一次
 * "脚本存在吗"的硬校验兜底。
 */

import path from "node:path";

const HERE = typeof __dirname === "string" ? __dirname : process.cwd();

/** `web/`：本文件位于 `web/tests/e2e/support/paths.ts`。 */
export const WEB_ROOT = path.resolve(HERE, "..", "..", "..");

/** 仓库根目录：有 `pyproject.toml`、`.venv/`、`scripts/run_worker.py` 的那一层。 */
export const REPO_ROOT = path.resolve(WEB_ROOT, "..");
