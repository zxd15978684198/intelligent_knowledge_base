/**
 * 跑一次 E2E 需要跨进程传递的那点状态：**本次运行专属的账号与工作区**。
 *
 * 为什么落在系统临时目录而不是仓库里：这文件每次运行都会变、且含明文令牌，
 * 放进 `web/tests/e2e/` 会在 `git status` 里制造噪音，也容易被误提交。
 * 用固定文件名让 globalSetup 与各 worker 进程在不共享内存的情况下拿到同一份数据。
 */

import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { E2E_PASSWORD } from "./env";

export interface E2eState {
  /** 本次运行注册的登录名（唯一，避免与历史 E2E 数据撞唯一键）。 */
  readonly username: string;
  readonly email: string;
  readonly password: string;
  /** 注册接口直接签发的令牌：测试自己调 API 时至直接用它，不必再登录一次。 */
  readonly token: string;
  readonly principalId: string;
  /**
   * 注册随建出来的默认工作区 id。
   *
   * 前端从 `GET /auth/me` 的 `workspace_id` 取同一个值；测试单独保存一份，
   * 是为了在页面上断言"工作区就绪"之前就能先布置数据。
   */
  readonly workspaceId: string;
  readonly createdAt: string;
}

const STATE_FILE = join(tmpdir(), "pyknora-e2e-state.json");

export function stateFilePath(): string {
  return STATE_FILE;
}

export function writeState(state: E2eState): void {
  writeFileSync(STATE_FILE, `${JSON.stringify(state, null, 2)}\n`, "utf8");
}

export function readState(): E2eState {
  const raw = readFileSync(STATE_FILE, "utf8");
  const parsed: unknown = JSON.parse(raw);
  if (!isE2eState(parsed)) {
    throw new Error(`E2E 状态文件格式不对：${STATE_FILE}（先跑 globalSetup）`);
  }
  return parsed;
}

function isE2eState(value: unknown): value is E2eState {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.username === "string" &&
    typeof record.email === "string" &&
    typeof record.password === "string" &&
    typeof record.token === "string" &&
    typeof record.principalId === "string" &&
    typeof record.workspaceId === "string" &&
    typeof record.createdAt === "string"
  );
}

/** 造一个本次运行唯一的后缀（毫秒时间戳 + 随机尾，串行跑也不会重）。 */
export function uniqueSuffix(): string {
  const stamp = Date.now().toString(36);
  const tail = Math.floor(Math.random() * 1_000_000).toString(36);
  return `${stamp}${tail}`;
}

/** 一组新账号凭据。用户名只允许 `[A-Za-z0-9_.-]` 且 3–64 位（后端 `RegisterRequest`）。 */
export function newCredentials(suffix: string): {
  username: string;
  email: string;
  password: string;
} {
  return {
    username: `e2e_${suffix}`.slice(0, 64),
    email: `e2e_${suffix}@test.local`,
    password: E2E_PASSWORD,
  };
}
