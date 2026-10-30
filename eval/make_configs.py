#!/usr/bin/env python
"""生成 `eval/baseline_config.yaml` 与 `eval/candidate_config.yaml`。

为什么是生成器而不是手写两份 yaml：

- 手写必漂。`app_commit` 每次提交都变，`snapshot` 里的版本指针每次重灌语料都变；
  抄错一位，跑出来的数就不再对应任何一次可复现的构建。
- 口径要能追源。每个值都取自一个可指认的位置（来源表见 `eval/README.md`），
  没有一个是"我记得大概是"。
- 公平性守卫（`run_eval.py --compare-config`）只认**恰好一个主因素**不同。两份
  yaml 由同一段代码发两份字典，差别只由 `retrieval_strategy` 这一个参数决定，
  "顺手多改一个参数"在结构上就不可能发生。

候选**不写** `candidate_k`：候选池倍数由服务端
`services/retrieval/search.py::_resolve_candidate_k` 结算（`candidate` -> `top_k * 4`，
封顶 `MAX_TOP_K`），写进 yaml 等于把实现细节复制一份到配置里，两处迟早分叉；
`pyknora.core.settings` 里也没有 `retrieval_candidate_k` 这个字段。

用法::

    uv run --frozen python eval/make_configs.py          # 生成两份 yaml
    uv run --frozen python eval/make_configs.py --check  # 用 run_eval 的 loader 校验
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = REPO_ROOT / "eval"
RUNNER_PATH = EVAL_DIR / "run_eval.py"
SETUP_PATH = REPO_ROOT / ".tmp_ref" / "eval_setup.json"
DATASET_PATH = EVAL_DIR / "eval_set_v0.1.jsonl"
BASELINE_PATH = EVAL_DIR / "baseline_config.yaml"
CANDIDATE_PATH = EVAL_DIR / "candidate_config.yaml"
ANSWER_PATH = REPO_ROOT / "src" / "pyknora" / "services" / "rag" / "answer.py"

#: 配置里的数据路径按「仓库根」写（runner 的 `resolve_dataset_path` 依次试
#: cwd / 配置目录 / 其上一级），换机器、换启动目录都不用改。
DATASET_RELATIVE = DATASET_PATH.relative_to(REPO_ROOT).as_posix()

#: 两份配置共用的实验标识。**不能**靠它区分 A/B：它不在
#: `run_eval.PRIMARY_FACTOR_FIELDS` 里，两份 yaml 只要在这里不同，
#: `--compare-config` 就会以"非主因素差异"打回（exit 2）。
#: 两轮产物靠 `--out eval/results/base-<日期>` / `cand-<日期>` 分开，见 README。
EXPERIMENT_ID = "day03-rag-ab"

#: 评测口径（计划 §3.5）：两条链路的 top_k 写进配置，不取端点默认值
#: （`/query` 默认 10、`/chat` 默认 6，且本轮不改默认值）——否则"这一轮的
#: Recall@5"算的是别人的分母，A/B 立刻不可比。
#:
#: `chat_top_k=6` 与计划 §3.5 示例里的 5 不一致：6 是本轮任务书给的取值，
#: 两条链路各自一致地固定下来即可（A/B 两份都是 6，公平性不受影响）。
#: 若要回到计划口径，只改这一行常量并重跑生成器。
QUERY_TOP_K = 5
CHAT_TOP_K = 6

#: 本轮不启用 reranker（计划 §12.1 的四条理由）。显式写 `null` 而不是省掉：
#: "评估过、没启用"与"忘了填"在复盘时长得一样，只有写出来才分得清。
RERANKER: None = None

#: snapshot.documents[] 的字段（计划 §3.5）。少一个就报错，不拿 None 顶：
#: 版本指针一旦缺，报告里"命中的是不是现行版"就无法判定。
SNAPSHOT_DOCUMENT_FIELDS: tuple[str, ...] = (
    "document_id",
    "active_document_version_id",
    "active_build_id",
    "active_index_version",
    "chunks",
)

#: `--check` 打印的字段顺序：人读的是口径，不是整张摊平后的日志。
KEY_FIELDS: tuple[str, ...] = (
    "experiment_id",
    "app_commit",
    "prompt_version",
    "eval_dataset",
    "snapshot.workspace_id",
    "snapshot.kb_id",
    "chat_model",
    "chat_base_url",
    "chat_timeout_s",
    "embedding_model_key",
    "embedding_dim",
    "embedding_backend",
    "retrieval_strategy",
    "query_top_k",
    "chat_top_k",
    "reranker",
)

EXIT_OK = 0
EXIT_ENV = 1
EXIT_CONFIG = 2


class EnvError(Exception):
    """输入/环境缺失（不是配置不合格）。转成 exit 1，与 yaml 写错的 exit 2 分开。"""


def load_runner() -> ModuleType:
    """按路径加载 `eval/run_eval.py`，复用它的 loader / 校验 / 比对。

    不写 `import run_eval`：脚本从哪个目录启动不确定，`sys.path` 里未必有 `eval/`。
    也不在这里抄一份校验：抄的那一刻两侧就开始分叉，"生成器通过、runner 打回"
    这种最难查的错就是这么来的。
    """
    spec = importlib.util.spec_from_file_location("_pyknora_eval_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise EnvError(f"加载不了 runner：{RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    # 必须先登记进 sys.modules：run_eval 里的 `@dataclass(slots=True)` 会回查
    # `sys.modules[cls.__module__]` 取模块命名空间，没登记就 AttributeError。
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _require(value: object, what: str) -> Any:
    """取一个必须存在的值；缺失就抛，绝不回退到"看起来像"的默认值。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise EnvError(f"{SETUP_PATH} 缺少 {what}；先重跑 .tmp_ref/setup_eval_kb.py")
    return value


def load_setup() -> dict[str, Any]:
    """读 `.tmp_ref/eval_setup.json`（评测 KB 的真实快照，由 setup 脚本生成）。"""
    try:
        raw = json.loads(SETUP_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EnvError(f"{SETUP_PATH} 不存在；先跑 .tmp_ref/setup_eval_kb.py") from exc
    except json.JSONDecodeError as exc:
        raise EnvError(f"{SETUP_PATH} 不是合法 json：{exc}") from exc
    if not isinstance(raw, dict):
        raise EnvError(f"{SETUP_PATH} 顶层必须是对象，实际是 {type(raw).__name__}")
    return raw


def build_snapshot(setup: dict[str, Any]) -> dict[str, Any]:
    """把 setup 快照裁成配置里要的 `snapshot` 段（只留计划 §3.5 的字段）。"""
    documents = setup.get("documents")
    if not isinstance(documents, list) or not documents:
        raise EnvError(f"{SETUP_PATH} 的 documents 必须是非空列表")
    rows: list[dict[str, Any]] = []
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise EnvError(f"{SETUP_PATH} 的 documents[{index}] 必须是对象")
        rows.append(
            {
                name: _require(document.get(name), f"documents[{index}].{name}")
                for name in SNAPSHOT_DOCUMENT_FIELDS
            }
        )
    return {
        "workspace_id": _require(setup.get("workspace_id"), "workspace_id"),
        "kb_id": _require(setup.get("kb_id"), "kb_id"),
        "documents": rows,
    }


def prompt_version() -> str:
    """取 `answer.py` 的 `PROMPT_VERSION`：优先 import，失败才扫源码字面量。

    `prompt_version` 是报告里"这批答案出自哪版提示词"的唯一凭据，必须来自代码而
    不是手填（手填的那份在有人改提示词时不会跟着变，报告就骗人了）。

    为什么留一条扫源码的退路：`answer.py` 在工作区里正被其它任务并行修改，可能
    短暂处于"语法不完整"的状态；那时 import 会 `SyntaxError`，但常量本身是好的。
    退路只在 import 失败时启用，并且**每次都打一行警告**——静默换源的配置比报错更难查。
    """
    try:
        from pyknora.services.rag.answer import PROMPT_VERSION
    except (ImportError, SyntaxError) as exc:
        print(
            f"[gen] 注意：import answer 失败（{type(exc).__name__}），"
            f"改从 {ANSWER_PATH} 的源码字面量取 PROMPT_VERSION",
            file=sys.stderr,
        )
        return _prompt_version_from_source()
    return PROMPT_VERSION


def _prompt_version_from_source() -> str:
    try:
        text = ANSWER_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EnvError(f"{ANSWER_PATH} 不存在：PROMPT_VERSION 无处可取") from exc
    match = re.search(r'^PROMPT_VERSION\s*=\s*"([^"]+)"\s*$', text, re.MULTILINE)
    if match is None:
        raise EnvError(f'{ANSWER_PATH} 里找不到 `PROMPT_VERSION = "..."` 字面量')
    return match.group(1)


def collect_settings() -> dict[str, Any]:
    """从 `pyknora.core.settings` 的 settings 对象与代码常量取"随部署变"的字段。

    刻意不在 `eval/` 里读 `.env`：配置的唯一口径是 settings 对象，读文件等于造
    第二个真相来源——它还会把密钥带进评测进程的输出里。
    """
    try:
        from pyknora.core.settings import (
            RETRIEVAL_STRATEGY_BASELINE,
            RETRIEVAL_STRATEGY_CANDIDATE,
            settings,
        )
    except ImportError as exc:  # 未 sync / 依赖缺失，报成环境问题而不是配置问题
        raise EnvError(
            f"import pyknora.core.settings 失败（先 `uv sync --extra dev`）：{exc}"
        ) from exc
    return {
        "prompt_version": prompt_version(),
        "chat_model": settings.chat_model,
        "chat_base_url": settings.chat_base_url,
        "chat_timeout_s": settings.chat_timeout_s,
        "embedding_model_key": settings.embedding_model_key,
        "embedding_dim": settings.embedding_dim,
        "embedding_backend": settings.embedding_backend,
        "baseline_strategy": RETRIEVAL_STRATEGY_BASELINE,
        "candidate_strategy": RETRIEVAL_STRATEGY_CANDIDATE,
        "configured_strategy": settings.retrieval_strategy,
    }


def build_config(
    *,
    base: dict[str, Any],
    snapshot: dict[str, Any],
    strategy: str,
    head: str,
) -> dict[str, Any]:
    """发一份配置。字段顺序照计划 §3.5 的 15 个字段，方便和文档逐行对读。"""
    return {
        "experiment_id": EXPERIMENT_ID,
        "app_commit": head,
        "prompt_version": base["prompt_version"],
        "eval_dataset": DATASET_RELATIVE,
        "snapshot": snapshot,
        "chat_model": base["chat_model"],
        "chat_base_url": base["chat_base_url"],
        "chat_timeout_s": base["chat_timeout_s"],
        "embedding_model_key": base["embedding_model_key"],
        "embedding_dim": base["embedding_dim"],
        "embedding_backend": base["embedding_backend"],
        "retrieval_strategy": strategy,
        "query_top_k": QUERY_TOP_K,
        "chat_top_k": CHAT_TOP_K,
        "reranker": RERANKER,
    }


def build_both(runner: ModuleType) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """生成基线与候选两份字典；两者只在 `retrieval_strategy` 上不同。"""
    head = runner.git_head()
    if not head:
        raise EnvError("读不到 git HEAD（`git rev-parse HEAD` 失败）：app_commit 不许手填")
    snapshot = build_snapshot(load_setup())
    base = collect_settings()
    baseline = build_config(
        base=base, snapshot=snapshot, strategy=base["baseline_strategy"], head=head
    )
    candidate = build_config(
        base=base, snapshot=snapshot, strategy=base["candidate_strategy"], head=head
    )
    return baseline, candidate, base


def render_config(cfg: dict[str, Any], *, note: str) -> str:
    """yaml 文本 + 四行头注释（生成器、时间、来源、这一份的口径）。"""
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    header = (
        "# 由 eval/make_configs.py 生成，勿手改；改字段请改生成器后重跑。\n"
        f"# 生成时间：{stamp}\n"
        "# 来源：.tmp_ref/eval_setup.json（snapshot）、pyknora.core.settings、git HEAD\n"
        f"# {note}\n"
    )
    body = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
    return header + body


def print_key_fields(runner: ModuleType, cfg: dict[str, Any], path: Path) -> None:
    flat = runner.flatten_config(cfg)
    documents = (cfg.get("snapshot") or {}).get("documents") or []
    chunks = sum(int(item.get("chunks") or 0) for item in documents if isinstance(item, dict))
    print(f"\n[fields] {path}：")
    for key in KEY_FIELDS:
        if key in flat:
            print(f"  {key:28s} {flat[key]}")
    print(f"  {'snapshot.documents':28s} {len(documents)} 篇 / {chunks} 个切片")


def inspect_dataset(runner: ModuleType, cfg: dict[str, Any], path: Path) -> list[str]:
    """核对评测集本身：路径能解析、条数、类别分布、gold 与 snapshot 对得上。

    返回问题清单（空 = 通过）。这里不发任何 HTTP，只读文件。
    """
    problems: list[str] = []
    raw = str(cfg.get("eval_dataset") or "")
    resolved = runner.resolve_dataset_path(raw, path)
    if not resolved.exists():
        return [f"eval_dataset 解析不到文件：{raw} -> {resolved}"]
    try:
        lines = [line for line in resolved.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows = [json.loads(line) for line in lines]
    except json.JSONDecodeError as exc:
        return [f"{resolved} 不是合法 jsonl：{exc}"]

    categories = Counter(str(row.get("category")) for row in rows)
    expects = Counter(str(row.get("expect")) for row in rows)
    print(
        f"[dataset] {resolved}：{len(rows)} 条；category={dict(sorted(categories.items()))}；"
        f"expect={dict(sorted(expects.items()))}"
    )

    snapshot = cfg.get("snapshot") or {}
    known = {
        str(document.get("document_id"))
        for document in (snapshot.get("documents") or [])
        if isinstance(document, dict)
    }
    gold = {
        str(chunk.get("document_id"))
        for row in rows
        for chunk in (row.get("gold_chunks") or [])
        if isinstance(chunk, dict)
    }
    unknown = gold - known
    if unknown:
        problems.append(f"gold_chunks 指向 snapshot 之外的文档：{sorted(unknown)}")
    print(
        f"[dataset] gold 覆盖 {len(gold)} 篇文档，snapshot 记录 {len(known)} 篇，"
        f"缺失 {len(unknown)}"
    )

    kb_id = str(snapshot.get("kb_id"))
    workspace_id = str(snapshot.get("workspace_id"))
    for row in rows:
        sample_kb = row.get("kb_id")
        sample_workspace = row.get("workspace_id")
        if sample_kb is not None and str(sample_kb) != kb_id and row.get("expect") != "deny":
            problems.append(f"样本 {row.get('id')} 的 kb_id 与 snapshot.kb_id 不一致")
        if sample_workspace is not None and str(sample_workspace) != workspace_id:
            problems.append(f"样本 {row.get('id')} 的 workspace_id 与 snapshot.workspace_id 不一致")
    return problems


def cmd_generate(runner: ModuleType) -> int:
    baseline, candidate, base = build_both(runner)
    for path, cfg, note in (
        (
            BASELINE_PATH,
            baseline,
            "A 组：库默认策略（baseline）；B 组见 candidate_config.yaml。",
        ),
        (
            CANDIDATE_PATH,
            candidate,
            "B 组：与 A 组唯一不同的主因素是 retrieval_strategy=candidate；"
            "候选池倍数由服务端结算，故不写 candidate_k。",
        ),
    ):
        path.write_text(render_config(cfg, note=note), encoding="utf-8", newline="\n")
        print(f"[gen] 写入 {path}")

    guard_ok, guard_lines = runner.compare_configs(BASELINE_PATH, CANDIDATE_PATH)
    print(f"[guard] {'通过' if guard_ok else '未通过'}：")
    for line in guard_lines:
        print(f"  {line}")
    if not guard_ok:
        return EXIT_CONFIG

    print("\n[gen] 关键字段：")
    print_key_fields(runner, baseline, BASELINE_PATH)

    if base["configured_strategy"] != base["baseline_strategy"]:
        print(
            f"\n[gen] 注意：当前进程读到的检索策略是 {base['configured_strategy']}"
            f"（不是 {base['baseline_strategy']}）。跑基线前把它切回 "
            f"{base['baseline_strategy']} 并重启被测服务。"
        )

    print(
        "\n[gen] 下一步（切换策略靠**服务端环境变量 + 重启**，runner 不发策略参数）：\n"
        "  1) PYKNORA_RETRIEVAL_STRATEGY=baseline 重启 API 后：\n"
        "     uv run --frozen python eval/run_eval.py --config eval/baseline_config.yaml "
        "--repeats 3 --out eval/results/base-<日期>\n"
        "  2) PYKNORA_RETRIEVAL_STRATEGY=candidate 重启 API 后：\n"
        "     uv run --frozen python eval/run_eval.py --config eval/candidate_config.yaml "
        "--repeats 3 --out eval/results/cand-<日期>\n"
        "  两轮共用 experiment_id，必须用 --out 分开，否则后一轮覆盖前一轮的产物。"
    )
    return EXIT_OK


def cmd_check(runner: ModuleType) -> int:
    missing = [path for path in (BASELINE_PATH, CANDIDATE_PATH) if not path.exists()]
    if missing:
        for path in missing:
            print(f"[check] 缺少 {path}；先跑一次不带 --check 的生成。", file=sys.stderr)
        return EXIT_ENV

    failures = 0
    configs: dict[Path, dict[str, Any]] = {}
    for path in (BASELINE_PATH, CANDIDATE_PATH):
        cfg = runner.load_yaml(path)
        problems = runner.validate_config(cfg)
        if problems:
            failures += 1
            print(f"[check] {path} 不合格（{len(problems)} 处）：", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
        else:
            print(f"[check] {path} 通过 loader 校验（{len(cfg)} 个顶层字段，无缺失、无占位符）")
        configs[path] = cfg

    print("\n[diff] 两份配置逐字段差异：")
    for key, left, right in runner.build_diff(configs[BASELINE_PATH], configs[CANDIDATE_PATH]):
        print(f"  - {key}: A={left!r} / B={right!r}")
    guard_ok, guard_lines = runner.compare_configs(BASELINE_PATH, CANDIDATE_PATH)
    print(f"\n[guard] {'通过' if guard_ok else '未通过'}：")
    for line in guard_lines:
        print(f"  {line}")
    if not guard_ok:
        failures += 1

    try:
        fresh_baseline, fresh_candidate, base = build_both(runner)
    except EnvError as exc:
        print(f"\n[drift] 跳过（无法重算：{exc}）")
    else:
        drift = 0
        for path, fresh in (
            (BASELINE_PATH, fresh_baseline),
            (CANDIDATE_PATH, fresh_candidate),
        ):
            for key, left, right in runner.build_diff(configs[path], fresh):
                drift += 1
                print(f"[drift] {path.name}: {key} 磁盘={left!r} / 重算={right!r}")
        if drift:
            failures += 1
            print(f"[drift] 共 {drift} 处与生成器重算结果不一致：重跑生成器（别手改 yaml）。")
        else:
            print("[drift] 磁盘上的两份与生成器重算结果逐字段一致。")
        if base["configured_strategy"] != base["baseline_strategy"]:
            print(
                f"[drift] 提示：当前 PYKNORA_RETRIEVAL_STRATEGY={base['configured_strategy']}，"
                "跑基线前先切回 baseline 并重启服务。"
            )

    print_key_fields(runner, configs[BASELINE_PATH], BASELINE_PATH)

    print()
    for problem in inspect_dataset(runner, configs[BASELINE_PATH], BASELINE_PATH):
        failures += 1
        print(f"[dataset] 问题：{problem}")

    if failures:
        print(f"\n[check] 未通过（{failures} 类问题，exit {EXIT_CONFIG}）。")
        return EXIT_CONFIG
    print("\n[check] 通过：两份配置可交给 runner 使用（未发任何请求）。")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="make_configs",
        description="生成 / 校验 eval 的基线配置与候选配置（一次只改一个主因素）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  uv run --frozen python eval/make_configs.py\n"
            "  uv run --frozen python eval/make_configs.py --check\n"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验：用 run_eval 的 loader 加载两份 yaml、跑公平性守卫、核对评测集（不发请求）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        runner = load_runner()
    except EnvError as exc:
        print(f"[make_configs] {exc}", file=sys.stderr)
        return EXIT_ENV
    try:
        return cmd_check(runner) if args.check else cmd_generate(runner)
    except runner.ConfigError as exc:
        print(f"[make_configs] 配置不合格：{exc}", file=sys.stderr)
        return EXIT_CONFIG
    except EnvError as exc:
        print(f"[make_configs] {exc}", file=sys.stderr)
        return EXIT_ENV


if __name__ == "__main__":
    raise SystemExit(main())
