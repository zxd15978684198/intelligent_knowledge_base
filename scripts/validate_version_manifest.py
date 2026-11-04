#!/usr/bin/env python3
"""校验 release/agent_versions.json：字段齐全、路径在项目根内、摘要与磁盘一致。

生成物：由 gatekeeper 的 lib/manifest.js 生成，不要手改；组件集合或摘要变化时重新生成。
仅使用标准库，可直接用 uv run --locked python scripts/validate_version_manifest.py 运行。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 与插件常量 MANIFEST_PATH 一致；改路径时两处一起改。
MANIFEST_PATH = PROJECT_ROOT / "release" / "agent_versions.json"

# 本项目真实拥有的组件（生成时探测得出）。清单必须为每个组件写版本；
# 其中带 path 的组件还要通过路径与摘要检查，其余只核对是否存在。
PROJECT_COMPONENTS = [
    "evaluation",
]

# 顶层四类信息，缺任何一个都说明生成流程漏了一步。
REQUIRED_TOP_LEVEL = ("schema_version", "service", "runtime", "agent")

# 分块计算摘要：索引、权重这类大文件不必整体读进内存。
SHA256_CHUNK_BYTES = 64 * 1024


class ManifestValidationError(ValueError):
    """版本清单不完整，或与仓库中的文件不一致。"""


def _sha256(file_path: Path) -> str:
    """分块计算文件摘要，避免把大文件一次性读入内存。"""

    digest = hashlib.sha256()
    with file_path.open("rb") as file:
        for block in iter(lambda: file.read(SHA256_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_mapping(value: object, field_name: str) -> dict[str, Any]:
    """把运行时类型检查集中在一处，错误信息才有统一的形状。"""

    if not isinstance(value, dict):
        raise ManifestValidationError(f"{field_name} 必须是 JSON 对象")
    return value


def validate_manifest(manifest_path: Path, project_root: Path) -> dict[str, Any]:
    """读取并校验清单；通过时返回解析后的对象。

    三件事必须同时成立，发布证据才可信：
    1. 顶层四类信息齐全，且 PROJECT_COMPONENTS 中每个组件都有版本记录；
    2. 清单引用的文件都在项目根目录内（先 resolve 再 is_relative_to，所以 ../
       和指向根外的软链接都过不去）；
    3. 文件当前摘要与清单记录一致 —— 不一致只报告，不提供自动刷新。
    """

    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestValidationError(f"无法读取版本清单：{exc}") from exc

    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(f"版本清单不是合法 JSON：{exc}") from exc

    manifest = _require_mapping(raw, "清单根节点")
    for key in REQUIRED_TOP_LEVEL:
        if key not in manifest:
            raise ManifestValidationError(f"缺少必填字段：{key}")

    agent = _require_mapping(manifest["agent"], "agent")
    missing = [name for name in PROJECT_COMPONENTS if name not in agent]
    if missing:
        raise ManifestValidationError("agent 缺少组件版本：" + "、".join(missing))

    root = project_root.resolve()
    for name, value in agent.items():
        # 版本型组件（模型标识、Skill 清单等）没有仓库文件可校验。
        if not isinstance(value, dict):
            continue
        has_path = "path" in value
        has_sha256 = "sha256" in value
        # 半对字段一律拒绝：只有 sha256 没有 path 时，那个摘要永远不会被核对。
        if has_path != has_sha256:
            raise ManifestValidationError(f"{name} 的 path 和 sha256 必须成对出现")
        if not has_path:
            continue
        relative_path = value.get("path")
        expected = value.get("sha256")
        if not isinstance(relative_path, str) or not isinstance(expected, str):
            raise ManifestValidationError(f"{name} 的 path 和 sha256 必须是字符串")
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root):
            raise ManifestValidationError(f"{name} 的路径越过了项目根目录：{relative_path}")
        if not candidate.is_file():
            raise ManifestValidationError(f"{name} 引用的文件不存在：{relative_path}")
        if _sha256(candidate) != expected:
            raise ManifestValidationError(f"{name} 摘要不一致：请审核变更后更新版本和 SHA-256")

    return manifest


def main(argv: list[str] | None = None) -> int:
    """返回 0 表示清单可以继续往下游走，返回 1 表示发布证据不可信。"""

    parser = argparse.ArgumentParser(description="校验 Agent 版本清单")
    parser.add_argument(
        "--root",
        default=None,
        help="项目根目录，默认取脚本所在仓库的根目录",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.root).resolve() if args.root else PROJECT_ROOT
    manifest_path = project_root / MANIFEST_PATH.relative_to(PROJECT_ROOT)
    try:
        manifest = validate_manifest(manifest_path, project_root)
    except ManifestValidationError as exc:
        print(f"版本清单校验失败：{exc}")
        return 1

    service = manifest["service"]
    print(f"版本清单校验通过：{service['name']} {service['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
