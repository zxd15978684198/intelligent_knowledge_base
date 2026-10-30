"""`eval/report.py` 的单测：钉死「不编数字」这条红线。

为什么这些用例值得占 tests/unit 的位置：报告器输出的每个格子都会被抄进
`docs/Day03/04-优化前后对比.md` 的结论，而「缺数据写 0」「只比一侧有的样本」
「候选变差没进清单」这三类错误，人几乎不可能从生成的 markdown 里看出来——
它们长得很像一个正常数字。所以这里用**合成 fixture**把口径钉成断言：

- fixture 全部由 `tmp_path` 内即时写出的 JSON 组成（见 `_record` / `_make_side`），
  文件里的 `experiment_id` / `app_commit` 都带 `fixture` 标记，**不是实测数据**；
- 不落 `.tmp_ref/`：`tests/unit` 必须在没有 `.tmp_ref` 的机器上也能跑，且
  `.tmp_ref` 有并行 agent 在写，共用路径会互相覆盖（AGENTS.md §4 I5）。
- 不改 `eval/report.py`（并行 agent 的产物，只读）。发现疑似缺陷写进简报与 xfail。

一处已知缺口用 xfail 标出而**不去改代码**：`eval/run_eval.py:1096` 会产出
`EVIDENCE_GATE_MISS`，但 `eval/report.py:102-127` 的 `FAILURE_CODE_CLASS` 没登记它，
于是按「九类之外」（传输/服务端故障）聚合，而它其实是「无证据题却给了实质回答」。
详见 `test_evidence_gate_miss_should_be_evidence_class` 的 reason。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"

#: fixture 里写死的被测地址（与真实部署无关，只用于让 base_url 校验走 OK 分支）。
BASE_URL = "http://127.0.0.1:8010"


def _load_eval_module(name: str, module_name: str) -> ModuleType:
    """按路径加载 `eval/<name>.py`，并登记进 `sys.modules`（同 test_eval_harness）。"""
    path = EVAL_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - eval/ 被删才会发生
        raise RuntimeError(f"加载不了 {path}")
    module = importlib.util.module_from_spec(spec)
    # dataclass(slots=True) 生成类时回查 sys.modules[cls.__module__]，不先登记就失败。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report = _load_eval_module("report", "_eval_report_under_test")


# ────────────────────────────────────────────────────────────────────────────
# 造数据：合成 fixture（不是实测）
# ────────────────────────────────────────────────────────────────────────────


def _record(sample_id: str, **overrides: Any) -> dict[str, Any]:
    """一条 `raw.jsonl` 记录的最小形状；字段名对齐 `run_eval.build_raw_record`。"""
    record: dict[str, Any] = {
        "sample_id": sample_id,
        "category": "fixture-手册",
        "expect": "answer",
        "repeat": 1,
        "latency_ms": 10.0,
        "results": [],
        "answer_text": "",
        "citations": [],
        "status": "ANSWERED",
        "invalid_citations": 0,
        "retrieval_strategy": "hybrid",
        "retrieval_strategy_source": "config",
        "prompt_version": "fixture-prompt-v0",
        "prompt_version_source": "config",
        "query_http_status": 200,
        "chat_http_status": 200,
        "chat_ttft_ms": None,
        "chat_total_ms": 100.0,
        "error_code": None,
        "degraded": False,
        "deny": None,
        "gold_sha1_8": [],
        "gold_document_ids": [],
        "expect_facts": [],
        "notes": "synthetic fixture, not a real measurement",
    }
    record.update(overrides)
    return record


def _config(dataset: Path | None, *, strategy: str) -> dict[str, Any]:
    """`summary.json` 的 `config` 块：字段名对齐 `run_eval.validate_config`。"""
    return {
        "experiment_id": "fixture-ab-day03",
        "app_commit": "fixture-not-a-real-commit",
        "prompt_version": "fixture-prompt-v0",
        "eval_dataset": None if dataset is None else str(dataset),
        "embedding_backend": "test/hashing",
        "retrieval_strategy": strategy,
        "query_top_k": 5,
        "chat_top_k": 5,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            assert isinstance(item, dict)
            rows.append(item)
    return rows


def _write_dataset(path: Path, questions: dict[str, str]) -> Path:
    """最小评测集：报告只读 `id` + `query` 来补问题原文。"""
    rows = [
        {"id": sample_id, "query": query, "category": "fixture", "notes": "fixture"}
        for sample_id, query in questions.items()
    ]
    _write_jsonl(path, rows)
    return path


def _make_side(
    root: Path,
    records: Sequence[dict[str, Any]] | None,
    *,
    dataset: Path | None = None,
    strategy: str = "hybrid",
    repeats: int = 1,
    deny_leak: bool = False,
    write_summary: bool = True,
    write_raw: bool = True,
) -> Path:
    """写一个结果目录。`summary.json` 用 runner 的真实 `build_summary` 生成，

    这样「summary 值 == raw 复算值」是构造出来的，不会因为手写漂移把每条用例
    都变成「复算不一致」。
    """
    root.mkdir(parents=True, exist_ok=True)
    if write_summary:
        assert records is not None, "要写 summary 就必须有 records"
        runner = report.runner_module()
        summary = runner.build_summary(
            records,
            cfg=_config(dataset, strategy=strategy),
            repeats=repeats,
            base_url=BASE_URL,
            limit=None,
            app_commit_mismatch=False,
            deny_leak=deny_leak,
        )
        _write_json(root / report.SUMMARY_FILE, summary)
    if write_raw and records is not None:
        _write_jsonl(root / report.RAW_FILE, records)
    return root


# ────────────────────────────────────────────────────────────────────────────
# 场景 A：候选比基线差（三个样本，覆盖 answer / abstain / deny 三类判据）
# ────────────────────────────────────────────────────────────────────────────

_QUESTIONS_A = {
    "q-answer": "年假多少天？",
    "q-abstain": "外星人存在吗？",
    "q-deny": "别的工作区的文档？",
}


def _degrading_records() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """手算基线（两个 delta 都能一眼验算，见用例里的注释）。

    基线：Recall@5 = (1.0 + 0.0) / 2 = 0.5？——不是。deny/abstain 没有 gold，
    不算进 recall 分母，所以基线只有 q-answer 一条：1.0。
    """
    base = [
        _record(
            "q-answer",
            results=[{"content_sha1_8": "hit-1"}],
            gold_sha1_8=["hit-1"],
            gold_document_ids=["doc-a"],
            expect_facts=["年假"],
            answer_text="年假为 5 天",
            citations=[
                {
                    "document_id": "doc-a",
                    "snippet": "年假 5 天",
                    "page_no": 1,
                    "page_end_no": 1,
                }
            ],
            latency_ms=10.0,
            chat_total_ms=100.0,
        ),
        _record(
            "q-abstain",
            expect="abstain",
            status="ABSTAINED",
            answer_text="没有检索到相关内容",
            latency_ms=20.0,
            chat_total_ms=200.0,
        ),
        _record(
            "q-deny",
            expect="deny",
            status="DENIED",
            latency_ms=30.0,
            chat_total_ms=300.0,
            deny={
                "query_http_status": 404,
                "chat_http_status": 404,
                "query_body_leaked": False,
                "chat_body_leaked": False,
                "intercepted": True,
                "returned_200": False,
            },
        ),
    ]
    cand = [
        _record(
            "q-answer",
            results=[],
            gold_sha1_8=["hit-1"],
            gold_document_ids=["doc-a"],
            expect_facts=["年假"],
            answer_text="抱歉，未找到相关内容",
            latency_ms=50.0,
            chat_total_ms=500.0,
        ),
        _record(
            "q-abstain",
            expect="abstain",
            status="ANSWERED",
            answer_text="外星人并不存在",
            latency_ms=60.0,
            chat_total_ms=600.0,
        ),
        _record(
            "q-deny",
            expect="deny",
            status="DENY_NOT_404",
            latency_ms=70.0,
            chat_total_ms=700.0,
            deny={
                "query_http_status": 404,
                "chat_http_status": 500,
                "query_body_leaked": False,
                "chat_body_leaked": False,
                "intercepted": False,
                "returned_200": False,
            },
        ),
    ]
    return base, cand


def _degrading_pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    dataset = _write_dataset(tmp_path / "eval_set_fixture_a.jsonl", _QUESTIONS_A)
    base_records, cand_records = _degrading_records()
    base = _make_side(tmp_path / "base", base_records, dataset=dataset, strategy="hybrid")
    cand = _make_side(tmp_path / "cand", cand_records, dataset=dataset, strategy="vector_only")
    return base, cand, dataset


# ────────────────────────────────────────────────────────────────────────────
# 场景 B：两侧完全同分布（用来验证「没有退化」与「可比」两个反例）
# ────────────────────────────────────────────────────────────────────────────


def _stable_pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    dataset = _write_dataset(tmp_path / "eval_set_fixture_b.jsonl", _QUESTIONS_A)
    base_records, _ = _degrading_records()
    base = _make_side(tmp_path / "base", base_records, dataset=dataset, strategy="hybrid")
    cand = _make_side(tmp_path / "cand", base_records, dataset=dataset, strategy="vector_only")
    return base, cand, dataset


# ────────────────────────────────────────────────────────────────────────────
# 小工具：把报告 markdown 拆成可断言的格子
# ────────────────────────────────────────────────────────────────────────────


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = text.find("\n## ", start + len(heading))
    return text[start:] if nxt == -1 else text[start:nxt]


def _metric_cells(text: str) -> dict[str, list[str]]:
    """§1 七指标表 → {指标名: [baseline, candidate, 差值, 说明]}。"""
    table: dict[str, list[str]] = {}
    for line in _section(text, "## 1 七指标对比").splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0] in {"指标", "---"}:
            continue
        table[cells[0]] = cells[1:]
    return table


def _render_via_cli(
    tmp_path: Path,
    base_dir: Path,
    cand_dir: Path,
    *,
    dataset: Path | None = None,
    name: str = "compare_fixture.md",
) -> str:
    """走 CLI 生成报告并读回文本——断言的是用户真正会看到的那份文件。"""
    out = tmp_path / name
    argv = [str(base_dir), str(cand_dir), "--out", str(out)]
    if dataset is not None:
        argv += ["--eval-dataset", str(dataset)]
    assert report.main(argv) == report.EXIT_OK
    return out.read_text(encoding="utf-8")


# ────────────────────────────────────────────────────────────────────────────
# 1. 缺指标 → 「待填」，不是 0
# ────────────────────────────────────────────────────────────────────────────


def test_missing_metric_cell_is_pending_not_zero(tmp_path: Path) -> None:
    """候选侧一条可核验引用都没有（citations_checked=0）→ 该格必须「待填」。

    红线：0 是实测值，不是「不知道」。把 None 渲染成 0.000 会让「引用质量崩了」
    看起来像「引用质量为 0」——两者在结论里的含义完全不同。
    手算：base 1/1 = 1.000；cand 0/0 无定义（None）；Δ 不可算。
    """
    base_dir, cand_dir, dataset = _degrading_pair(tmp_path)
    cand = report.load_side("candidate", cand_dir)

    metric = report.metric_value(cand, "citation_accuracy")
    assert metric.pending is True
    assert metric.source == report.PENDING
    assert metric.note == "无被核验的引用（citations_checked=0）"
    # 直接钉住单元格渲染：任何把 None 写成 0 的改动都会红。
    assert report._metric_cell(metric, "ratio") == "待填（无被核验的引用（citations_checked=0））"

    text = _render_via_cli(tmp_path, base_dir, cand_dir, dataset=dataset)
    cells = _metric_cells(text)
    assert cells["引用准确率"] == [
        "1.000",
        "待填（无被核验的引用（citations_checked=0））",
        "待填（缺一侧数值）",
        "1 / 0",
    ]
    # 报告头把「待填 1 个」数出来，而不是把它算进「已核对」。
    assert "- 来自 `summary.json` 且与 `raw.jsonl` 复算一致：13 个" in text
    assert "- 待填：1 个" in text
    assert "引用准确率（candidate：无被核验的引用（citations_checked=0））" in text


def test_zero_is_measured_value_not_pending(tmp_path: Path) -> None:
    """真的算出 0.0 时必须写 0.000，不能反过来被当成「缺数据」。

    候选侧 q-answer 一条都没召回（results=[]）→ Precision@5 按契约记 0.0
    （`run_eval.precision_at_5:858-868`），分母是 min(5, 0)=0 的情形被显式落成 0。
    手算：base 1/1 = 1.000，cand 0.0 → Δ = 0.0 − 1.0 = −1.000。
    """
    base_dir, cand_dir, dataset = _degrading_pair(tmp_path)
    cand = report.load_side("candidate", cand_dir)

    metric = report.metric_value(cand, "precision_at_5")
    assert metric.pending is False
    assert metric.value == pytest.approx(0.0)

    text = _render_via_cli(tmp_path, base_dir, cand_dir, dataset=dataset)
    assert _metric_cells(text)["Precision@5"] == ["1.000", "0.000", "-1.000", "1 / 1"]


def test_summary_missing_entirely_marks_all_cells_pending(tmp_path: Path) -> None:
    """目录里什么都没有（不是 0 条样本）→ 7 个格子全是「待填」且说明缺什么。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    base_dir, cand_dir, dataset = _degrading_pair(tmp_path)
    # 用空目录当 candidate：目录存在但不是一次运行。
    _ = cand_dir

    side = report.load_side("candidate", empty)
    for name, _label, _kind in report.METRIC_ROWS:
        metric = report.metric_value(side, name)
        assert metric.pending is True, f"{name} 缺数据时不得有值"
        assert metric.note == "缺 summary.json"

    text = _render_via_cli(tmp_path, base_dir, empty, dataset=dataset)
    for label in ("Recall@5", "Precision@5", "引用准确率", "无证据拒答准确率", "权限拦截率"):
        assert _metric_cells(text)[label][1].startswith("待填")
    assert "- 待填：7 个" in text


def test_metric_value_falls_back_to_raw_recompute(tmp_path: Path) -> None:
    """summary 缺、raw 在 → 用复算值并标来源；不是「待填」也不是 0。"""
    records = [
        _record(
            "r-1",
            results=[{"content_sha1_8": "hit-1"}],
            gold_sha1_8=["hit-1"],
            gold_document_ids=["doc-a"],
            expect_facts=["年假"],
            answer_text="年假 5 天",
        )
    ]
    side = report.load_side(
        "baseline", _make_side(tmp_path / "raw_only", records, write_summary=False)
    )
    metric = report.metric_value(side, "recall_at_5")
    assert metric.value == pytest.approx(1.0)
    assert metric.source == "raw.jsonl 复算"
    assert metric.note == "summary.json 无该值，按 raw 复算"


def test_missing_reason_prefers_denominator_then_raw_then_file() -> None:
    """「为什么是待填」的三种原因各走一条分支，顺序不能乱。"""
    # (a) 分母为 0：先说是「本轮没有这类样本」，而不是说文件坏了。
    abstain_only = [
        _record("a-1", expect="abstain", status="ABSTAINED", answer_text="没有检索到相关内容")
    ]
    side = report.RunSide(
        label="candidate",
        root=Path("fixture-not-a-real-path"),
        summary={
            "metrics": {"deny_interception": {"value": None}},
            "counts": {"deny_samples": 0},
        },
        records=abstain_only,
    )
    assert report.missing_reason(side, "deny_interception") == "本轮无 deny 样本"

    # (b) raw 在、但没有任何一行有 delay 字段。
    no_latency = report.RunSide(
        label="candidate",
        root=Path("fixture-not-a-real-path"),
        summary={"metrics": {"retrieval_p95_ms": {"value": None}}},
        records=[_record("n-1", latency_ms=None, chat_total_ms=None)],
    )
    assert report.missing_reason(no_latency, "retrieval_p95_ms") == "无 latency_ms 记录"

    # (c) 连 summary.json 都没有。
    no_files = report.RunSide(
        label="candidate", root=Path("fixture-not-a-real-path"), summary_error="缺 summary.json"
    )
    assert report.missing_reason(no_files, "recall_at_5") == "缺 summary.json"


# ────────────────────────────────────────────────────────────────────────────
# 2. 两侧都有值 → 差值 = cand − base（含负差）
# ────────────────────────────────────────────────────────────────────────────


def test_delta_cell_hand_computed() -> None:
    """差值 = 候选 − 基线，手算钉住（含负差、ms 与 ratio 两种格式）。"""
    ratio_up = report.delta_cell(
        report.MetricValue(0.5, "fixture"), report.MetricValue(0.8, "fixture"), "ratio"
    )
    assert ratio_up == "+0.300"  # 0.8 − 0.5

    ratio_down = report.delta_cell(
        report.MetricValue(1.0, "fixture"), report.MetricValue(0.8, "fixture"), "ratio"
    )
    assert ratio_down == "-0.200"  # 0.8 − 1.0

    ms_down = report.delta_cell(
        report.MetricValue(120.0, "fixture"), report.MetricValue(95.5, "fixture"), "ms"
    )
    assert ms_down == "-24.5 ms"  # 95.5 − 120.0

    ms_up = report.delta_cell(
        report.MetricValue(30.0, "fixture"), report.MetricValue(70.0, "fixture"), "ms"
    )
    assert ms_up == "+40.0 ms"  # 70.0 − 30.0

    # 任一侧缺值：绝不退化成 0（0 会被读成「没变化」）。
    pending = report.delta_cell(
        report.MetricValue(None, "待填", "缺"), report.MetricValue(0.8, "fixture"), "ratio"
    )
    assert pending == "待填（缺一侧数值）"


def test_metric_table_hand_computed_both_sides(tmp_path: Path) -> None:
    """整张 §1 表逐格手算，覆盖四个方向（上升/下降/持平/ms）。

    基线：Recall@5 = 1.000（唯一有 gold 的 answer 样本命中）
    候选：Recall@5 = 0.000（同一样本 results 为空）→ Δ = −1.000
    权限拦截率 1.000 → 0.000（candidate 的 chat 端点返回 500）
    检索 P95：n=3 → 第 ceil(2.85)=3 慢，30.0 ms → 70.0 ms → Δ = +40.0 ms
    问答 P95：300.0 ms → 700.0 ms → Δ = +400.0 ms
    """
    base_dir, cand_dir, dataset = _degrading_pair(tmp_path)
    cells = _metric_cells(_render_via_cli(tmp_path, base_dir, cand_dir, dataset=dataset))

    assert cells["Recall@5"] == ["1.000", "0.000", "-1.000", "1 / 1"]
    assert cells["Precision@5"] == ["1.000", "0.000", "-1.000", "1 / 1"]
    assert cells["无证据拒答准确率"] == ["1.000", "0.000", "-1.000", "1 / 1"]
    assert cells["权限拦截率"] == ["1.000", "0.000", "-1.000", "1 / 1"]
    assert cells["检索 P95"] == ["30.0 ms", "70.0 ms", "+40.0 ms", "3 / 3"]
    assert cells["问答 P95"] == ["300.0 ms", "700.0 ms", "+400.0 ms", "3 / 3"]


# ────────────────────────────────────────────────────────────────────────────
# 3. 候选变差 → 必须进退化清单
# ────────────────────────────────────────────────────────────────────────────


def test_candidate_regressions_are_listed(tmp_path: Path) -> None:
    """逐样本判据下降即入清单；次序与数值都要对得上手算结果。

    手算（两侧都只有 repeats=1 的一行，取均值 = 该行本身）：
    - q-abstain：abstained 1.0 → 0.0（拒答成功）
    - q-answer ：recall 1.0 → 0.0、precision 1.0 → 0.0、fact_hit 1.0 → 0.0
                 （citation_pass 候选侧 checked=0 → None，不参与比较）
    - q-deny   ：deny_intercepted 1.0 → 0.0（权限拦截）
    """
    base_dir, cand_dir, _dataset = _degrading_pair(tmp_path)
    base = report.load_side("baseline", base_dir)
    cand = report.load_side("candidate", cand_dir)

    rows, notes, comparable = report.find_degradations(base, cand)
    assert comparable == 3
    assert notes == []
    assert [(row.sample_id, row.signal) for row in rows] == [
        ("q-abstain", "拒答成功"),
        ("q-answer", "Recall@5"),
        ("q-answer", "Precision@5"),
        ("q-answer", "事实命中"),
        ("q-deny", "权限拦截"),
    ]
    assert [row.base for row in rows] == pytest.approx([1.0, 1.0, 1.0, 1.0, 1.0])
    assert [row.cand for row in rows] == pytest.approx([0.0, 0.0, 0.0, 0.0, 0.0])


def test_report_section_three_lists_degradations(tmp_path: Path) -> None:
    """退化必须出现在报告正文（§3），不能只活在返回值里。"""
    base_dir, cand_dir, dataset = _degrading_pair(tmp_path)
    text = _render_via_cli(tmp_path, base_dir, cand_dir, dataset=dataset)
    section = _section(text, "## 3 退化清单")
    for sample_id in ("q-abstain", "q-answer", "q-deny"):
        assert f"| {sample_id} |" in section
    for signal in ("拒答成功", "Recall@5", "Precision@5", "事实命中", "权限拦截"):
        assert f"| {signal} |" in section
    assert "共 5 条退化，涉及 3 条样本" in section


def test_identical_runs_have_no_degradations_and_no_overclaim(tmp_path: Path) -> None:
    """两侧没差异时：清单为空，且报告不得暗示「候选更好」。"""
    base_dir, cand_dir, dataset = _stable_pair(tmp_path)
    base = report.load_side("baseline", base_dir)
    cand = report.load_side("candidate", cand_dir)

    rows, notes, comparable = report.find_degradations(base, cand)
    assert rows == []
    assert notes == []
    assert comparable == 3

    text = _render_via_cli(tmp_path, base_dir, cand_dir, dataset=dataset)
    section = _section(text, "## 3 退化清单")
    assert "所有判据均未下降" in section
    assert "**这不等于「候选更好」**" in section


def test_find_degradations_does_not_score_missing_sample_as_zero(tmp_path: Path) -> None:
    """只在一侧出现的样本：不比较、不按 0 算，只写进不可比说明。"""
    base_dir, cand_dir, _dataset = _degrading_pair(tmp_path)
    cand_records = [
        row
        for row in json.loads(json.dumps(_read_jsonl(cand_dir / report.RAW_FILE)))
        if row["sample_id"] != "q-deny"
    ]
    _write_jsonl(cand_dir / report.RAW_FILE, cand_records)

    base = report.load_side("baseline", base_dir)
    cand = report.load_side("candidate", cand_dir)
    rows, notes, comparable = report.find_degradations(base, cand)
    assert comparable == 2
    assert notes == ["1 条样本只在 baseline：q-deny"]
    assert all(row.sample_id != "q-deny" for row in rows)


# ────────────────────────────────────────────────────────────────────────────
# 4. --check 必须拒绝不可对比的两份产物
# ────────────────────────────────────────────────────────────────────────────


def test_check_rejects_question_set_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """候选少跑了一条样本 → question_set FAIL，退出码 1（不是 0、不是 2）。"""
    base_dir, cand_dir, _dataset = _degrading_pair(tmp_path)
    kept = [row for row in _read_jsonl(cand_dir / report.RAW_FILE) if row["sample_id"] != "q-deny"]
    _write_jsonl(cand_dir / report.RAW_FILE, kept)

    code = report.main(["--check", str(base_dir), str(cand_dir)])
    out = capsys.readouterr().out
    assert code == report.EXIT_CHECK_FAILED
    assert "[FAIL] question_set" in out
    assert "baseline 独有 1 条 [q-deny]" in out
    assert "结论：不可对比" in out


def test_check_rejects_different_eval_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """两份结果声明的 eval_dataset 内容不同 → dataset.hash FAIL。

    这里只改 candidate 的 `config.eval_dataset`，raw.jsonl 故意不动：报告的职责是
    「发现两份产物不是同一份评测集」，而不是替人挑一个看起来顺眼的哈希。
    """
    base_dir, cand_dir, _dataset = _degrading_pair(tmp_path)
    other = _write_dataset(tmp_path / "eval_set_fixture_other.jsonl", {"q-answer": "另一个问题？"})
    summary = _read_json(cand_dir / report.SUMMARY_FILE)
    summary["config"]["eval_dataset"] = str(other)
    _write_json(cand_dir / report.SUMMARY_FILE, summary)

    code = report.main(["--check", str(base_dir), str(cand_dir)])
    out = capsys.readouterr().out
    assert code == report.EXIT_CHECK_FAILED
    assert "[FAIL] dataset.hash" in out
    assert "[WARN] eval_dataset" in out
    assert "sha256 不一致" in out


def test_check_accepts_comparable_pair(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """同评测集、同题目集、唯一主因素不同 → 可对比（退出码 0）。

    `run_checks` 现在返回 19 项；`run_checks` 的 docstring 写「16 项」是陈旧注释
    （`eval/report.py:794`），已在简报里作为疑似文档缺陷登记，这里只钉行为。
    """
    base_dir, cand_dir, _dataset = _stable_pair(tmp_path)
    base = report.load_side("baseline", base_dir)
    cand = report.load_side("candidate", cand_dir)
    dataset = report.load_dataset(_dataset)
    items = report.run_checks(base, cand, dataset, dataset)

    assert [item.key for item in items] == [
        "dir.baseline",
        "dir.candidate",
        "file.summary.baseline",
        "file.raw.baseline",
        "file.report.baseline",
        "file.summary.candidate",
        "file.raw.candidate",
        "file.report.candidate",
        "summary.keys",
        "eval_dataset",
        "dataset.hash",
        "question_set",
        "app_commit",
        "experiment_id",
        "repeats",
        "base_url",
        "config.diff",
        "metrics.recompute",
        "deny_leak",
    ]
    assert len(items) == 19
    assert [item.status for item in items if item.status == report.CHECK_FAIL] == []

    code = report.main(["--check", str(base_dir), str(cand_dir)])
    out = capsys.readouterr().out
    assert code == report.EXIT_OK
    assert "[FAIL]" not in out
    assert "结论：可对比" in out
    assert "唯一不同的主因素：retrieval_strategy" in out
    assert "base='hybrid' → cand='vector_only'" in out


def test_missing_directory_exits_2_without_writing_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """路径写错是 exit 2（处置人不同），且不得生成半份报告。"""
    out = tmp_path / "should_not_exist.md"
    code = report.main([str(tmp_path / "nope"), str(tmp_path / "nope2"), "--out", str(out)])
    captured = capsys.readouterr()
    assert code == report.EXIT_INPUT
    assert "[input]" in captured.err
    assert out.exists() is False


# ────────────────────────────────────────────────────────────────────────────
# 5. 失败样本按九类聚合；未知 code 落进兜底桶而不是被吞掉
# ────────────────────────────────────────────────────────────────────────────


def _nine_class_records() -> list[dict[str, Any]]:
    """五条记录各命中一个已知二级 code（手算见用例 docstring）。"""
    return [
        # NO_HIT → 召回不足：有 gold、一条都没召回。
        _record("d-no-hit", gold_sha1_8=["g1"], results=[]),
        # GOLD_MISS + INVALID_CITATION → 召回不足 + 引用错误。
        _record(
            "d-gold-miss",
            gold_sha1_8=["g2"],
            results=[{"content_sha1_8": "not-gold"}],
            invalid_citations=2,
        ),
        # FORBIDDEN_LEAK → 权限错误：deny 样本返回 200。
        _record(
            "d-deny-leak",
            expect="deny",
            status="DENY_LEAKED",
            deny={
                "query_http_status": 200,
                "chat_http_status": 200,
                "query_body_leaked": True,
                "chat_body_leaked": True,
                "intercepted": False,
                "returned_200": True,
            },
        ),
        # OVER_RETRIEVAL → 证据不足：无证据题却给了带引用的回答。
        _record(
            "d-over-retrieval",
            expect="abstain",
            status="ANSWERED",
            answer_text="答案是 42",
            citations=[{"document_id": "doc-x", "snippet": "42", "page_no": 1, "page_end_no": 1}],
        ),
        # REQUEST_FAILED → 九类之外：传输/服务端故障，不算模型质量缺陷。
        _record("d-request-failed", error_code="INTERNAL_ERROR"),
    ]


def test_failure_ledger_aggregates_nine_classes(tmp_path: Path) -> None:
    """按一级类聚合、按样本去重；缺席的类保留 0，raw 不可用的那侧写「待填」。

    手算：召回不足 2 条（d-no-hit、d-gold-miss）、引用错误 1、证据不足 1、
    权限错误 1、九类之外 1；其余四类 0。
    """
    dataset = _write_dataset(
        tmp_path / "eval_set_fixture_d.jsonl",
        {
            "d-no-hit": "问题 1",
            "d-gold-miss": "问题 2",
            "d-deny-leak": "问题 3",
            "d-over-retrieval": "问题 4",
            "d-request-failed": "问题 5",
        },
    )
    records = _nine_class_records()
    base_dir = _make_side(tmp_path / "base", records, dataset=dataset, deny_leak=True)
    # candidate 只有 summary.json：用来钉「raw 不可用 → 待填，不是 0」。
    cand_dir = _make_side(
        tmp_path / "cand", records, dataset=dataset, deny_leak=True, write_raw=False
    )
    base = report.load_side("baseline", base_dir)
    cand = report.load_side("candidate", cand_dir)
    questions = report.load_dataset(dataset).questions

    rows = report.build_ledger(base, questions)
    classes = {row.sample_id: row.classes for row in rows}
    assert classes["d-no-hit"] == "召回不足（NO_HIT）"
    assert classes["d-gold-miss"] == "召回不足（GOLD_MISS）；引用错误（INVALID_CITATION）"
    assert classes["d-deny-leak"] == "权限错误（FORBIDDEN_LEAK）"
    assert classes["d-over-retrieval"] == "证据不足（OVER_RETRIEVAL）"
    assert classes["d-request-failed"] == "九类之外（REQUEST_FAILED）"
    assert classes["d-no-hit"] != ""  # 有个问题原文就写进去，没 id 也不能空着
    assert next(row for row in rows if row.sample_id == "d-no-hit").question == "问题 1"

    table = report.class_counts(rows, [], base=base, cand=cand)
    assert [item.name for item in table] == [
        *report.FAILURE_CLASS_ORDER,
        report.OUTSIDE_NINE,
    ]
    assert [(item.name, item.base, item.cand) for item in table] == [
        ("文档解析失败", 0, None),
        ("索引版本错误", 0, None),
        ("召回不足", 2, None),
        ("排序失败", 0, None),
        ("路由失败", 0, None),
        ("证据不足", 1, None),
        ("生成错误", 0, None),
        ("引用错误", 1, None),
        ("权限错误", 1, None),
        ("九类之外", 1, None),
    ]
    assert next(item for item in table if item.name == "召回不足").codes == ["GOLD_MISS", "NO_HIT"]

    # 不变量：台账里的每个 code 都必须能在聚合表里找到，不允许被静默丢弃。
    ledger_codes = {code for row in rows for code in row.codes}
    table_codes = {code for item in table for code in item.codes}
    assert table_codes == ledger_codes

    data = report.build_report_data(
        base,
        cand,
        report.load_dataset(dataset),
        report.load_dataset(dataset),
        [],
        generated_at="fixture-generated-at",
    )
    text = report.render_report(data)
    assert "| 召回不足 | 2 | 待填（缺 raw.jsonl） | GOLD_MISS、NO_HIT |" in text
    assert "待填：candidate 的 `raw.jsonl` 不可用，无法逐样本比对。" in text


def test_ledger_question_missing_from_dataset_stays_pending(tmp_path: Path) -> None:
    """评测集里没有这个 id → 问题列写「待填」，不猜、不留空。"""
    dataset = _write_dataset(tmp_path / "eval_set_fixture_e.jsonl", {"d-no-hit": "问题 1"})
    base_dir = _make_side(tmp_path / "base", _nine_class_records(), dataset=dataset)
    base = report.load_side("baseline", base_dir)
    rows = report.build_ledger(base, report.load_dataset(dataset).questions)
    missing = next(row for row in rows if row.sample_id == "d-gold-miss")
    assert missing.question == "待填（评测集无此 id）"


def test_unknown_code_lands_in_catch_all_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未登记的二级 code：显式落到「九类之外」，并且带上 code 原文。

    `run_eval.collect_failures` 只会产出已知 code，所以这里 monkeypatch 一个
    未知 code：钉的是「映射层是全函数」——将来 runner 加了新 code 而忘记登记，
    结果必须是「可见的兜底」，不是「消失」。
    """
    records = [_record("u-1")]
    side = report.load_side("baseline", _make_side(tmp_path / "base", records))
    runner = report.runner_module()

    def fake_collect_failures(_records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "sample_id": "u-1",
                "category": "fixture-手册",
                "codes": ["NOT_A_REGISTERED_CODE", "NO_HIT"],
                "detail": "fixture",
            }
        ]

    monkeypatch.setattr(runner, "collect_failures", fake_collect_failures)

    rows = report.build_ledger(side, {})
    assert "NOT_A_REGISTERED_CODE" not in report.FAILURE_CODE_CLASS
    assert len(rows) == 1
    assert rows[0].codes == ["NOT_A_REGISTERED_CODE", "NO_HIT"]
    assert rows[0].classes == "召回不足（NO_HIT）；九类之外（NOT_A_REGISTERED_CODE）"
    assert "未登记" in rows[0].hints

    table = report.class_counts(rows, [], base=side, cand=side)
    bucket = next(item for item in table if item.name == report.OUTSIDE_NINE)
    assert bucket.base == 1
    assert "NOT_A_REGISTERED_CODE" in bucket.codes
    # 「九类之外」列在九类之后，不挤进任何一类。
    assert table[-1].name == report.OUTSIDE_NINE


@pytest.mark.xfail(
    reason=(
        "疑似缺陷：eval/run_eval.py:1096 会产出 EVIDENCE_GATE_MISS（无证据题却给了"
        "实质回答），但 eval/report.py:102-127 的 FAILURE_CODE_CLASS 没登记它，"
        "于是 eval/report.py:695-708 把它归到「九类之外」（传输/服务端故障），"
        "而它属于「证据不足」。不影响「不被吞掉」（兜底桶会列出来），"
        "只影响九类聚合的语义。本用例钉的是应有口径，登记表补上后会 XPASS。"
    )
)
def test_evidence_gate_miss_should_be_evidence_class() -> None:
    label, _hints = report.classify_codes(["EVIDENCE_GATE_MISS"])
    assert label == "证据不足（EVIDENCE_GATE_MISS）"


# ────────────────────────────────────────────────────────────────────────────
# 6. 复算不一致：保留 summary 值但必须点名（不取平均、不挑顺眼的）
# ────────────────────────────────────────────────────────────────────────────


def test_summary_raw_mismatch_kept_and_flagged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """summary 说 0.75、raw 复算说 1.0 → 保留 summary 值并标注不一致，--check FAIL。

    手算：raw 里唯一有 gold 的样本 recall = 1.0，所以复算 = 1.000000。
    """
    base_dir, cand_dir, dataset = _degrading_pair(tmp_path)
    summary = _read_json(base_dir / report.SUMMARY_FILE)
    summary["metrics"]["recall_at_5"]["value"] = 0.75
    _write_json(base_dir / report.SUMMARY_FILE, summary)

    base = report.load_side("baseline", base_dir)
    metric = report.metric_value(base, "recall_at_5")
    assert metric.value == pytest.approx(0.75)
    assert metric.source == report.SUMMARY_FILE
    assert "复算不一致" in metric.note
    assert "复算=1.000000" in metric.note

    code = report.main(["--check", str(base_dir), str(cand_dir)])
    out = capsys.readouterr().out
    assert code == report.EXIT_CHECK_FAILED
    assert "[FAIL] metrics.recompute" in out

    text = _render_via_cli(tmp_path, base_dir, cand_dir, dataset=dataset)
    assert "**复算不一致（不可直接引用）**" in text
    assert "复算不一致（1 项）" in text


# ────────────────────────────────────────────────────────────────────────────
# 7. 端到端：CLI 写出的报告与 stdout 计数
# ────────────────────────────────────────────────────────────────────────────


def test_main_writes_report_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """一次完整生成：14 个格子（13 有据 + 1 待填）、台账 3 条、退化 5 条。"""
    base_dir, cand_dir, dataset = _degrading_pair(tmp_path)
    text = _render_via_cli(tmp_path, base_dir, cand_dir, dataset=dataset)
    out = capsys.readouterr().out
    assert "指标格子 14 个：summary.json 13 / raw 复算 0 / 待填 1" in out
    assert "失败样本台账 3 条；退化 5 条；复算不一致 0 项" in out
    assert "## 3 退化清单" in text
    assert "共 5 条退化，涉及 3 条样本" in text
    assert "待填（1 个指标格子）" in text
