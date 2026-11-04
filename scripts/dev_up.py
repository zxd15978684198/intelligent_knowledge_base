"""一键启动本地开发栈：数据库探活 → API → Worker → 前端，探活通过后打印可用地址。

为什么需要它：`README.md` 原本是四条分步命令（起库 / 迁移 / 起 API / 起 Worker / 起前端），
漏一步就落进"页面能打开但列表恒空"的半死状态；而 401、502、白屏、空列表在浏览器里
长得几乎一样，排查成本远高于写这个脚本。

脚本只做装配与探活，不改任何配置、不含业务逻辑：

1. 配置只有一份来源——`pyknora.core.settings.settings`（它自己读环境变量与 dotenv）；
2. 端口沿用前端计划的约定：API 8010、前端 3000，可用参数覆盖；
3. 三个子进程都继承本进程环境，前端额外注入 `API_PROXY_TARGET`，让 Next 的 rewrite
   指向本次启动的 API；本地 embedding 默认加 `HF_HUB_OFFLINE=1`（不加时离线机器上
   `transformers` 会对每个文件重试 5 次，实测启动卡 2 分钟以上，现象是"端口连不上"）；
4. 探活失败时把该进程最近输出打出来，并把子进程全部收掉，不留孤儿占端口。

**数据库端口不硬信配置**：配置写 5433、机器上库却在 5434 这种错位真实发生过
（本机就是：`settings.database_url` 指 5433，而容器 `pyknora-pg-p0` 映射 5434）。
此时 API 的 `/health` 照样返回 ok（它不查库），但任何读库请求都会 500，所以预检先探测
配置端口，不通就在 5432/5433/5434 里找真正能连上库的那个，找到就**只对子进程**注入
`PYKNORA_DATABASE_URL`，并在屏幕上明确告警，不静默改配置文件。

用法::

    uv run python scripts/dev_up.py                  # 起全套，Ctrl-C 一起停
    uv run python scripts/dev_up.py --check          # 只体检，不启动任何进程
    uv run python scripts/dev_up.py --no-web         # 只起后端（前端另行 pnpm dev）
    uv run python scripts/dev_up.py --no-worker      # 不起摄取 Worker
    uv run python scripts/dev_up.py --start-db       # 库没起时尝试 docker start
    uv run python scripts/dev_up.py --api-port 8011 --web-port 3001

幂等：API/前端已在跑时只探活复用，不重复起进程，因此可以反复执行。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = REPO_ROOT / "web"

#: 数据库端口探测顺序：配置的端口优先，其余按仓库里出现过的映射习惯兜底
_DB_PORT_CANDIDATES = (5432, 5433, 5434)


# --------------------------------------------------------------------------- #
# 探活小工具
# --------------------------------------------------------------------------- #
def _port_free(port: int) -> bool:
    """端口能否被本进程绑定（True = 空闲）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_get(url: str, timeout: float = 3.0) -> tuple[int, bytes]:
    """返回 (status, body)。4xx 也算拿到响应，只有连不上才抛。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _api_healthy(port: int) -> bool:
    """是不是一个已经就绪的 PyKnora API（用于幂等复用，避免重复起进程）。"""
    try:
        status, body = _http_get(f"http://127.0.0.1:{port}/health", timeout=2.0)
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False
    if status != 200:
        return False
    with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
        return json.loads(body).get("status") == "ok"
    return False


def _web_ready(port: int) -> bool:
    try:
        status, _ = _http_get(f"http://127.0.0.1:{port}/login", timeout=10.0)
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False
    return status in (200, 307, 308)


def _wait(predicate, *, deadline_s: float, label: str) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if predicate():
            print(f"  [ok] {label}")
            return True
        time.sleep(1.0)
    print(f"  [!!] {label} 在 {deadline_s:.0f}s 内未就绪")
    return False


def _docker(*args: str) -> str | None:
    if shutil.which("docker") is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - 参数由本模块构造
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _pyknora_containers(include_stopped: bool = False) -> list[str]:
    """pyknora* 容器名；只用于"库连不上"时给线索。

    `include_stopped=True` 必须加 `-a`：库停着时 `docker ps` 看不见它，
    `--start-db` 就成了空操作（曾因此误报"没有可用的库"）。
    """
    args = ["ps", *(["-a"] if include_stopped else []), "--filter", "name=pyknora"]
    out = _docker(*args, "--format", "{{.Names}}")
    return [line.strip() for line in (out or "").splitlines() if line.strip()]


def _split_db_url(url: str) -> tuple[str, int, str]:
    """`postgresql+asyncpg://u:p@host:port/db` → (host, port, dbname)。"""
    tail = url.rsplit("@", 1)[-1]
    host_port, _, dbname = tail.partition("/")
    host, _, port = host_port.partition(":")
    try:
        port_int = int(port or "5432")
    except ValueError:
        port_int = 5432
    return host or "127.0.0.1", port_int, dbname or "pyknora"


def _listener_pids(port: int) -> list[int]:
    """谁在 LISTEN 这个端口。Windows 用 netstat -ano，其他平台用 lsof。"""
    pids: list[int] = []
    if sys.platform == "win32":
        try:
            out = subprocess.run(  # noqa: S603,S607 - netstat 是系统自带
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(f":{port}") and "LISTEN" in parts[3]:
                with contextlib.suppress(ValueError):
                    pids.append(int(parts[4]))
    else:
        try:
            out = subprocess.run(  # noqa: S603,S607
                ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        for line in out.splitlines():
            with contextlib.suppress(ValueError):
                pids.append(int(line.strip()))
    return sorted(set(pids))


def _kill_listener(port: int) -> None:
    """停掉占着该端口的进程树（含 pnpm → node 的子进程）。"""
    for pid in _listener_pids(port):
        print(f"  [..] 停止占用 {port} 的进程 pid={pid}")
        if sys.platform == "win32":
            subprocess.run(  # noqa: S603,S607
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
            )
        else:
            with contextlib.suppress(OSError):
                os.kill(pid, 15)
    time.sleep(2)


# --------------------------------------------------------------------------- #
# 子进程管理
# --------------------------------------------------------------------------- #
def _resolve_cmd(cmd: list[str]) -> list[str]:
    """把 `pnpm` 这类 Windows 上是 `.cmd` 垫片的命令解析成可被 CreateProcess 执行的形式。

    `subprocess.Popen(["pnpm", ...])` 在 Windows 上直接抛 `WinError 2`：CreateProcess
    只认 PE 可执行文件，而 `pnpm` 实际是 `pnpm.cmd` 批处理，必须经 `cmd /c` 启动。
    """
    if sys.platform != "win32":
        return cmd
    found = shutil.which(cmd[0])
    if found is None:
        return cmd
    if found.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", *cmd]
    return [found, *cmd[1:]]


_LOG_DIR = REPO_ROOT / ".tmp_ref" / "dev_up"
_LOG_FILES: dict[str, object] = {}


def _spawn(args: list[str], *, cwd: Path, env: dict[str, str], name: str) -> subprocess.Popen[str]:
    """启动子进程，输出**重定向到日志文件**。

    这里是踩过的坑：早期版本用 `stdout=subprocess.PIPE` 且没人持续读，
    管道缓冲区（Windows 上 64 KB）写满后子进程就阻塞在 write 上——
    uvicorn 起完 Banner 就"卡死"，Next dev 编译日志写满后直接不响应，
    现象是「端口在、页面打不开」和「服务莫名消失」。
    落盘后既不会堵，也能在失败时把最后几十行打给用户看。
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _LOG_DIR / f"{name}.log"
    handle = log_path.open("w", encoding="utf-8", errors="replace")
    _LOG_FILES[name] = handle
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    print(f"  [..] 启动 {' '.join(args[:3])} …（日志 {log_path.relative_to(REPO_ROOT)}）")
    return subprocess.Popen(  # noqa: S603 - 参数由本脚本构造
        _resolve_cmd(args),
        cwd=str(cwd),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )


def _stop(procs: dict[str, subprocess.Popen[str]]) -> None:
    for name, proc in procs.items():
        if proc.poll() is not None:
            continue
        print(f"  [..] 停止 {name} (pid {proc.pid})")
        if sys.platform == "win32":
            # 前端是 pnpm → node 的进程树，只 terminate 父进程会在 3000 留下子进程
            subprocess.run(  # noqa: S603,S607
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)


def _tail(name: str, *, lines: int = 25) -> str:
    path = _LOG_DIR / f"{name}.log"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    kept = text.splitlines()[-lines:]
    return "\n".join(kept)


# --------------------------------------------------------------------------- #
# 预检
# --------------------------------------------------------------------------- #
def _preflight(
    api_port: int,
    web_port: int,
    *,
    want_web: bool,
    want_worker: bool,
    start_db: bool,
    keep_existing: bool = False,
) -> tuple[bool, dict[str, str]]:
    """返回 (是否继续, 需注入子进程的环境变量)。"""
    from pyknora.core.settings import settings as app_settings

    env_overrides: dict[str, str] = {}

    print("[1/4] 预检")
    host, db_port, db_name = _split_db_url(app_settings.database_url)
    if _tcp_open(host, db_port):
        print(f"  [ok] 数据库 {host}:{db_port} 可连接（与配置一致）")
    else:
        found: int | None = None
        for candidate in (db_port, *_DB_PORT_CANDIDATES):
            if candidate != db_port and _tcp_open(host, candidate):
                found = candidate
                break
        if found is not None:
            head, _, tail = app_settings.database_url.partition("@")
            rest, _, dbpart = tail.partition("/")
            new_hostport = rest.rsplit(":", 1)[0] + f":{found}"
            env_overrides["PYKNORA_DATABASE_URL"] = f"{head}@{new_hostport}/{dbpart}"
            print(
                f"  [warn] 配置写 {host}:{db_port}，但该端口没有库；"
                f"实测 {host}:{found} 上有 {db_name}"
            )
            print(
                f"         本次启动只对子进程注入 PYKNORA_DATABASE_URL（端口 {found}），"
                "不改配置、不改代码"
            )
            print(f"         彻底修法：把 dotenv 里的端口改成 {found}，或把容器映射回 {db_port}")
        else:
            running = _pyknora_containers()
            all_containers = _pyknora_containers(include_stopped=True)
            if start_db and all_containers:
                # 优先用配置里的容器名：机器上常有多个 pyknora* 库容器，
                # 抓错一个（端口映射不同）照样连不上。
                target = (
                    app_settings.db_container
                    if app_settings.db_container in all_containers
                    else all_containers[0]
                )
                print(f"  [..] 尝试 docker start {target} …")
                _docker("start", target)
                if _wait(lambda: _tcp_open(host, db_port), deadline_s=30, label="数据库就绪"):
                    running = []
                else:
                    running = _pyknora_containers()
            if not _tcp_open(host, db_port):
                print(f"  [!!] 数据库 {host}:{db_port} 连不上，附近端口也没有可用库")
                if running:
                    print(f"       在跑的 pyknora 容器：{', '.join(running)}（端口映射可能不同）")
                elif all_containers:
                    print(f"       已停止的 pyknora 容器：{', '.join(all_containers)}")
                    print("       加 --start-db 让本脚本 docker start")
                print("       起库（容器名/端口取自当前配置）：")
                print(
                    f"         docker run -d --name {app_settings.db_container} -p {db_port}:5432 ^"
                )
                print(f"           -e POSTGRES_PASSWORD=dev -e POSTGRES_DB={db_name} ^")
                print("           pgvector/pgvector:pg16")
                return False, env_overrides

    if not want_worker:
        print("  [..] --no-worker：跳过 Worker")
    if _api_healthy(api_port):
        if env_overrides and not keep_existing:
            # 端口上的 API 用的是"配置里的死端口"，健康检查照样 ok（/health 不查库），
            # 但登录/列表会 500。这种实例必须换掉，否则一键启动等于没启动。
            print(
                f"  [warn] {api_port} 上已有 API，但它连的是配置里的库端口"
                "（上面的错位），登录会 500"
            )
            print("         → 停掉它并按实测端口重启（不想动旧进程加 --keep-existing）")
            _kill_listener(api_port)
        elif env_overrides:
            print(
                f"  [..] --keep-existing：保留 {api_port} 上的旧 API"
                "（它连的库端口是错的，登录会 500）"
            )
        else:
            print(f"  [ok] {api_port} 上已有就绪的 API（跳过启动，直接复用）")
    elif not _port_free(api_port):
        print(f"  [!!] API 端口 {api_port} 被别的进程占着，且不是 PyKnora API")
        print(f"       查占用：netstat -ano | findstr :{api_port}")
        return False, env_overrides

    if want_web:
        if _web_ready(web_port):
            print(f"  [ok] {web_port} 上已有可用的前端（跳过启动，直接复用）")
        elif not _port_free(web_port):
            print(f"  [!!] 前端端口 {web_port} 被别的进程占着")
            print(f"       查占用：netstat -ano | findstr :{web_port}")
            return False, env_overrides

    return True, env_overrides


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="一键启动 PyKnora 本地开发栈")
    parser.add_argument("--api-port", type=int, default=8010)
    parser.add_argument("--web-port", type=int, default=3000)
    parser.add_argument("--no-web", action="store_true", help="不起前端")
    parser.add_argument("--no-worker", action="store_true", help="不起摄取 Worker")
    parser.add_argument("--start-db", action="store_true", help="库没起时尝试 docker start")
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="不替换端口上已有的进程（即使它连的库端口不对）",
    )
    parser.add_argument("--check", action="store_true", help="只体检，不启动任何进程")
    parser.add_argument("--timeout", type=float, default=240.0, help="单服务探活上限（秒）")
    args = parser.parse_args(argv)

    want_web = not args.no_web
    want_worker = not args.no_worker

    try:
        ok, env_overrides = _preflight(
            args.api_port,
            args.web_port,
            want_web=want_web,
            want_worker=want_worker,
            start_db=args.start_db,
            keep_existing=args.keep_existing,
        )
    except Exception as exc:  # 配置读不出来时给人话，不抛 traceback
        print(f"  [!!] 读取配置失败：{exc}")
        return 2
    if not ok:
        return 2
    if args.check:
        print("\n体检通过（--check 不启动任何进程）。")
        return 0

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env["API_PROXY_TARGET"] = f"http://127.0.0.1:{args.api_port}"
    env.update(env_overrides)

    procs: dict[str, subprocess.Popen[str]] = {}
    reused: list[str] = []

    def cleanup() -> None:
        _stop(procs)

    try:
        print("[2/4] 启动 API")
        if _api_healthy(args.api_port):
            reused.append(f"  后端      http://127.0.0.1:{args.api_port}/health（复用）")
        else:
            procs["api"] = _spawn(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "pyknora.api.main:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.api_port),
                ],
                cwd=REPO_ROOT,
                env=env,
                name="api",
            )
            if not _wait(
                lambda: _api_healthy(args.api_port),
                deadline_s=args.timeout,
                label=f"API {args.api_port} 就绪",
            ):
                print(f"\n----- api 最近输出 -----\n{_tail('api')}")
                return 1

        if want_worker:
            print("[3/4] 启动 Worker")
            procs["worker"] = _spawn(
                [sys.executable, "scripts/run_worker.py"], cwd=REPO_ROOT, env=env, name="worker"
            )
        else:
            print("[3/4] 跳过 Worker")

        if want_web:
            if _web_ready(args.web_port):
                reused.append(f"  前端      http://127.0.0.1:{args.web_port}/login（复用）")
            else:
                print("[3/4] 启动前端（首次编译约 10–20s）")
                procs["web"] = _spawn(["pnpm", "dev"], cwd=WEB_ROOT, env=env, name="web")
                if not _wait(
                    lambda: _web_ready(args.web_port),
                    deadline_s=args.timeout,
                    label=f"前端 {args.web_port} 就绪",
                ):
                    print(f"\n----- web 最近输出 -----\n{_tail('web')}")
                    return 1

        print("[4/4] 探活")
        db_line = "  数据库    与配置一致"
        if env_overrides:
            h, p, _ = _split_db_url(env_overrides["PYKNORA_DATABASE_URL"])
            db_line = f"  数据库    {h}:{p}（配置端口不对，已对子进程覆盖）"
        print(
            "\n全部就绪：\n"
            f"  前端      http://127.0.0.1:{args.web_port}/login\n"
            f"  后端      http://127.0.0.1:{args.api_port}/health\n"
            f"  API 文档  http://127.0.0.1:{args.api_port}/docs\n"
            f"  同源代理  http://127.0.0.1:{args.web_port}/api/v1/*（浏览器只认这个）\n"
            f"{db_line}" + "".join("\n" + line for line in reused) + "\n"
            "\n按 Ctrl-C 一起停止。"
        )
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C，正在停止…")
        cleanup()
        return 0
    except BaseException:
        # 启动中途失败（探活超时 / 配置错）：把起过的子进程收掉，不留孤儿占端口
        cleanup()
        raise

    try:
        while True:
            for name, proc in procs.items():
                if proc.poll() is not None:
                    print(f"\n[!!] {name} 意外退出（code={proc.returncode}）：\n{_tail(name)}")
                    return 1
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C，正在停止…")
        return 0
    finally:
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
