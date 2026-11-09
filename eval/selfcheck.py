#!/usr/bin/env python
"""交付物自检：把「讲义 §十 要求交什么」变成一条命令。

为什么要有这个脚本
------------------
交付清单散在三处：计划 §9（eval/ 五件 + docs/Day03/ 八份 + tests/ 四件）、§10.2（五条硬门槛）、
§10.3（照抄命令）。评审前靠人数，最容易漏的是「文件在但没内容」和「配置与评测集对不上」。
本脚本把**离线就能判定**的部分收成一次运行，每项给 PASS/FAIL/SKIP 加一句理由，最后给退出码。

三种状态的含义（别混）
----------------------
- PASS：现在就能证明它成立（文件在且非空 / 配置能被 loader 加载 / 两份配置只差一个主因素…）；
- FAIL：缺东西或口径不一致——**必须修**，不是「再跑一次就好」。计划 §9 点名但仓库里没有的
  交付物（含 tests/ 四个文件里没有真实落点的那一个）一律记 FAIL，不再用「本轮未建」兜底：
  交付自检跑在**被交付的树**上，缺文件就是缺口，不是「等下一轮」；
- SKIP：**离线证不了**，理由里写清是哪一种：
  (a) `需真跑评测才有数据`：`eval/results/` 整个目录不在（计划 §9 明确该目录不入库）；
  (b) `前置缺失`：上游文件不在导致这一条无可判定（同一缺口在别处已经 FAIL，这里不重复判）。

为什么不在这里重写校验
----------------------
配额、字段名、gold 形状的校验在 `eval/build_eval_set.py::cmd_check` 里已有一份；配置的字段校验与
公平性守卫在 `eval/run_eval.py::validate_config / compare_configs` 里；配置与评测集的一致性在
`eval/make_configs.py::inspect_dataset` 里。这里全部按路径 import 复用——抄一份的那天起，两侧就
开始分叉，最后变成「自检通过、评审打回」这种最难查的错。

用法::

    uv run --frozen python eval/selfcheck.py

退出码：0 = 没有 FAIL；1 = 有 FAIL。SKIP 不影响退出码，但会在汇总里单列（SKIP 不等于已交付）。
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import re
import subprocess
import sys
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = REPO_ROOT / "eval"
DOCS_DAY03 = REPO_ROOT / "docs" / "Day03"
RESULTS_DIR = EVAL_DIR / "results"

EVAL_SET = EVAL_DIR / "eval_set_v0.1.jsonl"
BASELINE_CONFIG = EVAL_DIR / "baseline_config.yaml"
CANDIDATE_CONFIG = EVAL_DIR / "candidate_config.yaml"
RUN_EVAL = EVAL_DIR / "run_eval.py"
BUILD_EVAL_SET = EVAL_DIR / "build_eval_set.py"
MAKE_CONFIGS = EVAL_DIR / "make_configs.py"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

# SKIP 的两类原因：汇总时分开列，避免「需真跑评测」和「前置缺失」混成一句含糊的「待补」。
SKIP_DATA = "data"  # 需要真跑评测才有数据（eval/results/ 整个不在）
SKIP_BLOCKED = "blocked"  # 前置缺失，这一条无从判定（同一缺口在别处已 FAIL）

EXIT_OK = 0
EXIT_FAIL = 1

# 讲义 §十 / 计划 §10.2 要求 docs/Day03/ 下的八份。
DOC_FILES = (
    "01-优化选型.md",
    "02-固定评测集与口径.md",
    "03-基线评测报告.md",
    "04-优化前后对比.md",
    "05-失败样本记录.md",
    "06-可信证据与引用设计.md",
    "07-拒答停止与安全测试.md",
    "08-AI协作与问题清单.md",
)

# run_eval 一轮跑完该产出的三件；目录在而缺件 = 这轮没跑完（真实缺口），不是「需真跑」。
RESULT_ARTIFACTS = ("summary.json", "raw.jsonl", "report.md")

NO_RESULTS = "eval/results/ 不存在：需真跑评测才有数据（计划 §9 明确该目录不入库）"

# 产物目录命名有两套：计划 §9 写 `base-*/` 与 `cand-*/`，实际落盘的是
# `baseline-*/` 与 `candidate-*/`（compare-20261030.md 里引用的也是后者）。两套都认，
# 把并集按目录名排序（目录名带日期，字典序即时间序），最后一个就是最新一轮。
RUN_PATTERNS = (
    (("baseline-*", "base-*"), "基线"),
    (("candidate-*", "cand-*"), "候选"),
)
COMPARE_PATTERN = "compare-*.md"

# 交付口径的七个指标（summary.json 的 metrics 键）。顺序同 docs/Day03/04 的表格；
# `chat_ttft_p95_ms` 是多测的首 token 延迟，不在七指标内——它缺失不影响本项判定，
# 但七项里任何一项没有数值都说明这轮产物不完整。
SUMMARY_METRICS = (
    ("recall_at_5", "Recall@5"),
    ("precision_at_5", "Precision@5"),
    ("citation_accuracy", "引用准确率"),
    ("abstain_accuracy", "无证据拒答准确率"),
    ("deny_interception", "权限拦截率"),
    ("retrieval_p95_ms", "检索 P95"),
    ("chat_total_p95_ms", "问答 P95"),
)


@dataclass(frozen=True, slots=True)
class Check:
    """一条自检结论。status 只能是 PASS/FAIL/SKIP。"""

    name: str
    status: str
    detail: str
    skip_kind: str = ""


@dataclass(frozen=True, slots=True)
class TestTarget:
    """讲义 §十 的逻辑文件名 → 本仓**真实**落点。

    讲义写的是四个理想文件名，本仓按「实际文件 + 用例内容」映射；映射依据逐条写在
    `basis` 里（文件名相近不算依据，必须能对到文件里的用例或自述）：

    - `tests/test_retrieval.py`：计划 §9:641 明写「本轮等价物：
      tests/unit/test_retrieval_search.py（扩展 candidate 策略用例）」；策略开关用例
      另落在 `tests/unit/test_retrieval_strategy.py`（docstring 自述 T3.1 尾款）。
    - `tests/test_evidence.py`：计划 §9:642「✔ 新建」；`tests/unit/test_evidence.py`
      的 docstring 逐条对位 EV-01..04。
    - `tests/test_citations.py`：计划 §9:643「✔ 新建」、§6.3 要求 CIT-01/02/03；
      本仓**没有**这个专项文件，只有部分覆盖（见 `partial`），`docs/Day03/06` §6
      已把「CIT-02 无用例、CIT-03 端到端用例未写」登记为未完成项——所以 `mapped`
      留空，自检判 FAIL（真实缺口），不拿「命名不同」把缺口说成 PASS。
    - `tests/test_security_refusal.py`：计划 §9:644 原写落在
      `tests/api/test_api_chat_safety.py`，但仓库里从未有这个文件；实际按 SAFE 编号
      拆成 `tests/api/test_api_refusal.py`（SAFE-01/02/03/06/09）与
      `tests/api/test_api_security_safe.py`（SAFE-05/10），两个文件的 docstring
      自述了各自覆盖的 SAFE 编号，故以这两个文件为真实落点。

    `mapped` 为空表示「计划点名、本仓没有真实落点」；`partial` 只作说明，不计入 PASS。
    """

    logical: str
    mapped: tuple[str, ...]
    basis: str
    partial: tuple[str, ...] = ()


TEST_TARGETS = (
    TestTarget(
        logical="tests/test_retrieval.py",
        mapped=(
            "tests/unit/test_retrieval_search.py",
            "tests/unit/test_retrieval_strategy.py",
        ),
        basis=(
            "计划 §9:641 写明本轮等价物是 tests/unit/test_retrieval_search.py"
            "（扩展 candidate 策略用例）；策略开关用例落在 tests/unit/test_retrieval_strategy.py"
            "（docstring 自述 T3.1 尾款）"
        ),
    ),
    TestTarget(
        logical="tests/test_evidence.py",
        mapped=("tests/unit/test_evidence.py",),
        basis="计划 §9:642「✔ 新建」；tests/unit/test_evidence.py 的 docstring 逐条对位 EV-01..04",
    ),
    TestTarget(
        logical="tests/test_citations.py",
        mapped=("tests/unit/test_citations.py",),
        basis=(
            "计划 §9:643「✔ 新建」、§6.3 要求 CIT-01/02/03；本轮已落 tests/unit/test_citations.py"
            "（7 例：CIT-01 序号层 + CIT-02 不塞入 + CIT-03 数据面 共 4 例通过，"
            "chunk_id 级校验 / 引用二次授权 / 版本失效判定 共 3 例"
            " xfail(strict=True) 标为待实现）。"
            "**该文件的 3 个 xfail 是真实实现缺口**，已登记在 docs/IMPLEMENTATION_STATUS.md"
            "「本轮：Day03 DAG」的未验证第 ⑦ 条与 docs/Day03/06 §6"
        ),
        partial=(
            "tests/unit/test_eval_harness.py（audit_citations 引用核验）",
            "tests/api/test_api_refusal.py（SAFE-06 invalid_citations）",
            "tests/api/test_api_chat.py（citations / invalid_citations 事件）",
        ),
    ),
    TestTarget(
        logical="tests/test_security_refusal.py",
        mapped=(
            "tests/api/test_api_refusal.py",
            "tests/api/test_api_security_safe.py",
        ),
        basis=(
            "计划 §9:644 原写 tests/api/test_api_chat_safety.py（仓库里没有该文件）；"
            "实际按 SAFE 编号拆成 test_api_refusal.py（SAFE-01/02/03/06/09）与 "
            "test_api_security_safe.py（SAFE-05/10），以两个文件 docstring 的自述为准"
        ),
    ),
)


def _relative(path: Path) -> str:
    """相对仓库根的 posix 路径；仓库外的原样返回。"""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _problem_lines(text: str) -> list[str]:
    """挑出被复用脚本打出来的问题行（`- …` 或 `[失败] …`）。"""
    problems: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("-") or stripped.startswith("[失败]"):
            problems.append(stripped)
    return problems


def _jsonl_records(path: Path) -> list[dict[str, Any]]:
    """只读 jsonl 用于**展示分布**；字段与 gold 的合法性交给 build_eval_set 判。"""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"第 {number} 行不是 JSON 对象")
            records.append(payload)
    return records


def check_file(label: str, path: Path) -> Check:
    """存在 + 非空。"""
    relative = _relative(path)
    if not path.exists():
        return Check(label, FAIL, f"缺文件 {relative}")
    if not path.is_file():
        return Check(label, FAIL, f"{relative} 不是普通文件")
    size = path.stat().st_size
    if size == 0:
        return Check(label, FAIL, f"{relative} 是空文件（0 字节）")
    return Check(label, PASS, f"{relative}，{size} 字节")


def load_module(name: str, path: Path) -> tuple[ModuleType | None, str]:
    """按路径加载 eval/ 下的脚本，返回 (module, 错误说明)。

    与 `make_configs.load_runner` 同一个做法：必须先登记进 `sys.modules`，否则模块里的
    `@dataclass(slots=True)` 回查 `sys.modules[cls.__module__]` 时会 AttributeError。
    """
    if not path.is_file():
        return None, f"{_relative(path)} 不存在"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None, f"无法为 {_relative(path)} 建立 import spec"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # 导入失败只影响依赖它的检查项，不能把自检本身炸掉
        sys.modules.pop(name, None)
        return None, f"import {path.name} 失败：{type(exc).__name__}: {exc}"
    return module, ""


def check_eval_set(build: ModuleType | None, build_error: str) -> list[Check]:
    """eval_set 的配额/字段/gold 形状（复用 --check）+ category/expect 分布。"""
    checks: list[Check] = []
    quota_name = "eval_set 配额/字段/gold 形状（复用 build_eval_set --check）"
    # 展示名里的「条数 / 类别数 / expect 档数」一律从 build 算，不写死：
    # 配额从 20 条 5 类扩到 30 条 8 类时，写死的名字会一直报旧口径。
    if build is None:
        dist_name = "eval_set category/expect 分布"
    else:
        # EXPECT_OF 是 category → expect 的映射，档数要取 values 的去重数（不能 len(dict)，
        # 那等于类别数 8；expect 实际只有 answer/abstain/deny 三档）。
        expect_tiers = len(set(build.EXPECT_OF.values()))
        dist_name = (
            f"eval_set category/expect 分布（{sum(build.QUOTA.values())} 条、"
            f"{len(build.QUOTA)} 类配额、expect {expect_tiers} 档）"
        )

    if not EVAL_SET.is_file():
        reason = f"缺文件 {_relative(EVAL_SET)}"
        return [Check(quota_name, FAIL, reason), Check(dist_name, FAIL, reason)]

    if build is None:
        checks.append(Check(quota_name, SKIP, f"build_eval_set.py 不可用：{build_error}"))
    else:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = build.cmd_check(argparse.Namespace(out=str(EVAL_SET)))
        if code == 0:
            checks.append(Check(quota_name, PASS, _first_line(stdout.getvalue())))
        else:
            problems = _problem_lines(stderr.getvalue()) or _problem_lines(stdout.getvalue())
            fallback = _first_line(stderr.getvalue())
            checks.append(
                Check(quota_name, FAIL, f"退出码 {code}：" + ("；".join(problems[:3]) or fallback))
            )

    try:
        records = _jsonl_records(EVAL_SET)
    except (OSError, ValueError) as exc:
        checks.append(Check(dist_name, FAIL, f"读不了 {_relative(EVAL_SET)}：{exc}"))
        return checks

    if build is None:
        checks.append(Check(dist_name, SKIP, f"build_eval_set.py 不可用：{build_error}"))
        return checks

    quota = build.QUOTA
    expect_of = build.EXPECT_OF
    categories = build.CATEGORIES
    counts = Counter(str(record.get("category")) for record in records)
    expects = Counter(str(record.get("expect")) for record in records)
    expected_total = sum(quota.values())
    expected_expects: Counter[str] = Counter()
    for category, number in quota.items():
        expected_expects[expect_of[category]] += number

    problems: list[str] = []
    if len(records) != expected_total:
        problems.append(f"总数 {len(records)}，应为 {expected_total}")
    for category in categories:
        actual = counts.get(category, 0)
        if actual != quota[category]:
            problems.append(f"{category} {actual}/{quota[category]}")
    for expect, number in sorted(expected_expects.items()):
        actual = expects.get(expect, 0)
        if actual != number:
            problems.append(f"expect={expect} {actual}/{number}")

    category_text = "，".join(f"{category}:{counts.get(category, 0)}" for category in categories)
    expect_text = "，".join(
        f"{expect}:{expects.get(expect, 0)}/{number}"
        for expect, number in sorted(expected_expects.items())
    )
    detail = f"{len(records)} 条；category {category_text}；expect {expect_text}"
    if problems:
        checks.append(Check(dist_name, FAIL, "；".join(problems)))
    else:
        checks.append(Check(dist_name, PASS, detail))
    return checks


def check_configs(runner: ModuleType | None, make: ModuleType | None) -> list[Check]:
    """两份 config 能被 loader 加载、只差一个主因素、且与评测集对得上。"""
    checks: list[Check] = []
    loaded: dict[Path, dict[str, Any]] = {}

    pairs = (
        ("baseline_config.yaml", BASELINE_CONFIG),
        ("candidate_config.yaml", CANDIDATE_CONFIG),
    )
    for label, path in pairs:
        name = f"{label} 可被 run_eval loader 加载"
        if runner is None:
            checks.append(Check(name, SKIP, "run_eval.py 不可用，无法复用它的 loader"))
            continue
        if not path.is_file():
            checks.append(Check(name, FAIL, f"缺文件 {_relative(path)}"))
            continue
        try:
            parsed = runner.load_yaml(path)
        except Exception as exc:
            checks.append(Check(name, FAIL, f"load_yaml 失败：{type(exc).__name__}: {exc}"))
            continue
        problems = runner.validate_config(parsed)
        if problems:
            checks.append(Check(name, FAIL, f"{len(problems)} 处问题：" + "；".join(problems[:3])))
        else:
            checks.append(Check(name, PASS, f"{len(parsed)} 个顶层字段，无缺失/占位符"))
        loaded[path] = parsed

    baseline = loaded.get(BASELINE_CONFIG)

    name = "两份配置只差一个主因素（run_eval.compare_configs）"
    if runner is None:
        checks.append(Check(name, SKIP, "run_eval.py 不可用，无法复用它的 compare"))
    elif not (BASELINE_CONFIG.is_file() and CANDIDATE_CONFIG.is_file()):
        checks.append(Check(name, SKIP, "两份配置不齐，无从比对", skip_kind=SKIP_BLOCKED))
    else:
        try:
            consistent, lines = runner.compare_configs(BASELINE_CONFIG, CANDIDATE_CONFIG)
        except Exception as exc:
            checks.append(Check(name, FAIL, f"compare_configs 失败：{type(exc).__name__}: {exc}"))
        else:
            if consistent:
                detail = lines[0] if lines else "只有 retrieval_strategy 不同"
                checks.append(Check(name, PASS, detail))
            else:
                checks.append(Check(name, FAIL, "；".join(lines[:3])))

    name = "配置里的 eval_dataset 指向 eval/eval_set_v0.1.jsonl"
    if runner is None or baseline is None:
        checks.append(Check(name, SKIP, "拿不到 baseline 配置（见上一条）"))
    else:
        raw = str(baseline.get("eval_dataset") or "")
        resolved = runner.resolve_dataset_path(raw, BASELINE_CONFIG)
        if resolved.resolve() == EVAL_SET.resolve():
            checks.append(Check(name, PASS, f"{raw} → {_relative(resolved)}"))
        else:
            checks.append(Check(name, FAIL, f"{raw} → {resolved}，不是 {_relative(EVAL_SET)}"))

    name = "config 与 dataset 一致性（make_configs.inspect_dataset）"
    if make is None or runner is None or baseline is None:
        checks.append(Check(name, SKIP, "缺 make_configs / run_eval / baseline 配置，无法比对"))
    else:
        stdout = io.StringIO()
        try:
            with redirect_stdout(stdout):
                problems = make.inspect_dataset(runner, baseline, BASELINE_CONFIG)
        except Exception as exc:
            checks.append(Check(name, FAIL, f"inspect_dataset 抛错：{type(exc).__name__}: {exc}"))
        else:
            if problems:
                checks.append(Check(name, FAIL, "；".join(problems[:3])))
            else:
                printed = [line.strip() for line in stdout.getvalue().splitlines() if line.strip()]
                detail = printed[-1] if printed else "快照内文档与评测集引用一致"
                checks.append(Check(name, PASS, detail))
    return checks


def collect_count(path: Path) -> tuple[int | None, str]:
    """`pytest --collect-only` 数用例数：只收集，不执行用例、不连库。"""
    command = [
        sys.executable,
        "-m",
        "pytest",
        # pyproject 的 addopts="-q" 会和这里的 -q 叠成 -qq，输出退化成「file: N」、
        # 连 "N tests collected" 都没有；清空 addopts 让输出格式可预测。
        "-o",
        "addopts=",
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"{type(exc).__name__}: {exc}"

    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    matched = re.search(r"(\d+)\s+tests?\s+collected", output)
    if matched is not None:
        return int(matched.group(1)), ""
    if completed.returncode == 5:  # pytest 的「没有收集到任何用例」
        return 0, ""
    nodeids = [line for line in output.splitlines() if "::" in line and line.strip()]
    if completed.returncode == 0 and nodeids:
        return len(nodeids), ""
    reason = _first_line(completed.stderr or "") or _first_line(completed.stdout or "")
    return None, f"pytest 退出 {completed.returncode}：{reason}"


def check_tests() -> list[Check]:
    """tests/ 四件：按映射表找真实文件，每个落点都要存在且收集数 > 0。

    FAIL 只给真实缺口：映射到的文件不存在 / 收集失败 / 收集到 0 条；`mapped` 为空
    （计划点名、本仓没有真实落点）同样记 FAIL，并把部分覆盖写清楚——拿 SKIP 把
    「缺交付物」藏成「等下一轮」，正是这个脚本第一版最容易被误读的地方。
    """
    checks: list[Check] = []
    for target in TEST_TARGETS:
        if not target.mapped:
            label = f"{target.logical}（无真实落点）"
            partial = "、".join(target.partial) or "无"
            detail = f"{target.basis}；部分覆盖：{partial}"
            checks.append(Check(label, FAIL, detail))
            continue

        label = f"{target.logical}（映射落点有用例）"
        passed: list[str] = []
        problems: list[str] = []
        for candidate in target.mapped:
            path = REPO_ROOT / candidate
            if not path.is_file():
                problems.append(f"缺 {candidate}")
                continue
            count, error = collect_count(path)
            if count is None:
                problems.append(f"{candidate} 收集失败：{error}")
            elif count == 0:
                problems.append(f"{candidate} 里没有用例（收集到 0 条）")
            else:
                passed.append(f"{candidate}：{count} 个用例")
        if problems:
            detail = "；".join(problems) + f"（映射依据：{target.basis}）"
            checks.append(Check(label, FAIL, detail))
        else:
            detail = "、".join(passed) + "（只收集，不执行）"
            checks.append(Check(label, PASS, detail))
    return checks


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, str]:
    """读一个 JSON 对象；失败只返回原因，不抛——自检不能因为一个坏产物整块崩掉。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"读不了 {_relative(path)}：{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return None, f"{_relative(path)} 不是 JSON 对象"
    return payload, ""


def _has_numeric_value(metric: Any) -> bool:
    """指标必须是 `{"value": <number>}`；None / 字符串 / 缺 value 都不算「有数值」。

    bool 是 int 的子类，`True` 不能冒充 1，所以单独排除。
    """
    if not isinstance(metric, dict):
        return False
    value = metric.get("value")
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_run(patterns: tuple[str, ...], label: str) -> Check:
    """一轮评测产物：目录在且三件齐全、summary 的七个指标键都有数值。"""
    name = f"eval/results/{'|'.join(patterns)}（{label}产物）"
    found: dict[Path, Path] = {}
    for pattern in patterns:
        for item in RESULTS_DIR.glob(pattern):
            if item.is_dir():
                found[item.resolve()] = item
    # 两套目录前缀并存时名字序不可靠（`base-` 排在 `baseline-` 前），用目录 mtime 定最新。
    runs = sorted(found.values(), key=lambda item: (item.stat().st_mtime, item.name))
    if not runs:
        detail = f"没有 {' / '.join(patterns)} 目录：这一轮还没跑"
        return Check(name, SKIP, detail, skip_kind=SKIP_DATA)

    latest = runs[-1]  # mtime 最新的一轮
    missing = [
        item
        for item in RESULT_ARTIFACTS
        if not (latest / item).is_file() or (latest / item).stat().st_size == 0
    ]
    if missing:
        detail = f"{latest.name} 缺/空 {'、'.join(missing)}：该轮未跑完或产物被删"
        return Check(name, FAIL, detail)

    payload, error = _read_json_object(latest / "summary.json")
    if payload is None:
        return Check(name, FAIL, error)

    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return Check(name, FAIL, f"{latest.name}/summary.json 没有 metrics 对象")

    absent = [f"{key}（{label_cn}）" for key, label_cn in SUMMARY_METRICS if key not in metrics]
    no_value = [
        f"{key}（{label_cn}）"
        for key, label_cn in SUMMARY_METRICS
        if key in metrics and not _has_numeric_value(metrics[key])
    ]
    if absent or no_value:
        problems: list[str] = []
        if absent:
            problems.append("缺指标键 " + "、".join(absent))
        if no_value:
            problems.append("指标无数值 " + "、".join(no_value))
        return Check(name, FAIL, f"{latest.name}：" + "；".join(problems))

    keys = "、".join(key for key, _ in SUMMARY_METRICS)
    detail = f"{latest.name}：{'、'.join(RESULT_ARTIFACTS)} 齐全；7/7 指标键有数值（{keys}）"
    return Check(name, PASS, detail)


def check_compare_report() -> Check:
    """两轮之间的对比报告：目录在就必须有非空 compare-*.md。"""
    name = f"eval/results/{COMPARE_PATTERN}（对比报告）"
    if not RESULTS_DIR.is_dir():
        return Check(name, SKIP, NO_RESULTS, skip_kind=SKIP_DATA)
    reports = sorted(item for item in RESULTS_DIR.glob(COMPARE_PATTERN) if item.is_file())
    if not reports:
        return Check(name, FAIL, "没有 compare-*.md：两轮跑完但对比报告没生成")
    latest = reports[-1]
    size = latest.stat().st_size
    if size == 0:
        return Check(name, FAIL, f"{_relative(latest)} 是空文件（0 字节）")
    return Check(name, PASS, f"{_relative(latest)}，{size} 字节")


def check_results() -> list[Check]:
    """评测产物：目录整个不在 = SKIP（需真跑）；目录在而缺件/缺指标 = FAIL（半轮是缺口）。"""
    if not RESULTS_DIR.is_dir():
        checks = [
            Check(
                f"eval/results/{pattern[0]}（{label}产物）",
                SKIP,
                NO_RESULTS,
                skip_kind=SKIP_DATA,
            )
            for pattern, label in RUN_PATTERNS
        ]
        checks.append(
            Check(
                f"eval/results/{COMPARE_PATTERN}（对比报告）",
                SKIP,
                NO_RESULTS,
                skip_kind=SKIP_DATA,
            )
        )
        return checks
    checks = [check_run(patterns, label) for patterns, label in RUN_PATTERNS]
    checks.append(check_compare_report())
    return checks


def run_checks() -> list[Check]:
    checks: list[Check] = []

    # 1) eval/ 四件（results/ 是评测产物，放在最后单独一节）
    checks.append(check_file("eval/eval_set_v0.1.jsonl", EVAL_SET))
    checks.append(check_file("eval/baseline_config.yaml", BASELINE_CONFIG))
    checks.append(check_file("eval/candidate_config.yaml", CANDIDATE_CONFIG))
    checks.append(check_file("eval/run_eval.py", RUN_EVAL))

    # 2) docs/Day03/ 八份
    for filename in DOC_FILES:
        checks.append(check_file(f"docs/Day03/{filename}", DOCS_DAY03 / filename))

    # 3) tests/ 四件：存在 + 收集数 > 0
    checks.extend(check_tests())

    # 4) eval_set 内容：复用 build_eval_set 的校验
    build, build_error = load_module("_pyknora_selfcheck_build_eval_set", BUILD_EVAL_SET)
    if build is None:
        checks.append(Check("eval/build_eval_set.py 可被 import（复用其校验）", FAIL, build_error))
    checks.extend(check_eval_set(build, build_error))

    # 5) 两份 config：复用 run_eval 的 loader/compare + make_configs 的一致性检查
    runner, runner_error = load_module("_pyknora_selfcheck_run_eval", RUN_EVAL)
    if runner is None:
        checks.append(Check("eval/run_eval.py 可被 import（复用其 loader）", FAIL, runner_error))
    make, make_error = load_module("_pyknora_selfcheck_make_configs", MAKE_CONFIGS)
    if make is None:
        label = "eval/make_configs.py 可被 import（复用其一致性检查）"
        checks.append(Check(label, FAIL, make_error))
    checks.extend(check_configs(runner, make))

    # 6) 评测产物：目录不在=SKIP；目录在而缺件/缺指标=FAIL
    checks.extend(check_results())
    return checks


def render(checks: list[Check]) -> None:
    print(f"PyKnora Day03 交付物自检 · 仓库根 {REPO_ROOT}")
    print("=" * 78)
    for check in checks:
        print(f"[{check.status}] {check.name}")
        print(f"        {check.detail}")

    counts = Counter(check.status for check in checks)
    print("=" * 78)
    summary = (
        f"汇总：PASS {counts[PASS]} / FAIL {counts[FAIL]} / SKIP {counts[SKIP]}"
        f"（共 {len(checks)} 项）"
    )
    print(summary)

    failures = [check for check in checks if check.status == FAIL]
    if failures:
        print()
        print("FAIL 明细（必须修）：")
        for check in failures:
            print(f"  - {check.name}：{check.detail}")

    skips = [check for check in checks if check.status == SKIP]
    if skips:
        print()
        print("SKIP 明细（离线证不了，不代表已交付）：")
        data_skips = [check for check in skips if check.skip_kind == SKIP_DATA]
        blocked = [check for check in skips if check.skip_kind == SKIP_BLOCKED]
        others = [check for check in skips if check.skip_kind not in (SKIP_DATA, SKIP_BLOCKED)]
        groups = (
            ("需真跑评测才有数据", data_skips),
            ("前置缺失，无从判定", blocked),
            ("其他", others),
        )
        for title, group in groups:
            if not group:
                continue
            print(f"  [{title}]")
            for check in group:
                print(f"    - {check.name}：{check.detail}")

    print()
    if failures:
        print(f"退出码 {EXIT_FAIL}：有 {len(failures)} 项 FAIL。")
    else:
        print(f"退出码 {EXIT_OK}：无 FAIL（SKIP 需在真实评测跑完后复核）。")


def main() -> int:
    checks = run_checks()
    render(checks)
    return EXIT_FAIL if any(check.status == FAIL for check in checks) else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
