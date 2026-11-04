/**
 * 起停入库 worker（`scripts/run_worker.py`）。
 *
 * 为什么 worker 由 globalSetup 管而不是 `webServer`：Playwright 的 webServer 每条
 * 都要求一个可探活的 URL，而 worker 不监听端口——硬塞进去只能拿别的端口当探针，
 * 探活了也不代表 worker 活着。
 *
 * 进程管理刻意保持"一个直接子进程"：用 venv 里的 `python.exe` 直接跑脚本，
 * 不经 `uv run` 包一层，否则 Windows 上 `child.kill()` 只会杀掉 uv，python 变孤儿。
 */

import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { backendEnv } from "./backend-env";

const PID_FILE = join(tmpdir(), "pyknora-e2e-worker.pid");

/** 保留最近若干行日志，worker 启动即崩时用来解释原因。 */
const LOG_LIMIT = 200;

export function venvPython(repoRoot: string): string {
  return process.platform === "win32"
    ? join(repoRoot, ".venv", "Scripts", "python.exe")
    : join(repoRoot, ".venv", "bin", "python");
}

function readPidFile(): number | null {
  try {
    const raw = readFileSync(PID_FILE, "utf8").trim();
    const pid = Number.parseInt(raw, 10);
    return Number.isFinite(pid) && pid > 0 ? pid : null;
  } catch {
    return null;
  }
}

function isAlive(pid: number): boolean {
  try {
    // signal 0 只做存在性探测，不发信号。
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * 确保有一个 worker 在跑；返回 pid 以及"是不是这次启动的"。
 *
 * 已经有一个活的（上一次运行留下的 pid 文件）就直接复用，避免两个 worker 抢同一批
 * 作业——后端用 `SKIP LOCKED` 认领，两个 worker 不会重复处理，但日志会交叉、
 * 资源也浪费。
 */
export async function ensureWorker(repoRoot: string): Promise<{ pid: number; owned: boolean }> {
  const existing = readPidFile();
  if (existing !== null && isAlive(existing)) {
    return { pid: existing, owned: false };
  }

  // 先确认路径真的存在：`spawn` 抛 ENOENT 时 pid 是 undefined，日志里只剩一行
  // "拿不到 pid"，看不出是哪个路径错了。
  const python = venvPython(repoRoot);
  const script = join(repoRoot, "scripts", "run_worker.py");
  const missing = [
    existsSync(python) ? null : python,
    existsSync(script) ? null : script,
  ].filter((value): value is string => value !== null);
  if (missing.length > 0) {
    throw new Error(
      `worker 启动路径不对（repoRoot=${repoRoot}），缺少：${missing.join("、")}。` +
        "请确认从 web/ 目录运行，且仓库根已 uv sync --extra dev（.venv 存在）。",
    );
  }

  const log: string[] = [];
  const record = (prefix: string) => (chunk: Buffer) => {
    for (const line of chunk.toString("utf8").split(/\r?\n/)) {
      if (line.trim() === "") continue;
      log.push(`${prefix} ${line}`);
      if (log.length > LOG_LIMIT) log.shift();
    }
  };

  const child = spawn(
    python,
    ["scripts/run_worker.py", "--poll-interval", "1"],
    {
      cwd: repoRoot,
      env: { ...process.env, ...backendEnv() },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    },
  );
  child.stdout?.on("data", record("[worker]"));
  child.stderr?.on("data", record("[worker:err]"));

  writeFileSync(PID_FILE, String(child.pid ?? 0), "utf8");

  const pid = child.pid;
  if (pid === undefined) {
    throw new Error("worker 启动失败：拿不到 pid");
  }

  // 给数据库连接与 embedder 装配留出时间；这段窗口里退出的一定是启动失败。
  await new Promise((resolve) => setTimeout(resolve, 2500));
  if (child.exitCode !== null) {
    throw new Error(
      `worker 启动后立刻退出（code=${String(child.exitCode)}）。最后几行日志：\n${log.join("\n") || "(无输出)"}`,
    );
  }

  return { pid, owned: true };
}

/** 停掉 pid 文件里记录的 worker；没记录就什么都不做。 */
export function stopWorker(): void {
  const pid = readPidFile();
  if (pid === null) return;

  if (process.platform === "win32") {
    // /T 连子进程一起杀：worker 内部若起了线程池进程，只杀父进程会留下孤儿。
    spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true });
  } else {
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      // 已经没了。
    }
  }

  try {
    unlinkSync(PID_FILE);
  } catch {
    // pid 文件可能已被清掉。
  }
}
