#!/usr/bin/env python
"""Day3 A/B 对比报告：把两次 `run_eval.py` 的结果目录拼成一张可核对的表。

为什么不把这些逻辑塞进 `run_eval.py`：

- 两轮产物是**不同时间、不同进程**落盘的；对比要回答的是「同一条样本，候选比基线
  好还是差」，必须把两份 `raw.jsonl` 按 `sample_id` 对上，而单轮 runner 只看得见自己。
- 指标口径只能有一份真相。所以本脚本**不重抄公式**：它 import `eval/run_eval.py`，复用
  `recall_at_5` / `audit_citations` / `aggregate` / `collect_failures` 这些纯函数，并用
  `raw.jsonl` 复算 `summary.json` 的每个指标——两者对不上就在报告里点名，而不是挑一个
  看起来顺眼的写进去。

三条硬规矩（违反即报告作废）：

1. **不补值**：缺数据的格子写「待填（缺 X）」，不写 0、不估算、不从上一次抄。
2. **退化必须记**：计划 §5.4（讲义 §4.5）要求候选变差的样本单列，所以逐样本判据走同一套
   公式，下降即入清单。
3. **只描述观察到什么**：本文件不写「已优化成功」，结论留给人工文档。

用法：

    # 只校验两个目录是否完整可对比（不写任何文件）
    uv run --frozen python eval/report.py --check <base-dir> <cand-dir>

    # 生成对比报告（默认 eval/reports/compare-<时间戳>.md）
    uv run --frozen python eval/report.py eval/results/base-20261030 eval/results/cand-20261030
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

# 退出码：0 = 报告已生成 / 校验通过；1 = 校验不通过（不可对比）；2 = 输入目录/文件不可用。
# 2 与 1 分开，是因为处置人不同：2 是路径写错了，1 是两份产物本身对不上。
EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_INPUT = 2

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = REPO_ROOT / "eval" / "reports"

SUMMARY_FILE = "summary.json"
RAW_FILE = "raw.jsonl"
REPORT_FILE = "report.md"

#: 报告头要逐项引用的字段。缺任何一个都说明 summary.json 不是 run_eval.py 写的。
REQUIRED_SUMMARY_KEYS: tuple[str, ...] = (
    "experiment_id",
    "app_commit",
    "generated_at",
    "repeats",
    "metrics",
    "counts",
    "config",
)

PENDING = "待填"

#: 计划 §3.6 / §5.5 的七行指标：(summary.json 里的键, 报告里的名字, 单位)。
#: `chat_ttft_p95_ms` 故意不在里面：计划把「问答 P95」定义为到 done 的总时长，
#: TTFT 只留在单轮 report.md 里看，混进对比表会让「问答 P95 变快」有两种解释。
METRIC_ROWS: tuple[tuple[str, str, str], ...] = (
    ("recall_at_5", "Recall@5", "ratio"),
    ("precision_at_5", "Precision@5", "ratio"),
    ("citation_accuracy", "引用准确率", "ratio"),
    ("abstain_accuracy", "无证据拒答准确率", "ratio"),
    ("deny_interception", "权限拦截率", "ratio"),
    ("retrieval_p95_ms", "检索 P95", "ms"),
    ("chat_total_p95_ms", "问答 P95", "ms"),
)

#: 计划 §3.7 的九类（顺序照抄原表，报告里的聚合表按这个顺序出，缺席的类也保留成 0）。
FAILURE_CLASS_ORDER: tuple[str, ...] = (
    "文档解析失败",
    "索引版本错误",
    "召回不足",
    "排序失败",
    "路由失败",
    "证据不足",
    "生成错误",
    "引用错误",
    "权限错误",
)

#: 九类之外：`run_eval.collect_failures` 自造的 `REQUEST_FAILED`（传输/服务端故障）不属于
#: 讲义九类，混进任何一类都会把「环境炸了」说成「模型答错了」，所以单列。
OUTSIDE_NINE = "九类之外"

#: 二级 code → (一级类, 疑似环节)。疑似环节只给「往哪儿查」的提示，**不是归因结论**。
FAILURE_CODE_CLASS: dict[str, tuple[str, str]] = {
    "PARSE_EMPTY": ("文档解析失败", "解析/切片（services/ingest/parser.py, splitter.py）"),
    "PARSE_FAILED": ("文档解析失败", "解析/切片（services/ingest/parser.py, splitter.py）"),
    "STALE_BUILD": ("索引版本错误", "索引构建与发布（services/ingest/publish.py）"),
    "OLD_VERSION_HIT": ("索引版本错误", "活动版本视图 v_active_chunks / documents 三指针"),
    "NO_HIT": ("召回不足", "召回（services/retrieval/search.py）"),
    "GOLD_MISS": ("召回不足", "候选集与融合（services/retrieval/search.py, rrf.py）"),
    "FTS_TOKEN_MISS": ("召回不足", "关键词路 FTS 查询构造（services/retrieval/search.py）"),
    "RANK_TOO_LOW": ("排序失败", "RRF 融合与排序（services/retrieval/rrf.py）"),
    "VECTOR_DOMINATES": ("排序失败", "两路候选数与 RRF 权重（services/retrieval/rrf.py）"),
    "WRONG_KB": ("路由失败", "KB 路由与权限谓词（api/v1/query.py, db/repos/reads.py）"),
    "DUPLICATE_DOC": ("路由失败", "版本可见性/去重（v_active_chunks）"),
    "OVER_RETRIEVAL": ("证据不足", "证据门与分流（services/rag/evidence.py, answer.py）"),
    "UNRELATED_CHUNK": ("证据不足", "证据门阈值（settings.evidence_min_score）"),
    "FACT_MISSING": ("生成错误", "生成与提示词（services/rag/answer.py / prompt_version）"),
    "EXTRA_FACT": ("生成错误", "生成（services/rag/answer.py）"),
    "TRUNCATED": ("生成错误", "生成与超时（chat_timeout_s）"),
    "INVALID_CITATION": ("引用错误", "引用编号生成（services/rag/answer.py）"),
    "CITATION_WRONG_DOC": ("引用错误", "引用组装（services/rag/answer.py）"),
    "CITATION_FACT_MISSING": ("引用错误", "引用片段截取（Citation.snippet 长度与切点）"),
    "CITATION_PAGE_INVERTED": ("引用错误", "页号区间（chunks.page_no / page_end_no）"),
    "CITATION_VERSION_MISMATCH": ("引用错误", "活动版本（active_document_version_id）"),
    "FORBIDDEN_LEAK": ("权限错误", "权限唯一入口（api/deps.py require_kb_access）"),
    "DENY_NOT_404": ("权限错误", "错误码与路由（api/deps.py, core/errors.py）"),
    "REQUEST_FAILED": (OUTSIDE_NINE, "传输/服务端故障（不是模型质量缺陷，先看服务端日志）"),
}

#: 逐样本判据的展示名（退化清单的「退化项」列）。
SIGNAL_LABELS: dict[str, str] = {
    "recall_at_5": "Recall@5",
    "precision_at_5": "Precision@5",
    "citation_pass": "引用通过率",
    "fact_hit": "事实命中",
    "abstained": "拒答成功",
    "deny_intercepted": "权限拦截",
}

#: 逐样本判据的参与范围（写进报告 §3，防止有人把「判据没覆盖」读成「没问题」）。
#: `answer`/`abstain`/`deny` 是 expect 三档；`injection`/`poison`/`privilege` 是安全组
#: category 三档的中文名（口径见 `build_eval_set.EXPECT_OF`：前两类归 abstain，后一类归 deny）。
SIGNAL_SCOPE: dict[str, str] = {
    "answer": "Recall@5、Precision@5、引用通过率、事实命中",
    "abstain": "拒答成功",
    "deny": "权限拦截",
    "injection": "提示注入",
    "poison": "污染来源",
    "privilege": "越权",
}

#: 浮点比较容差。两侧都来自同一批 JSON 浮点，除了舍入没有别的误差来源；
#: 用 1e-9 而不是 1e-6，是为了让「真的算错」和「只是打印精度」区分开。
DEGRADE_EPS = 1e-9

#: 复算要用的 run_eval 接口。改了 run_eval 就该在这里显式失败，而不是悄悄少算一项。
RUNNER_API: tuple[str, ...] = (
    "aggregate",
    "audit_citations",
    "build_diff",
    "collect_failures",
    "deny_intercepted",
    "is_abstained",
    "precision_at_5",
    "recall_at_5",
    "METRIC_DEFINITIONS",
    "METRIC_ORDER",
    "PRIMARY_FACTOR_FIELDS",
)

#: 分母取哪个 counts 键（P95 用 raw 行数，见 `_denominator`）。
DENOMINATOR_KEYS: dict[str, str] = {
    "recall_at_5": "recall_samples",
    "precision_at_5": "precision_samples",
    "citation_accuracy": "citations_checked",
    "abstain_accuracy": "abstain_samples",
    "deny_interception": "deny_samples",
}

#: 「为什么这个指标是待填」的人话。先看分母，再看 raw，最后才说 summary 缺字段。
COUNT_REASONS: dict[str, tuple[str, str]] = {
    "recall_at_5": ("recall_samples", "无 gold 可算的样本（recall_samples=0）"),
    "precision_at_5": ("precision_samples", "无 gold 可算的样本（precision_samples=0）"),
    "citation_accuracy": ("citations_checked", "无被核验的引用（citations_checked=0）"),
    "abstain_accuracy": ("abstain_samples", "本轮无 abstain 样本"),
    "deny_interception": ("deny_samples", "本轮无 deny 样本"),
}
RAW_REASONS: dict[str, tuple[str, str]] = {
    "retrieval_p95_ms": ("latency_ms", "无 latency_ms 记录"),
    "chat_total_p95_ms": ("chat_total_ms", "无 chat_total_ms 记录"),
    "chat_ttft_p95_ms": ("chat_ttft_ms", "无 chat_ttft_ms 记录"),
}

CHECK_OK = "OK"
CHECK_WARN = "WARN"
CHECK_FAIL = "FAIL"
CHECK_SKIP = "SKIP"


class InputError(Exception):
    """输入目录/文件不可用。统一转 exit 2，且**不生成报告**。"""


# ────────────────────────────────────────────────────────────────────────────
# run_eval 复用层
# ────────────────────────────────────────────────────────────────────────────

_RUNNER: ModuleType | None = None


def runner_module() -> ModuleType:
    """import `eval/run_eval.py`，而不是在报告里再抄一份指标公式。

    抄一份的代价不是多写几行，而是两处口径迟早漂：`recall` 的分母、`abstain` 的退化
    判定、`citation` 的三条规则，任何一处被顺手改「好看一点」，对比报告的数就和单轮
    `report.md` 对不上了。所以宁可显式依赖，并在接口改名时**大声失败**。
    """
    global _RUNNER
    if _RUNNER is not None:
        return _RUNNER
    eval_dir = Path(__file__).resolve().parent
    if str(eval_dir) not in sys.path:
        sys.path.insert(0, str(eval_dir))
    try:
        module = importlib.import_module("run_eval")
    except ImportError as exc:
        raise InputError(
            "无法 import eval/run_eval.py（对比报告复用它的指标口径，不另抄一份）："
            f"{type(exc).__name__}: {exc}。请用 `uv run --frozen python eval/report.py`。"
        ) from exc
    missing = [name for name in RUNNER_API if not hasattr(module, name)]
    if missing:
        raise InputError(
            "eval/run_eval.py 的接口变了，report.py 无法复用它算指标；缺：" + "、".join(missing)
        )
    _RUNNER = module
    return module


# ────────────────────────────────────────────────────────────────────────────
# 输入：两个结果目录
# ────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class RunSide:
    """一个结果目录的**宽容**加载结果：缺什么记什么，不抛异常（目录不存在除外）。"""

    label: str
    root: Path
    summary: dict[str, Any] | None = None
    summary_error: str | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    raw_error: str | None = None
    recomputed: dict[str, Any] | None = None
    recompute_error: str | None = None
    has_report: bool = False

    @property
    def summary_ok(self) -> bool:
        return self.summary is not None

    @property
    def raw_ok(self) -> bool:
        return self.raw_error is None


def load_side(label: str, root: Path) -> RunSide:
    """读一侧产物。目录本身不存在是硬错误；目录里缺文件只记下来，留给报告写「待填」。"""
    if not root.exists():
        raise InputError(f"{label} 结果目录不存在：{_absolute(root)}")
    if not root.is_dir():
        raise InputError(f"{label} 不是目录：{_absolute(root)}")
    side = RunSide(label=label, root=root.resolve())
    _load_summary(side)
    _load_raw(side)
    side.has_report = (side.root / REPORT_FILE).is_file()
    if side.records:
        repeats = _repeats_of(side)
        try:
            side.recomputed = runner_module().aggregate(side.records, repeats=repeats)
        except Exception as exc:  # 复算只是交叉校验，炸了不该让报告生不出来
            side.recompute_error = f"raw.jsonl 复算失败：{type(exc).__name__}: {exc}"
    return side


def _load_summary(side: RunSide) -> None:
    path = side.root / SUMMARY_FILE
    if not path.is_file():
        side.summary_error = f"缺 {SUMMARY_FILE}"
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        side.summary_error = f"{SUMMARY_FILE} 不可读：{type(exc).__name__}: {exc}"
        return
    if not isinstance(payload, dict):
        side.summary_error = f"{SUMMARY_FILE} 顶层不是对象（{type(payload).__name__}）"
        return
    side.summary = payload


def _load_raw(side: RunSide) -> None:
    """逐行读 raw.jsonl。有一行坏就**整侧作废**：静默跳过坏行会让「20 条」变成「19 条」，
    指标会变好看，且没人看得出来。"""
    path = side.root / RAW_FILE
    if not path.is_file():
        side.raw_error = f"缺 {RAW_FILE}"
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        side.raw_error = f"{RAW_FILE} 不可读：{type(exc).__name__}: {exc}"
        return
    records: list[dict[str, Any]] = []
    problems: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"第 {line_no} 行不是合法 JSON（{exc.msg}）")
            continue
        if not isinstance(item, dict):
            problems.append(f"第 {line_no} 行不是对象")
            continue
        if not item.get("sample_id"):
            problems.append(f"第 {line_no} 行缺 sample_id")
            continue
        records.append(item)
    if problems:
        side.raw_error = f"{RAW_FILE} 有 {len(problems)} 行不可用：" + "；".join(problems[:3])
        return
    if not records:
        side.raw_error = f"{RAW_FILE} 是空的"
        return
    side.records = records


def _repeats_of(side: RunSide) -> int:
    summary = side.summary or {}
    value = summary.get("repeats")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return max((_int_or(item.get("repeat"), 1) for item in side.records), default=1)


def _absolute(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - Windows 上极少数权限/符号链接场景
        return path


# ────────────────────────────────────────────────────────────────────────────
# 指标：读 summary.json，再用 raw.jsonl 复算交叉验证
# ────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class MetricValue:
    """一个格子的值 + 它是怎么来的。`value is None` 时 `note` 必须写清缺什么。"""

    value: float | None
    source: str
    note: str = ""

    @property
    def pending(self) -> bool:
        return self.value is None


def _summary_metric(side: RunSide, name: str) -> Any:
    block = (side.summary or {}).get("metrics")
    if not isinstance(block, dict):
        return None
    item = block.get(name)
    return item.get("value") if isinstance(item, dict) else None


def _recomputed_metric(side: RunSide, name: str) -> Any:
    block = (side.recomputed or {}).get("metrics")
    if not isinstance(block, dict):
        return None
    item = block.get(name)
    return item.get("value") if isinstance(item, dict) else None


def _has_metric_key(side: RunSide, name: str) -> bool:
    block = (side.summary or {}).get("metrics")
    return isinstance(block, dict) and isinstance(block.get(name), dict)


def metric_value(side: RunSide, name: str) -> MetricValue:
    """取值优先级：summary.json（并核对复算）→ raw.jsonl 复算 → 待填。

    「summary 有值、复算也有值、两者不等」不取平均、不挑一个：那是两份产物不属于同一次
    运行，报告必须把这件事说出来，所以保留 summary 的值并标注不一致。
    """
    summary_value = _float_or_none(_summary_metric(side, name))
    raw_value = _float_or_none(_recomputed_metric(side, name))
    if summary_value is not None and raw_value is not None:
        if not _close(summary_value, raw_value):
            return MetricValue(
                summary_value,
                SUMMARY_FILE,
                f"与 raw.jsonl 复算不一致（复算={raw_value:.6f}）",
            )
        return MetricValue(summary_value, SUMMARY_FILE, "已与 raw.jsonl 复算一致")
    if summary_value is not None:
        return MetricValue(summary_value, SUMMARY_FILE, "无 raw 复算可交叉验证")
    if raw_value is not None:
        return MetricValue(raw_value, "raw.jsonl 复算", "summary.json 无该值，按 raw 复算")
    return MetricValue(None, PENDING, missing_reason(side, name))


def missing_reason(side: RunSide, name: str) -> str:
    """待填的原因。顺序很重要：先说「本来就没有分母」，再说「文件缺了」。"""
    counts = (side.summary or {}).get("counts")
    if isinstance(counts, dict) and name in COUNT_REASONS:
        key, text = COUNT_REASONS[name]
        if _int_or(counts.get(key), 0) == 0:
            return text
    if name in RAW_REASONS and side.records:
        key, text = RAW_REASONS[name]
        if not any(item.get(key) is not None for item in side.records):
            return text
    if side.summary is None:
        return side.summary_error or f"缺 {SUMMARY_FILE}"
    if _has_metric_key(side, name):
        return f"{SUMMARY_FILE} 的 metrics.{name}.value 为 null，raw 复算也为空"
    if side.recompute_error:
        return side.recompute_error
    return f"{SUMMARY_FILE} 无 metrics.{name}"


def _denominator(side: RunSide, name: str) -> str:
    counts = (side.summary or {}).get("counts")
    if not isinstance(counts, dict):
        # 分母来自 summary.counts；缺了就从 raw 也算不出来（要评测集），所以写 `?` 不猜
        return "?"
    key = DENOMINATOR_KEYS.get(name, "samples")
    value = counts.get(key)
    return "?" if value is None else str(value)


def delta_cell(base: MetricValue, cand: MetricValue, kind: str) -> str:
    """差值 = 候选 − 基线。任一侧没有数就不算：0 会被读成「没变化」。"""
    if base.value is None or cand.value is None:
        return f"{PENDING}（缺一侧数值）"
    diff = cand.value - base.value
    return f"{diff:+.1f} ms" if kind == "ms" else f"{diff:+.3f}"


# ────────────────────────────────────────────────────────────────────────────
# 评测集：只用来补「问题原文」（raw.jsonl 里没有 query 字段）
# ────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class DatasetInfo:
    """评测集的路径 / 内容哈希 / id→问题 映射。哈希是**本次重算**的，runner 不落盘。"""

    path: Path | None = None
    declared: str | None = None
    sha256: str | None = None
    questions: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def declared_dataset_path(side: RunSide, override: Path | None) -> Path | None:
    """配置里的 `eval_dataset` 是相对路径时，依次试 cwd / 仓库根。

    与 `run_eval.resolve_dataset_path` 的三段查找（cwd / 配置目录 / 其上一级）不完全相同：
    报告只拿得到配置里的字符串、拿不到配置文件所在目录，所以退化成「cwd / 仓库根」，
    并把两个候选都写进报告，避免「找不到文件」变成一句「路径不对」。
    """
    if override is not None:
        return override if override.is_absolute() else Path.cwd() / override
    config = (side.summary or {}).get("config")
    declared = None
    if isinstance(config, dict):
        raw = config.get("eval_dataset")
        declared = str(raw).strip() if raw is not None else None
    if not declared:
        return None
    candidate = Path(declared)
    if candidate.is_absolute():
        return candidate
    for base in (Path.cwd(), REPO_ROOT):
        if (base / candidate).is_file():
            return (base / candidate).resolve()
    return REPO_ROOT / candidate


def _declared_dataset(side: RunSide) -> str:
    config = (side.summary or {}).get("config")
    if not isinstance(config, dict):
        return ""
    return str(config.get("eval_dataset") or "").strip()


def load_dataset(path: Path | None) -> DatasetInfo:
    info = DatasetInfo(path=path, declared=None)
    if path is None:
        info.error = "两侧 summary.json 都没有 config.eval_dataset，且未传 --eval-dataset"
        return info
    if not path.is_file():
        info.error = f"评测集文件不存在：{path}"
        return info
    try:
        data = path.read_bytes()
    except OSError as exc:
        info.error = f"评测集不可读：{type(exc).__name__}: {exc}"
        return info
    # 按字节算 sha256（不是 decode 后的文本）：换行符/编码差异会改变文件内容，
    # 而「同一评测集」这个断言正是在问「文件内容是否同一个」。
    info.sha256 = hashlib.sha256(data).hexdigest()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        info.error = f"评测集不是 UTF-8：{exc}"
        return info
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            info.error = f"评测集第 {line_no} 行不是合法 JSON"
            return info
        if not isinstance(item, dict):
            continue
        sample_id = item.get("id") or item.get("sample_id")
        if sample_id:
            info.questions[str(sample_id)] = str(item.get("query") or "")
    if not info.questions:
        info.error = "评测集里没有可用的 id/query（问题列只能写待填）"
    return info


# ────────────────────────────────────────────────────────────────────────────
# 逐样本判据与退化清单
# ────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Degradation:
    sample_id: str
    category: str
    signal: str
    base: float
    cand: float


def _group_by_sample(records: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get("sample_id")), []).append(record)
    return grouped


def _mean_of(values: Sequence[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return None if not present else sum(present) / len(present)


def sample_signals(runs: Sequence[dict[str, Any]]) -> dict[str, float | None]:
    """一条样本在一侧的多轮判据（`repeats>1` 取均值）。

    判据全部复用 runner 的公式，不另立标准：`fact_hit` 与 `collect_failures` 的
    `FACT_MISSING` 同口径（`expect_facts` **任一**出现在回答正文即为命中），所以一条
    样本不会出现「台账说 FACT_MISSING、退化清单说事实命中」这种自相矛盾。
    """
    runner = runner_module()
    expect = str(runs[0].get("expect") or "")
    signals: dict[str, float | None] = {}
    if expect == "answer":
        signals["recall_at_5"] = _mean_of([runner.recall_at_5(run) for run in runs])
        signals["precision_at_5"] = _mean_of([runner.precision_at_5(run) for run in runs])
        signals["citation_pass"] = _mean_of([runner.audit_citations(run).accuracy for run in runs])
        signals["fact_hit"] = _mean_of(
            [
                1.0
                if any(fact in str(run.get("answer_text") or "") for fact in run["expect_facts"])
                else 0.0
                for run in runs
                if run.get("expect_facts")
            ]
        )
    elif expect == "abstain":
        signals["abstained"] = _mean_of([1.0 if runner.is_abstained(run) else 0.0 for run in runs])
    elif expect == "deny":
        signals["deny_intercepted"] = _mean_of(
            [1.0 if runner.deny_intercepted(run) else 0.0 for run in runs]
        )
    return signals


def find_degradations(base: RunSide, cand: RunSide) -> tuple[list[Degradation], list[str], int]:
    """逐样本比较，返回 (退化清单, 不可比说明, 实际参与比较的样本数)。

    只比两侧都有的 `sample_id`；只在一侧出现的样本不猜、也不按缺失算 0，而是列进说明。
    """
    base_groups = _group_by_sample(base.records)
    cand_groups = _group_by_sample(cand.records)
    shared = sorted(set(base_groups) & set(cand_groups))
    only_base = sorted(set(base_groups) - set(cand_groups))
    only_cand = sorted(set(cand_groups) - set(base_groups))
    rows: list[Degradation] = []
    comparable = 0
    for sample_id in shared:
        base_signals = sample_signals(base_groups[sample_id])
        cand_signals = sample_signals(cand_groups[sample_id])
        if not base_signals or not cand_signals:
            continue
        comparable += 1
        category = str(base_groups[sample_id][0].get("category") or "")
        for name, label in SIGNAL_LABELS.items():
            left = base_signals.get(name)
            right = cand_signals.get(name)
            if left is None or right is None:
                continue
            if right < left - DEGRADE_EPS:
                rows.append(Degradation(sample_id, category, label, left, right))
    notes: list[str] = []
    if only_base:
        notes.append(f"{len(only_base)} 条样本只在 baseline：{_join_ids(only_base)}")
    if only_cand:
        notes.append(f"{len(only_cand)} 条样本只在 candidate：{_join_ids(only_cand)}")
    return rows, notes, comparable


# ────────────────────────────────────────────────────────────────────────────
# 失败样本台账
# ────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class LedgerRow:
    side: str
    sample_id: str
    category: str
    expect: str
    codes: list[str]
    classes: str
    hints: str
    behavior: str
    question: str


@dataclass(slots=True)
class ClassCount:
    """一级类的样本数。`None` = 该侧 raw.jsonl 不可用，**不是 0**。"""

    name: str
    base: int | None
    cand: int | None
    codes: list[str]


def build_ledger(side: RunSide, questions: dict[str, str]) -> list[LedgerRow]:
    """复用 runner 的自动归类，按样本合并 repeats，再映射到计划 §3.7 的九类。"""
    runner = runner_module()
    grouped: dict[str, dict[str, Any]] = {}
    for item in runner.collect_failures(side.records):
        sample_id = str(item.get("sample_id"))
        entry = grouped.setdefault(
            sample_id,
            {"codes": set(), "details": [], "category": item.get("category")},
        )
        entry["codes"].update(str(code) for code in (item.get("codes") or []))
        detail = str(item.get("detail") or "")
        if detail and detail not in entry["details"]:
            entry["details"].append(detail)

    by_sample = _group_by_sample(side.records)
    rows: list[LedgerRow] = []
    for sample_id, entry in grouped.items():
        runs = by_sample.get(sample_id) or []
        codes = sorted(entry["codes"])
        classes, hints = classify_codes(codes)
        rows.append(
            LedgerRow(
                side=side.label,
                sample_id=sample_id,
                category=str(entry["category"] or ""),
                expect=str((runs[0].get("expect") if runs else None) or ""),
                codes=codes,
                classes=classes,
                hints=hints,
                behavior=describe_behavior(runs[0]) if runs else PENDING + "（无 raw 行）",
                question=questions.get(sample_id) or f"{PENDING}（评测集无此 id）",
            )
        )
    rows.sort(key=lambda row: (_class_rank(row.classes), row.sample_id))
    return rows


def classify_codes(codes: Sequence[str]) -> tuple[str, str]:
    """二级 code → 「一级类（二级 code）」+ 疑似环节。未知 code 显式落到「九类之外」。"""
    by_class: dict[str, list[str]] = {}
    hints: list[str] = []
    for code in codes:
        class_name, hint = FAILURE_CODE_CLASS.get(
            code, (OUTSIDE_NINE, "未登记的自动归类 code，需人工判断")
        )
        by_class.setdefault(class_name, []).append(code)
        if hint not in hints:
            hints.append(hint)
    ordered = sorted(by_class.items(), key=lambda item: _class_rank(item[0]))
    label = "；".join(f"{name}（{'、'.join(sorted(items))}）" for name, items in ordered)
    return label, "；".join(hints)


def describe_behavior(record: dict[str, Any]) -> str:
    """一行说清「实际发生了什么」。只写 raw 里真的有的字段，不推断。"""
    runner = runner_module()
    expect = str(record.get("expect") or "")
    parts = [f"status={record.get('status')}"]
    if expect == "deny":
        deny = record.get("deny") or {}
        parts.append(f"query={deny.get('query_http_status')} chat={deny.get('chat_http_status')}")
        leaked = bool(deny.get("query_body_leaked") or deny.get("chat_body_leaked"))
        parts.append(f"leaked={leaked}")
    else:
        parts.append(f"results={len(record.get('results') or [])}")
        recall = runner.recall_at_5(record)
        if recall is not None:
            parts.append(f"recall@5={recall:.2f}")
        audit = runner.audit_citations(record)
        if audit.checked:
            parts.append(f"引用 {audit.verified}/{audit.checked}")
        parts.append(f"invalid_citations={record.get('invalid_citations')}")
    if record.get("error_code"):
        parts.append(f"error_code={record['error_code']}")
    return "；".join(parts)


def class_counts(
    base_rows: Sequence[LedgerRow], cand_rows: Sequence[LedgerRow], *, base: RunSide, cand: RunSide
) -> list[ClassCount]:
    """按一级类聚合（按样本去重）。某侧 raw 不可用时该列是 None，渲染成「待填」。"""
    names = list(FAILURE_CLASS_ORDER)
    if any(OUTSIDE_NINE in row.classes for row in (*base_rows, *cand_rows)):
        names.append(OUTSIDE_NINE)

    def count(rows: Sequence[LedgerRow], name: str) -> int:
        return len({row.sample_id for row in rows if name in row.classes})

    table: list[ClassCount] = []
    for name in names:
        codes = sorted(
            {
                code
                for row in (*base_rows, *cand_rows)
                if name in row.classes
                for code in row.codes
                if FAILURE_CODE_CLASS.get(code, (OUTSIDE_NINE,))[0] == name
            }
        )
        table.append(
            ClassCount(
                name=name,
                base=None if not base.raw_ok else count(base_rows, name),
                cand=None if not cand.raw_ok else count(cand_rows, name),
                codes=codes,
            )
        )
    return table


def _class_rank(label: str) -> int:
    """聚合表排序：九类按计划原表顺序，其余（九类之外/多类拼接）排最后。"""
    for index, name in enumerate(FAILURE_CLASS_ORDER):
        if label.startswith(name):
            return index
    return len(FAILURE_CLASS_ORDER)


# ────────────────────────────────────────────────────────────────────────────
# 校验：--check 用，也用来在报告头里写「可比性」
# ────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class CheckItem:
    key: str
    status: str
    message: str


def run_checks(
    base: RunSide,
    cand: RunSide,
    base_dataset: DatasetInfo,
    cand_dataset: DatasetInfo,
) -> list[CheckItem]:
    """16 项结构/可比性校验。只读，不写任何文件。"""
    runner = runner_module()
    items: list[CheckItem] = [
        CheckItem("dir.baseline", CHECK_OK, f"结果目录存在：{base.root}"),
        CheckItem("dir.candidate", CHECK_OK, f"结果目录存在：{cand.root}"),
    ]
    for side in (base, cand):
        items.append(_check_summary(side))
        items.append(_check_raw(side))
        items.append(
            CheckItem(
                f"file.report.{side.label}",
                CHECK_OK if side.has_report else CHECK_WARN,
                f"{REPORT_FILE} {'存在' if side.has_report else '缺失（人读产物；指标不依赖它）'}",
            )
        )
    items.append(_check_required_keys(base, cand))
    items.append(_check_eval_dataset(base, cand))
    items.append(_check_dataset_hash(base_dataset, cand_dataset))
    items.append(_check_question_set(base, cand))
    items.append(_check_app_commit(base, cand))
    items.append(_check_experiment_id(base, cand))
    items.append(_check_repeats(base, cand))
    items.append(_check_base_url(base, cand))
    items.append(_check_config_diff(base, cand, runner))
    items.append(_check_recompute(base, cand, runner))
    items.append(_check_deny_leak(base, cand))
    return items


def _check_summary(side: RunSide) -> CheckItem:
    key = f"file.summary.{side.label}"
    if side.summary is not None:
        return CheckItem(key, CHECK_OK, f"{SUMMARY_FILE} 可解析（{len(side.summary)} 个顶层键）")
    return CheckItem(key, CHECK_FAIL, side.summary_error or f"缺 {SUMMARY_FILE}")


def _check_raw(side: RunSide) -> CheckItem:
    key = f"file.raw.{side.label}"
    if not side.raw_ok:
        return CheckItem(key, CHECK_FAIL, side.raw_error or f"缺 {RAW_FILE}")
    groups = _group_by_sample(side.records)
    return CheckItem(
        key, CHECK_OK, f"{RAW_FILE} {len(side.records)} 行 / {len(groups)} 个 sample_id"
    )


def _check_required_keys(base: RunSide, cand: RunSide) -> CheckItem:
    problems: list[str] = []
    for side in (base, cand):
        if side.summary is None:
            continue
        missing = [name for name in REQUIRED_SUMMARY_KEYS if name not in side.summary]
        if missing:
            problems.append(f"{side.label} 缺 {', '.join(missing)}")
    if problems:
        return CheckItem("summary.keys", CHECK_FAIL, "；".join(problems))
    if base.summary is None or cand.summary is None:
        return CheckItem(
            "summary.keys", CHECK_SKIP, "有一侧 summary.json 不可用，键校验只覆盖可解析的"
        )
    return CheckItem(
        "summary.keys", CHECK_OK, "两侧都有必需顶层键（experiment_id/app_commit/config…）"
    )


def _check_eval_dataset(base: RunSide, cand: RunSide) -> CheckItem:
    left, right = _declared_dataset(base), _declared_dataset(cand)
    if not left or not right:
        return CheckItem(
            "eval_dataset",
            CHECK_SKIP,
            f"至少一侧缺 config.eval_dataset（base={left or '缺'} / cand={right or '缺'}）",
        )
    if left == right:
        return CheckItem("eval_dataset", CHECK_OK, f"两侧声明同一评测集：`{left}`")
    return CheckItem(
        "eval_dataset",
        CHECK_WARN,
        f"两侧声明的路径不同：base=`{left}` / cand=`{right}`（内容是否同一份由 dataset.hash 判）",
    )


def _check_dataset_hash(base_dataset: DatasetInfo, cand_dataset: DatasetInfo) -> CheckItem:
    if base_dataset.sha256 is None or cand_dataset.sha256 is None:
        reasons = [
            f"{label}：{info.error or '无法计算 sha256'}"
            for label, info in (("base", base_dataset), ("cand", cand_dataset))
            if info.sha256 is None
        ]
        return CheckItem("dataset.hash", CHECK_SKIP, "无法核验评测集内容：" + "；".join(reasons))
    if base_dataset.sha256 == cand_dataset.sha256:
        return CheckItem(
            "dataset.hash",
            CHECK_OK,
            f"sha256 一致：`{base_dataset.sha256[:16]}…`（本次重算，runner 未落盘该值）",
        )
    return CheckItem(
        "dataset.hash",
        CHECK_FAIL,
        f"sha256 不一致：base=`{base_dataset.sha256[:16]}…` / cand=`{cand_dataset.sha256[:16]}…`",
    )


def _check_question_set(base: RunSide, cand: RunSide) -> CheckItem:
    if not base.raw_ok or not cand.raw_ok:
        return CheckItem(
            "question_set", CHECK_SKIP, "有一侧 raw.jsonl 不可用，无法比对 sample_id 集合"
        )
    left = set(_group_by_sample(base.records))
    right = set(_group_by_sample(cand.records))
    if left == right:
        return CheckItem("question_set", CHECK_OK, f"sample_id 集合一致（{len(left)} 条）")
    only_base = sorted(left - right)
    only_cand = sorted(right - left)
    return CheckItem(
        "question_set",
        CHECK_FAIL,
        f"不一致：baseline 独有 {len(only_base)} 条 [{_join_ids(only_base)}]、"
        f"candidate 独有 {len(only_cand)} 条 [{_join_ids(only_cand)}]",
    )


def _field_value(side: RunSide, key: str) -> Any:
    return (side.summary or {}).get(key)


def _check_app_commit(base: RunSide, cand: RunSide) -> CheckItem:
    left, right = _field_value(base, "app_commit"), _field_value(cand, "app_commit")
    if left is None or right is None:
        return CheckItem("app_commit", CHECK_SKIP, "至少一侧 summary.json 无 app_commit")
    if left == right:
        return CheckItem("app_commit", CHECK_OK, f"两侧同一 commit：`{left}`")
    return CheckItem(
        "app_commit",
        CHECK_WARN,
        f"不同：base=`{left}` / cand=`{right}`——两份结果不是同一份代码，"
        "指标差不能全归给主因素（对照实验要求同一 app_commit）",
    )


def _check_experiment_id(base: RunSide, cand: RunSide) -> CheckItem:
    left, right = _field_value(base, "experiment_id"), _field_value(cand, "experiment_id")
    if left is None or right is None:
        return CheckItem("experiment_id", CHECK_SKIP, "至少一侧 summary.json 无 experiment_id")
    if left == right:
        return CheckItem("experiment_id", CHECK_OK, f"同一 A/B：`{left}`")
    return CheckItem(
        "experiment_id",
        CHECK_WARN,
        f"不同：base=`{left}` / cand=`{right}`——可能是两个不同的 A/B，确认再引用",
    )


def _check_repeats(base: RunSide, cand: RunSide) -> CheckItem:
    left, right = _field_value(base, "repeats"), _field_value(cand, "repeats")
    if left is None or right is None:
        return CheckItem("repeats", CHECK_SKIP, "至少一侧 summary.json 无 repeats")
    if left == right:
        return CheckItem("repeats", CHECK_OK, f"两侧 repeats 相同：{left}")
    return CheckItem(
        "repeats",
        CHECK_WARN,
        f"不同：base={left} / cand={right}——可见样本量口径不一致，Δ 的解释要打折",
    )


def _check_base_url(base: RunSide, cand: RunSide) -> CheckItem:
    left, right = _field_value(base, "base_url"), _field_value(cand, "base_url")
    if left is None or right is None:
        return CheckItem("base_url", CHECK_SKIP, "至少一侧 summary.json 无 base_url")
    if left == right:
        return CheckItem("base_url", CHECK_OK, f"两侧同一被测地址：`{left}`")
    return CheckItem(
        "base_url",
        CHECK_WARN,
        f"不同：base=`{left}` / cand=`{right}`——机器/部署不同会污染 P95",
    )


def _check_config_diff(base: RunSide, cand: RunSide, runner: ModuleType) -> CheckItem:
    """公平性守卫的复核：两份 config 必须恰好一个主因素不同（计划 §5.3）。"""
    base_cfg = (base.summary or {}).get("config")
    cand_cfg = (cand.summary or {}).get("config")
    if not isinstance(base_cfg, dict) or not isinstance(cand_cfg, dict):
        return CheckItem(
            "config.diff", CHECK_SKIP, "至少一侧 summary.json 缺 config，无法逐字段比对"
        )
    diff = runner.build_diff(base_cfg, cand_cfg)
    primary = [item for item in diff if item[0].split(".")[-1] in runner.PRIMARY_FACTOR_FIELDS]
    others = [item for item in diff if item[0].split(".")[-1] not in runner.PRIMARY_FACTOR_FIELDS]
    if others:
        return CheckItem(
            "config.diff",
            CHECK_FAIL,
            f"{len(others)} 个非主因素字段不同（对照实验要求逐字相同）：{_diff_text(others)}",
        )
    if len(primary) > 1:
        return CheckItem(
            "config.diff",
            CHECK_FAIL,
            f"{len(primary)} 个主因素同时不同，无法归因：{_diff_text(primary)}",
        )
    if not primary:
        return CheckItem(
            "config.diff",
            CHECK_WARN,
            "两份 config 完全相同：可能服务端主因素没切（见 eval/README.md 第 2 节·坑 1），"
            "Δ 的因果归因不成立",
        )
    key, left, right = primary[0]
    return CheckItem(
        "config.diff", CHECK_OK, f"唯一不同的主因素：{key}（base={left!r} → cand={right!r}）"
    )


def _diff_text(items: Sequence[tuple[str, Any, Any]]) -> str:
    parts = [f"{key}: base={left!r} / cand={right!r}" for key, left, right in items[:5]]
    if len(items) > 5:
        parts.append(f"…共 {len(items)} 项")
    return "；".join(parts)


def _check_recompute(base: RunSide, cand: RunSide, runner: ModuleType) -> CheckItem:
    """summary.json 与 raw.jsonl 必须来自同一次运行——复算对不上就是两份产物被混拼了。"""
    problems: list[str] = []
    checked = 0
    for side in (base, cand):
        if side.recomputed is None or side.summary is None:
            continue
        for name in runner.METRIC_ORDER:
            summary_value = _float_or_none(_summary_metric(side, name))
            raw_value = _float_or_none(_recomputed_metric(side, name))
            if summary_value is None or raw_value is None:
                continue
            checked += 1
            if not _close(summary_value, raw_value):
                problems.append(
                    f"{side.label}.{name} summary={summary_value:.6f} 复算={raw_value:.6f}"
                )
    if not checked:
        reason = base.recompute_error or cand.recompute_error or "缺 summary.json 或 raw.jsonl"
        return CheckItem("metrics.recompute", CHECK_SKIP, f"没有可交叉验证的指标：{reason}")
    if problems:
        return CheckItem(
            "metrics.recompute",
            CHECK_FAIL,
            f"{len(problems)} 项 summary.json 与 raw.jsonl 复算不一致（产物可能不是同一次运行）："
            + "；".join(problems[:5]),
        )
    return CheckItem(
        "metrics.recompute",
        CHECK_OK,
        f"{checked} 项指标 summary.json 与 raw.jsonl 复算一致（容差 {DEGRADE_EPS:g}）",
    )


def _check_deny_leak(base: RunSide, cand: RunSide) -> CheckItem:
    leaks = [side.label for side in (base, cand) if bool((side.summary or {}).get("deny_leak"))]
    if leaks:
        return CheckItem(
            "deny_leak",
            CHECK_FAIL,
            f"{'、'.join(leaks)} 存在 deny 样本返回 200 / 泄漏片段："
            "当轮指标作废（计划 §5.4 泄漏检查）",
        )
    if base.summary is None or cand.summary is None:
        return CheckItem("deny_leak", CHECK_SKIP, "至少一侧 summary.json 不可用，无法确认无泄漏")
    return CheckItem("deny_leak", CHECK_OK, "两侧 summary.deny_leak 均为 false")


def print_checks(items: Sequence[CheckItem]) -> int:
    for item in items:
        print(f"[{item.status:<4s}] {item.key:<24s} {item.message}")
    fails = sum(1 for item in items if item.status == CHECK_FAIL)
    warns = sum(1 for item in items if item.status == CHECK_WARN)
    skips = sum(1 for item in items if item.status == CHECK_SKIP)
    verdict = "可对比" if fails == 0 else "不可对比"
    print(f"[check] 结论：{verdict}（FAIL {fails} / WARN {warns} / SKIP {skips}）")
    return EXIT_OK if fails == 0 else EXIT_CHECK_FAILED


# ────────────────────────────────────────────────────────────────────────────
# 报告数据 + 渲染
# ────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class MetricRow:
    name: str
    label: str
    kind: str
    base: MetricValue
    cand: MetricValue


@dataclass(slots=True)
class ReportData:
    base: RunSide
    cand: RunSide
    base_dataset: DatasetInfo
    cand_dataset: DatasetInfo
    questions: dict[str, str]
    generated_at: str
    checks: list[CheckItem]
    metrics: list[MetricRow]
    ledger: dict[str, list[LedgerRow]]
    class_table: list[ClassCount]
    degradations: list[Degradation]
    degrade_notes: list[str]
    comparable_samples: int


def build_report_data(
    base: RunSide,
    cand: RunSide,
    base_dataset: DatasetInfo,
    cand_dataset: DatasetInfo,
    checks: list[CheckItem],
    generated_at: str,
) -> ReportData:
    questions: dict[str, str] = dict(base_dataset.questions)
    for sample_id, query in cand_dataset.questions.items():
        questions.setdefault(sample_id, query)
    metrics = [
        MetricRow(
            name=name,
            label=label,
            kind=kind,
            base=metric_value(base, name),
            cand=metric_value(cand, name),
        )
        for name, label, kind in METRIC_ROWS
    ]
    ledger: dict[str, list[LedgerRow]] = {}
    for side in (base, cand):
        ledger[side.label] = build_ledger(side, questions) if side.records else []
    degradations, degrade_notes, comparable = find_degradations(base, cand)
    return ReportData(
        base=base,
        cand=cand,
        base_dataset=base_dataset,
        cand_dataset=cand_dataset,
        questions=questions,
        generated_at=generated_at,
        checks=checks,
        metrics=metrics,
        ledger=ledger,
        class_table=class_counts(ledger[base.label], ledger[cand.label], base=base, cand=cand),
        degradations=degradations,
        degrade_notes=degrade_notes,
        comparable_samples=comparable,
    )


def source_summary(data: ReportData) -> tuple[Counter[str], list[str], list[str]]:
    """(来源计数, 待填明细, 复算不一致明细)。报告头与 stdout 共用这一份。"""
    counts: Counter[str] = Counter()
    pending: list[str] = []
    mismatched: list[str] = []
    for row in data.metrics:
        for side, metric in ((data.base, row.base), (data.cand, row.cand)):
            counts[metric.source] += 1
            if metric.value is None:
                pending.append(f"{row.label}（{side.label}：{metric.note}）")
            elif "不一致" in metric.note:
                mismatched.append(f"{row.label}（{side.label}：{metric.note}）")
    return counts, list(dict.fromkeys(pending)), list(dict.fromkeys(mismatched))


def render_report(data: ReportData) -> str:
    lines: list[str] = [
        "# Day3 A/B 对比报告（`eval/report.py` 生成，勿手改）",
        "",
        "> 指标取两轮 `summary.json` 并用各自的 `raw.jsonl` 复算核对；失败台账与退化清单取",
        "> `raw.jsonl`；问题原文取 `eval_dataset`。",
        "> **缺数据一律写「待填（缺 X）」，不写 0、不估算。**",
        "",
        "## 0 报告头",
        "",
    ]
    lines += _render_header(data)
    lines += ["", "## 1 七指标对比（计划 §3.6 / §5.5）", ""]
    lines += _render_metrics(data)
    lines += ["", "## 2 失败样本台账（计划 §3.7 九类）", ""]
    lines += _render_ledger(data)
    lines += ["", "## 3 退化清单（计划 §5.4：候选变差必须单列）", ""]
    lines += _render_degradations(data)
    lines += ["", "## 4 诚实说明（读结论前先看这节）", ""]
    lines += _render_honesty(data)
    lines += ["", "## 附录 A 口径与代码出处", ""]
    lines += _render_appendix(data)
    return "\n".join(lines) + "\n"


def _render_header(data: ReportData) -> list[str]:
    base, cand = data.base, data.cand
    counts, pending, mismatched = source_summary(data)
    lines = [
        "| 项 | baseline | candidate |",
        "| --- | --- | --- |",
        f"| 结果目录 | `{base.root}` | `{cand.root}` |",
        f"| experiment_id | {_summary_field(base, 'experiment_id')} "
        f"| {_summary_field(cand, 'experiment_id')} |",
        f"| app_commit | {_summary_field(base, 'app_commit')} "
        f"| {_summary_field(cand, 'app_commit')} |",
        f"| prompt_version | {_summary_field(base, 'prompt_version')} "
        f"| {_summary_field(cand, 'prompt_version')} |",
        f"| retrieval_strategy（配置声明） | {_summary_field(base, 'retrieval_strategy')} "
        f"| {_summary_field(cand, 'retrieval_strategy')} |",
        f"| 落盘时间（runner 的 generated_at） | {_summary_field(base, 'generated_at')} "
        f"| {_summary_field(cand, 'generated_at')} |",
        f"| 样本数（unique / raw 行） | {_sample_counts(base)} | {_sample_counts(cand)} |",
        f"| repeats | {_summary_field(base, 'repeats')} | {_summary_field(cand, 'repeats')} |",
        f"| 产物齐全度 | {_artifacts(base)} | {_artifacts(cand)} |",
        "",
        "**评测集（eval_dataset）**",
        "",
        f"- baseline：{_dataset_line(base, data.base_dataset)}",
        f"- candidate：{_dataset_line(cand, data.cand_dataset)}",
        "",
        f"- 本报告生成时间：`{data.generated_at}`",
        f"- 主因素差异（两份 `config` 逐字段 diff）：{_primary_factor_line(data)}",
        f"- 可比性（`--check` 同源，共 {len(data.checks)} 项）：{_check_summary_line(data)}",
        "",
        "**数字来源（7 行 × 2 侧 = 14 个格子）**",
        "",
        f"- 来自 `{SUMMARY_FILE}` 且与 `{RAW_FILE}` 复算一致：{counts.get(SUMMARY_FILE, 0)} 个",
        f"- 来自 `{RAW_FILE}` 复算（`{SUMMARY_FILE}` 无该值）："
        f"{counts.get('raw.jsonl 复算', 0)} 个",
        f"- 待填：{counts.get(PENDING, 0)} 个",
    ]
    if pending:
        lines.append("- 待填明细：" + "；".join(pending))
    if mismatched:
        lines.append("- **复算不一致（不可直接引用）**：" + "；".join(mismatched))
    return lines


def _summary_field(side: RunSide, key: str) -> str:
    value = (side.summary or {}).get(key)
    if value is None or str(value).strip() == "":
        return f"{PENDING}（{SUMMARY_FILE} 无 {key}）"
    return f"`{value}`"


def _p95_rank_of(side: RunSide) -> tuple[int, int] | None:
    """nearest-rank 的 P95 落在第几慢（`run_eval.percentile_p95` 的 `ceil(0.95n)`）。"""
    counts = (side.summary or {}).get("counts")
    rows = counts.get("samples") if isinstance(counts, dict) else None
    if not isinstance(rows, int):
        rows = len(side.records) or None
    if not rows:
        return None
    return rows, (95 * rows + 99) // 100


def _sample_counts(side: RunSide) -> str:
    counts = (side.summary or {}).get("counts")
    unique = counts.get("unique_samples") if isinstance(counts, dict) else None
    rows = counts.get("samples") if isinstance(counts, dict) else None
    if unique is None and side.records:
        unique = len(_group_by_sample(side.records))
        rows = len(side.records)
    if unique is None:
        return f"{PENDING}（缺 {SUMMARY_FILE} 的 counts 与 {RAW_FILE}）"
    return f"{unique} 条 / {rows if rows is not None else '?'} 行"


def _artifacts(side: RunSide) -> str:
    return "、".join(
        [
            f"{SUMMARY_FILE} {'有' if side.summary is not None else '缺'}",
            f"{RAW_FILE} {'有' if side.raw_ok else '缺'}",
            f"{REPORT_FILE} {'有' if side.has_report else '缺'}",
        ]
    )


def _dataset_line(side: RunSide, info: DatasetInfo) -> str:
    declared = _declared_dataset(side) or f"{PENDING}（config 无 eval_dataset）"
    if info.path is None:
        return f"`{declared}` → {PENDING}（{info.error}）"
    tail = f"sha256 `{info.sha256}`（本次重算）" if info.sha256 else f"{PENDING}（{info.error}）"
    return f"`{declared}` → `{info.path}`，{tail}"


def _primary_factor_line(data: ReportData) -> str:
    item = next((item for item in data.checks if item.key == "config.diff"), None)
    if item is None:
        return f"{PENDING}（未做 config 比对）"
    return item.message


def _check_summary_line(data: ReportData) -> str:
    counts = Counter(item.status for item in data.checks)
    text = (
        f"{CHECK_FAIL} {counts.get(CHECK_FAIL, 0)} / {CHECK_WARN} {counts.get(CHECK_WARN, 0)} / "
        f"{CHECK_SKIP} {counts.get(CHECK_SKIP, 0)}"
    )
    flagged = [item for item in data.checks if item.status in (CHECK_FAIL, CHECK_WARN)]
    if not flagged:
        return text
    detail = "；".join(f"{item.key}={item.status}" for item in flagged)
    return f"{text} → {detail}（明细用 `--check` 看）"


def _render_metrics(data: ReportData) -> list[str]:
    lines = [
        "| 指标 | baseline | candidate | 差值（cand − base） | 说明（分母 base / cand） |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in data.metrics:
        note = f"{_denominator(data.base, row.name)} / {_denominator(data.cand, row.name)}"
        if "不一致" in row.base.note or "不一致" in row.cand.note:
            note += "；**复算不一致，见 §4**"
        base_cell = _metric_cell(row.base, row.kind)
        cand_cell = _metric_cell(row.cand, row.kind)
        delta = delta_cell(row.base, row.cand, row.kind)
        lines.append(f"| {row.label} | {base_cell} | {cand_cell} | {delta} | {note} |")
    lines += [
        "",
        "注：",
        "- 比率类为 0–1 小数（`0.800` = 80.0%），`差值` 是同一单位的绝对差"
        "（`+0.067` = +6.7 个百分点）。",
        "- `检索 P95` = `retrieval_p95_ms`（单次 /query 端到端，nearest-rank）；"
        "`问答 P95` = `chat_total_p95_ms`（/chat 到 done 的总时长）。",
        f"- 未列 `chat_ttft_p95_ms`：计划把「问答 P95」定义为总时长；TTFT 见单轮 `{REPORT_FILE}`。",
        "- `说明` 列的分母来自 `summary.counts`（P95 取 raw 行数），`?` 表示该侧 counts 缺失。",
    ]
    return lines


def _metric_cell(metric: MetricValue, kind: str) -> str:
    if metric.value is None:
        return f"{PENDING}（{metric.note}）"
    return f"{metric.value:.1f} ms" if kind == "ms" else f"{metric.value:.3f}"


def _render_ledger(data: ReportData) -> list[str]:
    lines = [
        "> 自动归类（复用 `run_eval.collect_failures` 的二级 code），**需人工复核**；"
        "计数按样本去重（`repeats>1` 不重复计），一条样本可同时落进多个一级类。",
        "",
        "### 2.1 按一级类聚合",
        "",
        "| 一级类 | baseline 样本 | candidate 样本 | 二级 code（两侧合计） |",
        "| --- | --- | --- | --- |",
    ]
    for item in data.class_table:
        lines.append(
            f"| {item.name} | {_count_cell(item.base, data.base)} "
            f"| {_count_cell(item.cand, data.cand)} | {'、'.join(item.codes) or '—'} |"
        )

    lines += ["", "### 2.2 失败明细", ""]
    rows = [*data.ledger[data.base.label], *data.ledger[data.cand.label]]
    if not rows:
        missing = [side.label for side in (data.base, data.cand) if not side.raw_ok]
        if missing:
            lines.append(
                f"{PENDING}：{' / '.join(missing)} 的 `{RAW_FILE}` 不可用（台账整张表缺数据）。"
            )
        else:
            lines.append(
                "两侧都没有自动识别出失败样本。**这不等于没有失败**：自动归类只看几条硬信号，"
                "且 `abstain`/`deny` 的判定各自有专属口径（见附录 A）。"
            )
        return lines
    lines += [
        "| 来源 | 失败 id | 问题 | 实际行为 | 归类（一级类 / 二级 code） | 疑似环节 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        cells = (
            f"| {row.side} | {row.sample_id} | {_cell_text(row.question)} "
            f"| {_cell_text(row.behavior)} | {_cell_text(row.classes)} | {_cell_text(row.hints)} |"
        )
        lines.append(cells)
    return lines


def _count_cell(value: int | None, side: RunSide) -> str:
    if value is None:
        return f"{PENDING}（缺 {RAW_FILE}）"
    return str(value)


def _render_degradations(data: ReportData) -> list[str]:
    lines = [
        "逐样本判据（每侧按 repeats 取均值，公式复用 `run_eval`）：",
        f"- answer 类：{SIGNAL_SCOPE['answer']}"
        "（`fact_hit` 与 `FACT_MISSING` 同口径：任一 fact 命中即 1）",
        f"- abstain 类：{SIGNAL_SCOPE['abstain']}"
        f"（category ∈ no_evidence / {SIGNAL_SCOPE['injection']} / {SIGNAL_SCOPE['poison']}；"
        "`is_abstained`，status 缺失时退回拒答文案）",
        f"- deny 类：{SIGNAL_SCOPE['deny']}（category = {SIGNAL_SCOPE['privilege']}；"
        "`deny_intercepted`：两端点 403/404 且响应体无片段）",
        f"- **下降即记为退化**（容差 {DEGRADE_EPS:g}）；不设复合分数、不做加权，"
        "避免把口径藏进权重。",
        "",
    ]
    if not data.base.raw_ok or not data.cand.raw_ok:
        missing = [side.label for side in (data.base, data.cand) if not side.raw_ok]
        lines.append(f"{PENDING}：{' / '.join(missing)} 的 `{RAW_FILE}` 不可用，无法逐样本比对。")
        return lines
    if not data.degradations:
        lines.append(
            f"两侧共 {data.comparable_samples} 条样本参与逐样本比较，所有判据均未下降。"
            "**这不等于「候选更好」**：结论要看 §1 的整体指标与 §4 的样本量说明。"
        )
    else:
        lines += [
            "| 失败 id | 类别 | 退化项 | baseline | candidate |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in data.degradations:
            lines.append(
                f"| {item.sample_id} | {item.category} | {item.signal} "
                f"| {item.base:.3f} | {item.cand:.3f} |"
            )
        lines += [
            "",
            f"共 {len(data.degradations)} 条退化，涉及 "
            f"{len({item.sample_id for item in data.degradations})} 条样本；"
            "「为什么会变差」需人工读两边 raw.jsonl 后写进 `docs/Day03/04-优化前后对比.md`，"
            "本文件不猜原因。",
        ]
    if data.degrade_notes:
        lines += ["", "不可比样本（只在一侧出现，未计入上面的比较）：", ""]
        lines += [f"- {note}" for note in data.degrade_notes]
    return lines


def _render_honesty(data: ReportData) -> list[str]:
    counts, pending, mismatched = source_summary(data)
    lines = [
        f"- 样本量：baseline {_sample_counts(data.base)} / candidate {_sample_counts(data.cand)}。"
    ]
    ranks = [(side.label, _p95_rank_of(side)) for side in (data.base, data.cand)]
    known = [(label, value) for label, value in ranks if value is not None]
    if known:
        detail = "；".join(f"{label} n={n} → 第 {rank} 慢" for label, (n, rank) in known)
        lines.append(
            "- P95 用 nearest-rank（排序后取 `ceil(0.95n)-1`），取到的是真实观测值、不是插值："
            + detail
            + "。样本少时它等于某一条的延迟，不要当性能基线。"
        )
    else:
        lines.append(f"- P95 用 nearest-rank（`ceil(0.95n)-1`），取第几慢：{PENDING}（缺样本数）。")
    repeats = {side.label: _field_value(side, "repeats") for side in (data.base, data.cand)}
    if all(value is not None for value in repeats.values()):
        values = set(repeats.values())
        if values == {1}:
            lines.append("- `repeats=1`：`min == max` 只说明只跑了一次，**波动无意义**。")
        elif len(values) == 1:
            lines.append(
                f"- `repeats={next(iter(values))}`：`min/max` 是同配置多次运行的极值，"
                "不是置信区间。"
            )
        else:
            lines.append(
                f"- 两侧 repeats 不同（{repeats['baseline']} vs {repeats['candidate']}）："
                "可见样本量口径不一致，Δ 的解释要打折。"
            )
    else:
        lines.append(f"- repeats：{PENDING}（至少一侧 `{SUMMARY_FILE}` 缺该字段）。")

    warnings = _inherited_warnings(data)
    if warnings:
        lines += ["", "**两侧 `summary.json` 自带的告警（原文转移，未改写）**", ""]
        lines += [f"- {item}" for item in warnings]

    lines += ["", "**本报告新增的待确认项**", ""]
    lines.append(f"- 待填（{counts.get(PENDING, 0)} 个指标格子）：" + ("；".join(pending) or "无"))
    lines.append(
        f"- 复算不一致（{len(mismatched)} 项）："
        + ("；".join(mismatched) if mismatched else "无（summary.json 与 raw.jsonl 对得上）")
    )
    flagged = [item for item in data.checks if item.status in (CHECK_FAIL, CHECK_WARN)]
    lines.append(
        "- 可比性告警："
        + (
            "；".join(f"{item.key}（{item.status}）{item.message}" for item in flagged)
            or "无（`--check` 全部 OK）"
        )
    )
    leaks = [side.label for side in (data.base, data.cand) if (side.summary or {}).get("deny_leak")]
    if leaks:
        lines.append(
            f"- **{('、'.join(leaks))} 出现 deny 泄漏：当轮指标作废，先修安全项再谈分数。**"
        )
    lines += [
        "",
        "**结论边界**：本文件只做数据呈现。「候选是否更好」的结论写进 "
        "`docs/Day03/04-优化前后对比.md`，且必须连同样本量、`app_commit`、`eval_dataset` "
        "与上表的待填项一起引用；`embedding_backend=hashing` 或 `--limit` 的产物不得对外。",
    ]
    return lines


def _inherited_warnings(data: ReportData) -> list[str]:
    collected: list[str] = []
    for side in (data.base, data.cand):
        warnings = (side.summary or {}).get("warnings")
        if not isinstance(warnings, list):
            continue
        for warning in warnings:
            text = str(warning).strip()
            if text and text not in collected:
                collected.append(text)
    return collected


def _render_appendix(data: ReportData) -> list[str]:
    runner = runner_module()
    lines = [
        "### A.1 数据从哪来",
        "",
        "| 数据 | 来源 | 说明 |",
        "| --- | --- | --- |",
        f"| 七指标 | `{SUMMARY_FILE}` → `metrics.<name>.value` | 用 `{RAW_FILE}` 复算交叉验证；"
        "不一致会标注 |",
        f"| 失败台账 / 退化清单 | `{RAW_FILE}` | 复用 `run_eval` 的 "
        "`collect_failures` 与判据函数 |",
        "| 问题原文 | `eval_dataset`（`config.eval_dataset`） "
        "| `raw.jsonl` 里没有 `query` 字段，按 `id` join |",
        "| 评测集哈希 | 本次重算 `eval_dataset` 的 sha256 | runner **不落盘**该值，报告里已标注 |",
        "",
        "### A.2 七指标定义（取自 `run_eval.METRIC_DEFINITIONS`）",
        "",
        "| 指标 | 定义 |",
        "| --- | --- |",
    ]
    for name, label, _kind in METRIC_ROWS:
        lines.append(f"| {label} | {runner.METRIC_DEFINITIONS.get(name, '（run_eval 无定义）')} |")

    lines += [
        "",
        "### A.3 九类 ↔ 二级 code ↔ 疑似环节",
        "",
        "| 一级类 | 二级 code | 疑似环节 |",
        "| --- | --- | --- |",
    ]
    for name in (*FAILURE_CLASS_ORDER, OUTSIDE_NINE):
        codes = sorted(
            code for code, (class_name, _hint) in FAILURE_CODE_CLASS.items() if class_name == name
        )
        for code in codes:
            lines.append(f"| {name} | `{code}` | {FAILURE_CODE_CLASS[code][1]} |")
    lines += [
        "",
        "### A.4 待填与复算的判定规则",
        "",
        f"1. 指标格子：`{SUMMARY_FILE}` 有值 → 用它；同时用 `{RAW_FILE}` 复算，差值超过 "
        f"`{DEGRADE_EPS:g}` 就标「不一致」。",
        f"2. `{SUMMARY_FILE}` 缺该值、而 `{RAW_FILE}` 复算得出 → 用复算值并标来源。",
        "3. 两者都没有 → 写「待填（原因）」；原因优先取分母（如「本轮无 deny 样本」），"
        "其次取文件缺失。**任何情况下都不写 0**：0 是一个实测值，不是「不知道」。",
        "",
    ]
    return lines


def _cell_text(text: str) -> str:
    """markdown 表格单元格：竖线会截断列，换行会截断行，都要换掉。"""
    return text.replace("|", "\\|").replace("\n", " ")


def _join_ids(ids: Sequence[str], limit: int = 5) -> str:
    if not ids:
        return "无"
    shown = "、".join(ids[:limit])
    return shown if len(ids) <= limit else f"{shown}…（共 {len(ids)} 条）"


# ────────────────────────────────────────────────────────────────────────────
# 小工具
# ────────────────────────────────────────────────────────────────────────────


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _int_or(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=DEGRADE_EPS, abs_tol=DEGRADE_EPS)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ────────────────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eval/report.py",
        description="把两份 run_eval.py 结果目录对比成一份可核对的 markdown 报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  uv run --frozen python eval/report.py --check "
            "eval/results/base-20261030 eval/results/cand-20261030\n"
            "  uv run --frozen python eval/report.py "
            "eval/results/base-20261030 eval/results/cand-20261030\n"
        ),
    )
    parser.add_argument("baseline", type=Path, help="基线结果目录（run_eval.py --out 指的那个）")
    parser.add_argument("candidate", type=Path, help="候选结果目录")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验两目录结构是否完整可对比（同一评测集/哈希/题目集/commit），不写报告",
    )
    parser.add_argument(
        "--out", type=Path, help=f"报告路径（默认 {DEFAULT_REPORT_DIR}/compare-<时间戳>.md）"
    )
    parser.add_argument(
        "--eval-dataset",
        type=Path,
        help="覆盖配置里的 eval_dataset 路径（配置里的相对路径解析不到时用）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        base = load_side("baseline", args.baseline)
        cand = load_side("candidate", args.candidate)
        base_dataset = load_dataset(declared_dataset_path(base, args.eval_dataset))
        cand_dataset = load_dataset(declared_dataset_path(cand, args.eval_dataset))
        checks = run_checks(base, cand, base_dataset, cand_dataset)
        if args.check:
            return print_checks(checks)
        data = build_report_data(
            base, cand, base_dataset, cand_dataset, checks, generated_at=_now()
        )
        out_path = args.out or (DEFAULT_REPORT_DIR / f"compare-{_stamp()}.md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_report(data), encoding="utf-8")
    except InputError as exc:
        print(f"[input] {exc}", file=sys.stderr)
        return EXIT_INPUT

    counts, pending, mismatched = source_summary(data)
    print(f"[report] baseline  = {base.root}")
    print(f"[report] candidate = {cand.root}")
    print(
        f"[report] 指标格子 {sum(counts.values())} 个：{SUMMARY_FILE} {counts.get(SUMMARY_FILE, 0)}"
        f" / raw 复算 {counts.get('raw.jsonl 复算', 0)} / 待填 {counts.get(PENDING, 0)}"
    )
    print(
        f"[report] 失败样本台账 {sum(len(rows) for rows in data.ledger.values())} 条；"
        f"退化 {len(data.degradations)} 条；复算不一致 {len(mismatched)} 项"
    )
    for item in checks:
        if item.status in (CHECK_FAIL, CHECK_WARN):
            print(f"[{item.status.lower()}] {item.key}: {item.message}")
    print(f"[report] 已写入：{out_path.resolve()}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
