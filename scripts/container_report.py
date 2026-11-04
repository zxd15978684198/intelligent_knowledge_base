#!/usr/bin/env python3
# ruff: noqa: E501
# 生成物：由 gatekeeper 生成，不要手改。
# 来源：lib/container-report.js（py-apply 的 container-report 目标）。
"""容器体检 6 项，默认只报告。

1. 镜像大小      2. 镜像层数      3. 默认用户是否 root
4. 开发依赖泄漏（运行中的容器里 pytest/ruff/mypy 能否 import）
5. 有无 .dockerignore             6. 是否多阶段构建

失败策略：
- docker 不可达（CLI 缺失、daemon 未启动、socket 被拒）是环境失败：打印 environment 段并 exit 2，
  不把 4 项 docker 检查逐项报 skip —— 那是假绿。
- docker 可达：默认只报告 exit 0；只有生成时配置了 imageMaxSizeMb 且实测镜像超过它才 exit 1。
- 无 compose 配置 / 无运行中容器 / 无镜像时，对应项打印 skip 而不是抛异常。
仅使用标准库，兼容 Python 3.8+（不依赖 tomllib）。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# 生成时从 pyproject.toml 的 [tool.gatekeeper.image].max_size_mb 读到的上限。
# None = 不设限（只报告）。该配置变化后需要重新生成本脚本。
IMAGE_MAX_SIZE_MB = None
DOCKERFILE_NAMES = ("Dockerfile", "dockerfile", "Containerfile")

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_SKIP = "skip"
STATUS_FAIL = "fail"

# 层数超过这个数字只 warn，不失败：不同基础镜像的合理层数差别很大。
LAYER_WARN_THRESHOLD = 20

# 探测 daemon 要短：daemon 卡死时不该把 CI 的 15 分钟预算耗在 version 上。
DOCKER_PROBE_TIMEOUT = 20

PROBE_CODE = (
    "import importlib.util; "
    "print(','.join(m for m in ('pytest', 'ruff', 'mypy') if importlib.util.find_spec(m) is not None))"
)


def project_root(explicit):
    """The project root: --root when given, else the directory above scripts/."""
    if explicit:
        return Path(explicit).resolve()
    return Path(__file__).resolve().parent.parent


def run(*args, cwd=None, timeout=120):
    """Run a command, returning None when it is missing or hangs.

    None is deliberately different from a non-zero exit: a command that cannot be
    launched (or that times out) is not a reportable image detail. Only the daemon
    probe in docker_reachable() turns that into an environment failure.
    """
    try:
        return subprocess.run(
            list(args),
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def first_line(text):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def docker_reachable():
    """(True, server version) when the daemon answers; (False, detail) otherwise.

    A docker on PATH is not enough: the daemon may be down, or the caller may be
    denied the socket (Docker Desktop's npipe on Windows, a rootless socket, a CI
    runner without /var/run/docker.sock). Both are environment failures and must be
    reported once, not smeared into four passing skips.
    """
    result = run(
        "docker", "version", "--format", "{{.Server.Version}}", timeout=DOCKER_PROBE_TIMEOUT
    )
    if result is None:
        return False, "找不到 docker CLI（PATH 里没有 docker，或命令超时）"
    if result.returncode != 0:
        detail = first_line(result.stderr) or first_line(result.stdout)
        if detail == "":
            detail = "docker version 退出码 " + str(result.returncode)
        return False, detail
    version = first_line(result.stdout)
    if version == "":
        return False, "docker version 没有返回服务端版本（daemon 无响应）"
    return True, version


COMPOSE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")


def compose_file(root):
    """The compose file in this project, or None when the project has none."""
    for name in COMPOSE_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def compose_service(root):
    """The first service declared by the compose config, running or not.

    The config subcommand is client-side: it reads the file and prints the
    resolved service list, so a stopped stack still names its services. Asking
    for running services only would report "no compose" for a perfectly good file
    whose containers are simply down — the exact false statement this check
    exists to avoid.
    """
    if compose_file(root) is None:
        return ""
    result = run("docker", "compose", "config", "--services", cwd=root)
    if result is None or result.returncode != 0:
        return ""
    return first_line(result.stdout)


def compose_image(root):
    """The first image named by the compose config, or "" when it cannot be read."""
    result = run("docker", "compose", "config", "--images", cwd=root)
    if result is None or result.returncode != 0:
        return ""
    return first_line(result.stdout)


def container_id(root, service):
    result = run("docker", "compose", "ps", "-q", service, cwd=root)
    if result is None or result.returncode != 0:
        return ""
    return first_line(result.stdout)


def inspect(root, target, template):
    """docker inspect --format; None when the command failed, "" when it printed nothing.

    target is a container id or an image tag: both are valid for docker inspect,
    which is what lets the report describe a built image whose stack is stopped.
    """
    result = run("docker", "inspect", "--format", template, target, cwd=root)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip()


def check_image(root, target, checks, missing):
    """Image size and layer count for a container id or an image reference."""
    if target == "":
        checks.append((STATUS_SKIP, "镜像大小", missing))
        checks.append((STATUS_SKIP, "镜像层数", missing))
        return
    image = target
    if not target.startswith("sha256:"):
        # A container id names its image through .Image; an image reference is
        # already the image. Trying both keeps one code path for running/stopped.
        resolved = inspect(root, target, "{{.Image}}")
        if resolved not in (None, ""):
            image = resolved

    raw_size = inspect(root, image, "{{.Size}}")
    if raw_size is None or raw_size == "":
        checks.append((STATUS_SKIP, "镜像大小", "docker inspect 没有返回 .Size"))
    else:
        try:
            size_mb = int(raw_size) / (1024.0 * 1024.0)
        except ValueError:
            checks.append((STATUS_SKIP, "镜像大小", f"镜像大小不是字节数：{raw_size!r}"))
        else:
            if IMAGE_MAX_SIZE_MB is None:
                checks.append((STATUS_OK, "镜像大小", f"{size_mb:.1f} MB（未配置上限，只报告）"))
            elif size_mb > IMAGE_MAX_SIZE_MB:
                checks.append(
                    (STATUS_FAIL, "镜像大小", f"{size_mb:.1f} MB 超过上限 {IMAGE_MAX_SIZE_MB} MB")
                )
            else:
                checks.append(
                    (STATUS_OK, "镜像大小", f"{size_mb:.1f} MB（上限 {IMAGE_MAX_SIZE_MB} MB）")
                )

    layers = inspect(root, image, "{{len .RootFS.Layers}}")
    if layers is None or layers == "":
        checks.append((STATUS_SKIP, "镜像层数", "docker inspect 没有返回 .RootFS.Layers"))
        return
    try:
        count = int(layers)
    except ValueError:
        checks.append((STATUS_SKIP, "镜像层数", f"层数不是整数：{layers!r}"))
        return
    if count > LAYER_WARN_THRESHOLD:
        checks.append(
            (STATUS_WARN, "镜像层数", f"{count} 层（超过 {LAYER_WARN_THRESHOLD} 层，考虑合并 RUN）")
        )
    else:
        checks.append((STATUS_OK, "镜像层数", f"{count} 层"))


def check_user(root, target, checks, missing):
    """Default user from a container's or an image's Config.User."""
    if target == "":
        checks.append((STATUS_SKIP, "默认用户", missing))
        return
    user = inspect(root, target, "{{.Config.User}}")
    if user is None:
        checks.append((STATUS_SKIP, "默认用户", missing))
        return
    # An empty Config.User is Docker's default: root. "0"/"root" are the same thing.
    if user == "" or user.strip().lower() in ("root", "0", "0:0"):
        checks.append((STATUS_WARN, "默认用户", "以 root 运行（Dockerfile 没有非 root 的 USER）"))
    else:
        checks.append((STATUS_OK, "默认用户", f"USER={user}"))


def exec_probe(root, service):
    """The dev modules importable inside the running container, or None when it cannot tell."""
    for interpreter in ("python", "python3"):
        result = run(
            "docker", "compose", "exec", "-T", service, interpreter, "-c", PROBE_CODE, cwd=root
        )
        if result is not None and result.returncode == 0:
            return result.stdout.strip()
    return None


def check_dev_deps(root, service, checks):
    if service == "":
        checks.append((STATUS_SKIP, "开发依赖泄漏", "未找到 compose 服务（没有 compose 文件）"))
        return
    found = exec_probe(root, service)
    if found is None:
        checks.append(
            (
                STATUS_SKIP,
                "开发依赖泄漏",
                "服务未启动，无法探测运行中的容器（先 docker compose up -d --wait）",
            )
        )
        return
    names = [name for name in found.split(",") if name]
    if names:
        checks.append(
            (
                STATUS_WARN,
                "开发依赖泄漏",
                f"容器里能 import: {', '.join(names)}（运行时镜像不应装开发依赖）",
            )
        )
    else:
        checks.append((STATUS_OK, "开发依赖泄漏", "pytest/ruff/mypy 都不可 import"))


def dockerfile_of(root):
    for name in DOCKERFILE_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def check_dockerignore(root, checks):
    if (root / ".dockerignore").is_file():
        checks.append((STATUS_OK, ".dockerignore", "存在"))
    else:
        checks.append((STATUS_WARN, ".dockerignore", "缺失：构建上下文会带上 .git/.venv 等目录"))


def check_multistage(root, checks):
    dockerfile = dockerfile_of(root)
    if dockerfile is None:
        checks.append((STATUS_SKIP, "多阶段构建", "没有找到 Dockerfile"))
        return
    try:
        text = dockerfile.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        checks.append((STATUS_SKIP, "多阶段构建", f"读不到 {dockerfile.name}：{error}"))
        return
    stages = [line for line in text.splitlines() if line.strip().upper().startswith("FROM ")]
    if len(stages) >= 2:
        checks.append((STATUS_OK, "多阶段构建", f"{len(stages)} 个 FROM 阶段"))
    elif len(stages) == 1:
        checks.append((STATUS_WARN, "多阶段构建", "单阶段构建：构建工具会留在运行时镜像里"))
    else:
        checks.append((STATUS_SKIP, "多阶段构建", "Dockerfile 里没有 FROM 行"))


def main(argv):
    parser = argparse.ArgumentParser(
        description="容器镜像体检：默认只报告，只有配置了大小上限且超限才失败。"
    )
    parser.add_argument("--root", default=None, help="项目根目录；默认为本脚本的上一级目录")
    args = parser.parse_args(argv)
    root = project_root(args.root)

    print("容器体检（默认只报告）：" + str(root))

    reachable, detail = docker_reachable()
    if not reachable:
        print("[environment] 镜像体检未完成：docker 不可达（4 项 docker 检查均未执行）。")
        print("[environment] Cannot connect to the Docker daemon: " + detail)
        print(
            "[environment] 这是环境问题，不是镜像问题：确认 Docker Desktop/daemon 已启动、当前用户可访问 docker socket 后重跑。"
        )
        return 2
    print("docker 服务端版本：" + detail)

    checks = []
    compose = compose_file(root)
    service = compose_service(root)
    if service == "":
        detail = (
            "未找到 compose 服务（没有 compose 文件）"
            if compose is None
            else "compose 文件存在，但 docker compose config 读不到服务（检查服务定义）"
        )
        for name in ("镜像大小", "镜像层数", "默认用户", "开发依赖泄漏"):
            checks.append((STATUS_SKIP, name, detail))
    else:
        cid = container_id(root, service)
        print("compose 服务：" + service + "（容器 " + (cid[:12] if cid != "" else "未运行") + "）")
        if cid != "":
            check_image(root, cid, checks, "拿不到容器对应的镜像 ID")
            check_user(root, cid, checks, "docker inspect 失败")
            check_dev_deps(root, service, checks)
        else:
            # The stack is stopped, but the compose config still names an image and
            # docker image inspect can describe that image. Three of the six checks
            # are properties of the image, not of a running process, so a stopped
            # stack must not turn them into skips.
            image = compose_image(root)
            built = image != "" and inspect(root, image, "{{.Id}}") not in (None, "")
            if built:
                check_image(root, image, checks, "docker image inspect 失败")
                check_user(root, image, checks, "docker image inspect 失败")
                checks.append(
                    (
                        STATUS_SKIP,
                        "开发依赖泄漏",
                        "服务未启动，无法探测运行中的容器（先 docker compose up -d --wait）",
                    )
                )
            else:
                detail = "服务未启动且镜像未构建（" + service + "）"
                for name in ("镜像大小", "镜像层数", "默认用户", "开发依赖泄漏"):
                    checks.append((STATUS_SKIP, name, detail))
    check_dockerignore(root, checks)
    check_multistage(root, checks)
    for status, name, detail in checks:
        print(f"[{status}] {name}: {detail}")

    failed = [entry for entry in checks if entry[0] == STATUS_FAIL]
    if failed:
        print(f"结果：{len(failed)} 项超限（镜像大小是唯一会让本脚本失败的项目）。")
        return 1
    print("结果：没有超限项；warn 与 skip 都不会让本次运行失败。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
