"""`eval/run_eval.py` 的纯函数单测：指标口径、配置守卫、引用核验。

为什么这些用例值得占 tests/unit 的位置：这几块都**不连网、不连库、不跑真模型**，
却决定了报告里每个数字的含义。`run_eval.py` 的模块 docstring 自己写了理由——
分母一旦被顺手改得好看一点，两轮实验的数就不可比。所以先把口径钉在用例里，
再让人去跑真评测。

为什么用 importlib 按路径加载：`eval/` 不是包（没有 `__init__.py`），`run_eval`
也不是安装模块。加载方式照 `eval/make_configs.py:120-132`：**必须先登记
`sys.modules`**，否则模块里的 `@dataclass(slots=True)` 回查
`sys.modules[cls.__module__]` 会 AttributeError。

一处已知缺口，用 xfail 标出而**不去改代码**（本任务只许写 tests/）：
`precision_at_5` 的分子统计全部 results、分母却封顶 5，`len(results) > 5` 时
结果可以 >1.0。详见 `test_precision_at_5_never_exceeds_one` 的 reason。
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"


def load_eval_module(name: str, module_name: str) -> ModuleType:
    """按路径加载 `eval/<name>.py`，并登记进 `sys.modules`。"""
    path = EVAL_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - eval/ 被删才会发生
        raise RuntimeError(f"加载不了 {path}")
    module = importlib.util.module_from_spec(spec)
    # dataclass(slots=True) 生成类时回查 sys.modules[cls.__module__]，不先登记就失败。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run_eval = load_eval_module("run_eval", "_eval_run_eval_harness")


# ────────────────────────────────────────────────────────────────────────────
# 造数据的小工具
# ────────────────────────────────────────────────────────────────────────────


def _cfg() -> dict[str, Any]:
    """`dry_run_config()` 是一份能通过 validate_config 的最小配置，深拷后改字段。"""
    return copy.deepcopy(run_eval.dry_run_config())


def _write_yaml(path: Path, cfg: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _results(*sha1s: str) -> list[dict[str, Any]]:
    return [{"content_sha1_8": value} for value in sha1s]


def _answer_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "sample_id": "a-01",
        "category": "fact",
        "expect": "answer",
        "repeat": 1,
        "gold_sha1_8": ["aaaa0001"],
        "results": [],
        "latency_ms": 10.0,
        "expect_facts": [],
        "answer_text": "答",
        "invalid_citations": 0,
    }
    record.update(overrides)
    return record


def _citation(**overrides: Any) -> dict[str, Any]:
    citation: dict[str, Any] = {
        "document_id": "doc-a",
        "snippet": "上限 500 元",
        "page_no": None,
        "page_end_no": None,
    }
    citation.update(overrides)
    return citation


def _answer_with_citations(
    citations: list[Any],
    *,
    gold_docs: tuple[str, ...] = ("doc-a",),
    facts: tuple[str, ...] = ("上限 500 元",),
) -> dict[str, Any]:
    return {
        "sample_id": "a-01",
        "expect": "answer",
        "gold_document_ids": list(gold_docs),
        "expect_facts": list(facts),
        "citations": citations,
    }


# ────────────────────────────────────────────────────────────────────────────
# 配置校验：占位符 / 区间 / 一次性列全
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "<git rev-parse HEAD>",
        "<用户填>",
        "TODO",
        "tbd",
        "CHANGEME",
        "change-me",
        "fill_me",
        "your-commit",
        "待实测的数字",
        "用户填写",
    ],
)
def test_is_placeholder_rejects_unfilled_values(value: object) -> None:
    assert run_eval.is_placeholder(value) is True


@pytest.mark.parametrize(
    "value",
    ["baseline", "pyknora-rag-v1", "http://127.0.0.1:8010/v1", 5, 0, 60.0, False, [], {}],
)
def test_is_placeholder_accepts_real_values(value: object) -> None:
    assert run_eval.is_placeholder(value) is False


def test_is_placeholder_is_case_insensitive() -> None:
    # 实现里先 lower() 再比 marker，所以人手写 ToDo 也拦得住。
    assert run_eval.is_placeholder("ToDo") is True
    assert run_eval.is_placeholder("Rev-Parse") is True


@pytest.mark.parametrize(
    ("value", "low", "high", "expected"),
    [
        (1, 1, 50, True),
        (50, 1, 50, True),
        (0, 1, 50, False),
        (51, 1, 50, False),
        (5.0, 1, 50, False),
        ("5", 1, 50, False),
        (True, 1, 50, False),
        (False, 0, 1, False),
        (None, 0, 10, False),
    ],
)
def test_int_in_range(value: object, low: int, high: int, expected: bool) -> None:
    assert run_eval.int_in_range(value, low, high) is expected


def test_dry_run_config_passes_validation() -> None:
    assert run_eval.validate_config(_cfg()) == []


def test_validate_config_lists_every_problem_not_just_the_first() -> None:
    cfg = _cfg()
    del cfg["app_commit"]
    cfg["chat_top_k"] = 99
    problems = run_eval.validate_config(cfg)
    assert len(problems) == 2
    assert "缺少必需字段：app_commit" in problems
    assert any("chat_top_k 必须是 1..20 的整数" in item for item in problems)


@pytest.mark.parametrize(
    ("field", "value", "needle"),
    [
        ("query_top_k", 51, "query_top_k 必须是 1..50 的整数"),
        ("query_top_k", 0, "query_top_k 必须是 1..50 的整数"),
        ("chat_top_k", 21, "chat_top_k 必须是 1..20 的整数"),
        ("chat_top_k", 0, "chat_top_k 必须是 1..20 的整数"),
        ("embedding_dim", 0, "embedding_dim 必须是正整数"),
        ("embedding_dim", (1 << 20) + 1, "embedding_dim 必须是正整数"),
        ("eval_dataset", 123, "eval_dataset 必须是路径字符串"),
        ("app_commit", "<git rev-parse HEAD>", "必需字段为空或仍是占位符：app_commit"),
    ],
)
def test_validate_config_rejects(field: str, value: object, needle: str) -> None:
    cfg = _cfg()
    cfg[field] = value
    problems = run_eval.validate_config(cfg)
    assert any(needle in item for item in problems), problems


def test_validate_config_accepts_contract_bounds() -> None:
    cfg = _cfg()
    cfg["query_top_k"] = 50
    cfg["chat_top_k"] = 20
    cfg["embedding_dim"] = 1 << 20
    assert run_eval.validate_config(cfg) == []


def test_validate_config_rejects_bool_top_k() -> None:
    # bool 是 int 的子类，不显式排除的话 query_top_k=True 会被当成 top_k=1。
    cfg = _cfg()
    cfg["query_top_k"] = True
    problems = run_eval.validate_config(cfg)
    assert any("实际 True" in item for item in problems), problems


def test_validate_config_rejects_non_mapping_snapshot() -> None:
    cfg = _cfg()
    cfg["snapshot"] = "oops"
    problems = run_eval.validate_config(cfg)
    assert problems == ["snapshot 必须是映射，实际是 str"]


def test_validate_config_rejects_documents_not_a_list() -> None:
    cfg = _cfg()
    cfg["snapshot"]["documents"] = {}
    problems = run_eval.validate_config(cfg)
    assert problems == ["snapshot.documents 必须是列表（没有文档也要写 []）"]


def test_validate_config_rejects_document_without_id() -> None:
    cfg = _cfg()
    cfg["snapshot"]["documents"] = [{}]
    problems = run_eval.validate_config(cfg)
    assert problems == ["snapshot.documents[0].document_id 缺失或是占位符"]


def test_validate_config_requires_snapshot_member_fields() -> None:
    cfg = _cfg()
    cfg["snapshot"] = {"documents": []}
    problems = run_eval.validate_config(cfg)
    assert "缺少必需字段：snapshot.workspace_id" in problems
    assert "缺少必需字段：snapshot.kb_id" in problems


def test_validate_config_passes_snapshot_none_through() -> None:
    """现状：snapshot 为 null 不报错（循环里对 snapshot 本身跳过了占位判定）。

    这不是我认可的口径，而是钉住现状并在简报里作为缺口上报：真正跑到
    `cfg["snapshot"]["kb_id"]` 时才会炸，且不是 exit 2 的配置错误。
    """
    cfg = _cfg()
    cfg["snapshot"] = None
    assert run_eval.validate_config(cfg) == []


# ────────────────────────────────────────────────────────────────────────────
# flatten / diff / compare-configs：公平性守卫
# ────────────────────────────────────────────────────────────────────────────


def test_flatten_config_nests_dicts_and_serialises_lists() -> None:
    flat = run_eval.flatten_config({"a": {"b": 1}, "l": [{"x": 1}], "s": "t", "n": None})
    assert flat == {"a.b": 1, "l": '[{"x": 1}]', "s": "t", "n": None}


def test_build_diff_is_sorted_and_marks_the_missing_side() -> None:
    diff = run_eval.build_diff({"b": 2, "a": 1}, {"a": 1, "c": 3})
    assert diff == [("b", 2, "<缺失>"), ("c", "<缺失>", 3)]


def test_build_diff_treats_a_list_as_one_whole_value() -> None:
    left = {"snapshot": {"documents": [{"document_id": "d1"}]}}
    right = {"snapshot": {"documents": [{"document_id": "d2"}]}}
    diff = run_eval.build_diff(left, right)
    assert len(diff) == 1
    assert diff[0][0] == "snapshot.documents"
    assert isinstance(diff[0][1], str)
    assert '"d1"' in diff[0][1] and '"d2"' in diff[0][2]


def test_compare_configs_allows_one_primary_factor(tmp_path: Path) -> None:
    left = _cfg()
    right = _cfg()
    right["retrieval_strategy"] = "candidate"
    left_path = _write_yaml(tmp_path / "a.yaml", left)
    right_path = _write_yaml(tmp_path / "b.yaml", right)

    ok, lines = run_eval.compare_configs(left_path, right_path)

    assert ok is True
    assert lines == ["唯一不同的主因素：retrieval_strategy（A='baseline' → B='candidate'）"]


def test_compare_configs_rejects_two_primary_factors(tmp_path: Path) -> None:
    left = _cfg()
    right = _cfg()
    left["rrf_k"] = 40
    right["rrf_k"] = 60
    right["retrieval_strategy"] = "candidate"
    left_path = _write_yaml(tmp_path / "a.yaml", left)
    right_path = _write_yaml(tmp_path / "b.yaml", right)

    ok, lines = run_eval.compare_configs(left_path, right_path)

    assert ok is False
    assert lines[0] == "差异涉及 2 个主因素，无法归因（只允许恰好 1 个）："
    assert any("rrf_k" in line for line in lines[1:])
    assert any("retrieval_strategy" in line for line in lines[1:])


def test_compare_configs_rejects_a_non_primary_difference(tmp_path: Path) -> None:
    left = _cfg()
    right = _cfg()
    right["chat_model"] = "another-model"
    left_path = _write_yaml(tmp_path / "a.yaml", left)
    right_path = _write_yaml(tmp_path / "b.yaml", right)

    ok, lines = run_eval.compare_configs(left_path, right_path)

    assert ok is False
    assert lines[0] == "以下字段不是允许变化的主因素，对照实验里必须逐字相同："
    assert any("chat_model" in line for line in lines[1:])


def test_compare_configs_rejects_identical_files(tmp_path: Path) -> None:
    left_path = _write_yaml(tmp_path / "a.yaml", _cfg())
    right_path = _write_yaml(tmp_path / "b.yaml", _cfg())

    ok, lines = run_eval.compare_configs(left_path, right_path)

    assert ok is False
    assert lines == ["两份配置完全相同：对照实验至少要有 1 个主因素不同，否则跑两遍是复读。"]


def test_compare_configs_reports_the_invalid_side_first(tmp_path: Path) -> None:
    left_path = _write_yaml(tmp_path / "a.yaml", _cfg())
    bad = _cfg()
    bad["app_commit"] = "TODO"
    right_path = _write_yaml(tmp_path / "b.yaml", bad)

    ok, lines = run_eval.compare_configs(left_path, right_path)

    assert ok is False
    assert lines[0].startswith("B（")
    assert lines[0].endswith("配置不合格：")
    assert any("app_commit" in line for line in lines[1:])


def test_compare_configs_counts_strategy_and_derived_candidate_k_as_two(
    tmp_path: Path,
) -> None:
    """现状：strategy + 因它变化的 candidate_k 被判成 2 个主因素 → 拒绝。

    计划 §5.3（docs/plans/PyKnora_Day03_可信RAG闭环实施计划.md:403）把这当作
    「只允许一处不同」的**一处**。当前 make_configs 不写 candidate_k，所以没触发；
    一旦手写配置带上它，合法对照就会被 exit 2 挡掉（简报里报口径冲突）。
    """
    left = _cfg()
    right = _cfg()
    left["candidate_k"] = 10
    right["candidate_k"] = 40
    right["retrieval_strategy"] = "candidate"
    left_path = _write_yaml(tmp_path / "a.yaml", left)
    right_path = _write_yaml(tmp_path / "b.yaml", right)

    ok, lines = run_eval.compare_configs(left_path, right_path)

    assert ok is False
    assert lines[0] == "差异涉及 2 个主因素，无法归因（只允许恰好 1 个）："


def test_load_yaml_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(run_eval.ConfigError) as excinfo:
        run_eval.load_yaml(path)
    assert "顶层必须是映射" in str(excinfo.value)


def test_load_yaml_rejects_broken_yaml(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("a: [1,\n", encoding="utf-8")
    with pytest.raises(run_eval.ConfigError) as excinfo:
        run_eval.load_yaml(path)
    assert "不是合法 yaml" in str(excinfo.value)


def test_load_yaml_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(run_eval.ConfigError) as excinfo:
        run_eval.load_yaml(tmp_path / "nope.yaml")
    assert "配置文件不存在" in str(excinfo.value)


def test_load_yaml_keeps_angle_bracket_placeholder_as_text(tmp_path: Path) -> None:
    # YAML 里 `<git rev-parse HEAD>` 是普通标量，不会被当成指示符；这条钉住
    # is_placeholder 拿到的确实是那个尖括号串，而不是被 YAML 解释成别的东西。
    cfg = _cfg()
    cfg["app_commit"] = "<git rev-parse HEAD>"
    loaded = run_eval.load_yaml(_write_yaml(tmp_path / "cfg.yaml", cfg))
    assert loaded["app_commit"] == "<git rev-parse HEAD>"
    assert run_eval.is_placeholder(loaded["app_commit"]) is True


# ────────────────────────────────────────────────────────────────────────────
# Recall@5 / Precision@5
# ────────────────────────────────────────────────────────────────────────────


def test_recall_at_5_without_gold_is_none_not_zero() -> None:
    # gold 为空时「算成 0 并通过」是最危险的静默错误：它会把一条没有 gold 的
    # 样本洗成「召回失败」，也会把分母从平均里抹掉。实现约定是 None。
    record = {"gold_sha1_8": [], "results": _results("aaaa0001")}
    assert run_eval.recall_at_5(record) is None
    assert run_eval.recall_at_5({"results": _results("aaaa0001")}) is None


def test_recall_at_5_full_and_partial_hits() -> None:
    gold = {"aaaa0001", "bbbb0002"}
    assert run_eval.recall_at_5({"gold_sha1_8": list(gold), "results": _results("aaaa0001")}) == 0.5
    assert (
        run_eval.recall_at_5(
            {"gold_sha1_8": list(gold), "results": _results("aaaa0001", "bbbb0002", "cccc0003")}
        )
        == 1.0
    )


def test_recall_at_5_duplicate_rows_collapse_into_one_hit() -> None:
    record = {
        "gold_sha1_8": ["aaaa0001", "bbbb0002"],
        "results": _results("aaaa0001", "aaaa0001", "aaaa0001"),
    }
    assert run_eval.recall_at_5(record) == 0.5


def test_precision_at_5_without_gold_is_none() -> None:
    assert run_eval.precision_at_5({"gold_sha1_8": [], "results": _results("a")}) is None


def test_precision_at_5_zero_results_is_zero_not_none() -> None:
    # 契约口径：一条都没返回记 0.0，不能让这条样本从平均里消失。
    value = run_eval.precision_at_5({"gold_sha1_8": ["aaaa0001"], "results": []})
    assert value is not None
    assert value == 0.0


def test_precision_at_5_below_five_uses_the_returned_count() -> None:
    gold = ["aaaa0001", "bbbb0002", "cccc0003"]
    assert (
        run_eval.precision_at_5({"gold_sha1_8": gold, "results": _results("aaaa0001", "x")}) == 0.5
    )
    assert run_eval.precision_at_5({"gold_sha1_8": gold, "results": _results("x", "y", "z")}) == 0.0


def test_precision_at_5_at_five_results_uses_five_as_denominator() -> None:
    gold = ["aaaa0001", "bbbb0002"]
    record = {"gold_sha1_8": gold, "results": _results("aaaa0001", "bbbb0002", "x", "y", "z")}
    assert run_eval.precision_at_5(record) == pytest.approx(0.4)


def test_precision_at_5_over_five_results_counts_every_row() -> None:
    """现状钉桩：分母封顶 5、分子不封顶 → 2.0。**不代表口径正确**。

    口径缺陷见 test_precision_at_5_never_exceeds_one 的 xfail 说明；这条用例的
    存在价值是让将来修 bug 的人明确知道「改了就会红」，而不是偷偷改。
    """
    gold = [f"gold-{index}" for index in range(10)]
    record = {"gold_sha1_8": gold, "results": _results(*gold)}
    assert run_eval.precision_at_5(record) == pytest.approx(2.0)


@pytest.mark.xfail(
    reason=(
        "疑似缺陷 eval/run_eval.py:858-868：分子统计全部 results，分母却是 "
        "min(5, len(results))，len(results)>5 时 precision@5 可 >1.0。"
        "当前配置 query_top_k=5 不触发；validate_config 允许 query_top_k 到 50"
        "（eval/run_eval.py:207），改配置即可复现。计划 §3.6 定义的是 top-5。"
    ),
    strict=False,
)
def test_precision_at_5_never_exceeds_one() -> None:
    gold = [f"gold-{index}" for index in range(10)]
    record = {"gold_sha1_8": gold, "results": _results(*gold)}
    value = run_eval.precision_at_5(record)
    assert value is not None
    assert value <= 1.0


def test_recall_at_5_does_not_truncate_results_either() -> None:
    """同类口径缺口：结果超过 5 条时 recall 实际算的是 Recall@k。"""
    gold = ["aaaa0001"]
    record = {"gold_sha1_8": gold, "results": _results("x", "y", "z", "w", "v", "aaaa0001")}
    # 前 5 条都不命中，第 6 条才命中；top-5 口径应为 0.0。
    assert run_eval.recall_at_5(record) == 1.0


# ────────────────────────────────────────────────────────────────────────────
# P95：nearest-rank，手算钉死
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, 1.0),
        (2, 2.0),
        (19, 19.0),
        (20, 19.0),
        (21, 20.0),
        (100, 95.0),
        (101, 96.0),
    ],
)
def test_percentile_p95_nearest_rank_hand_computed(count: int, expected: float) -> None:
    values = [float(value) for value in range(1, count + 1)]
    assert run_eval.percentile_p95(values) == expected


def test_percentile_p95_is_not_the_maximum_at_n_equals_twenty() -> None:
    # 这是最容易写成 max 的地方：ceil(0.95*20)=19 → 取排序后第 19 个（下标 18）。
    values = [float(value) for value in range(1, 21)]
    assert run_eval.percentile_p95(values) == 19.0
    assert run_eval.percentile_p95(values) != max(values)


def test_percentile_p95_single_sample_and_empty() -> None:
    assert run_eval.percentile_p95([7.5]) == 7.5
    assert run_eval.percentile_p95([]) is None
    assert run_eval.percentile_p95([None, None]) is None


def test_percentile_p95_drops_none_before_ranking() -> None:
    # 两个有效值 n=2 → rank=2 → 取最大值，而不是把 None 当 0 排序。
    assert run_eval.percentile_p95([None, 1.0, 2.0]) == 2.0


def test_mean_or_none_distinguishes_empty_from_zero() -> None:
    assert run_eval.mean_or_none([]) is None
    assert run_eval.mean_or_none([0.0]) == 0.0
    assert run_eval.mean_or_none([1.0, 3.0]) == 2.0


# ────────────────────────────────────────────────────────────────────────────
# 拒答判定
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "answer_text", "expected"),
    [
        ("ABSTAINED", "", True),
        ("ABSTAINED", "有答案", True),
        ("ANSWERED", "知识库中没有检索到相关内容", False),
        ("DENIED", "没有检索到", False),
        ("DENY_NOT_404", "没有检索到", False),
        ("DENY_LEAKED", "没有检索到", False),
        ("ERROR", "没有检索到", False),
        ("UNKNOWN", "知识库中没有检索到相关内容", True),
        ("UNKNOWN", "", False),
        ("", "没有检索到", True),
    ],
)
def test_is_abstained_status_wins_over_the_text_marker(
    status: str, answer_text: str, expected: bool
) -> None:
    record = {"status": status, "answer_text": answer_text}
    assert run_eval.is_abstained(record) is expected


def test_is_abstained_marker_only_matches_the_configured_phrase() -> None:
    assert run_eval.ABSTAIN_MARKER == "没有检索到"
    assert run_eval.is_abstained({"answer_text": "没有检索到相关内容"}) is True
    assert run_eval.is_abstained({"answer_text": "没有找到"}) is False


# ────────────────────────────────────────────────────────────────────────────
# 引用核验：三条规则 + 三种常见坏引用
# ────────────────────────────────────────────────────────────────────────────


def test_audit_citations_excludes_non_answer_samples() -> None:
    # abstain / deny 的引用不进分母；现状是**既不 checked 也不 skipped**。
    for expect in ("abstain", "deny"):
        audit = run_eval.audit_citations(
            {"expect": expect, "citations": [_citation()], "expect_facts": ["上限 500 元"]}
        )
        assert audit.checked == 0
        assert audit.skipped == 0
        assert audit.accuracy is None


def test_audit_citations_skips_when_gold_docs_missing() -> None:
    audit = run_eval.audit_citations(
        _answer_with_citations([_citation(), _citation()], gold_docs=())
    )
    assert audit.checked == 0
    assert audit.skipped == 2
    assert audit.accuracy is None


def test_audit_citations_skips_when_expect_facts_empty() -> None:
    audit = run_eval.audit_citations(_answer_with_citations([_citation()], facts=()))
    assert audit.checked == 0
    assert audit.skipped == 1


def test_audit_citations_skips_non_dict_citation() -> None:
    audit = run_eval.audit_citations(_answer_with_citations(["oops"]))
    assert audit.checked == 0
    assert audit.skipped == 1


def test_audit_citations_unknown_document_id() -> None:
    # 情形一：引用了一个根本不存在的 id。
    audit = run_eval.audit_citations(_answer_with_citations([_citation(document_id="ghost-doc")]))
    assert audit.checked == 1
    assert audit.verified == 0
    assert audit.reasons == ["citation_document_not_in_gold"]
    assert audit.accuracy == 0.0


def test_audit_citations_cross_tenant_document_id() -> None:
    """情形二：引用越权（B 区）文档。

    现状无法与「id 不存在」区分：gold 只含 A 区文档，runner 又没有 B 区文档注册表，
    两者都落到 citation_document_not_in_gold。这是要上报的口径缺口，不是我看错。
    """
    audit = run_eval.audit_citations(
        _answer_with_citations([_citation(document_id="doc-b-tenants")])
    )
    assert audit.reasons == ["citation_document_not_in_gold"]


def test_audit_citations_old_version_snippet_without_fact() -> None:
    """情形三之一：引用同一文档但片段来自旧版本（v1 里没有 v2 才有的事实）。"""
    audit = run_eval.audit_citations(
        _answer_with_citations([_citation(snippet="旧版条款，不含新上限")])
    )
    assert audit.checked == 1
    assert audit.verified == 0
    assert audit.reasons == ["expect_fact_missing_in_snippet"]
    assert run_eval.CITATION_CODE_MAP["expect_fact_missing_in_snippet"] == ("CITATION_FACT_MISSING")


def test_audit_citations_old_version_snippet_with_fact_passes() -> None:
    """情形三之二：旧版片段恰好包含事实 → 判通过。

    计划 §3.7 要的 CITATION_VERSION_MISMATCH 没有实现（audit_citations 不看
    index_version / active_document_version_id），所以「引用旧版本」只有在事实缺失时
    才被间接发现。这条用例把缺口钉成可执行事实，由 Lead 决定是否补规则。
    """
    audit = run_eval.audit_citations(
        _answer_with_citations([_citation(snippet="上限 500 元（v1 旧版）")])
    )
    assert audit.verified == 1
    assert audit.accuracy == 1.0


def test_audit_citations_page_range_inverted() -> None:
    audit = run_eval.audit_citations(_answer_with_citations([_citation(page_no=4, page_end_no=2)]))
    assert audit.reasons == ["page_range_inverted"]
    assert run_eval.CITATION_CODE_MAP["page_range_inverted"] == "CITATION_PAGE_INVERTED"


def test_audit_citations_allows_both_pages_null() -> None:
    audit = run_eval.audit_citations(_answer_with_citations([_citation()]))
    assert audit.checked == 1
    assert audit.verified == 1
    assert audit.reasons == []


@pytest.mark.parametrize(
    ("page_no", "page_end_no", "expected"),
    [
        (None, None, True),
        (None, 3, False),
        (3, None, False),
        (3, 3, True),
        (3, 4, True),
        (4, 2, False),
        ("3", "4", True),
        ("a", "b", False),
        (True, True, True),
    ],
)
def test_page_range_ok(page_no: Any, page_end_no: Any, expected: bool) -> None:
    assert run_eval.page_range_ok(page_no, page_end_no) is expected


def test_citation_audit_add_counts_checked_and_verified() -> None:
    audit = run_eval.CitationAudit()
    assert audit.accuracy is None
    audit.add(None)
    audit.add("boom")
    # add() 只动 checked/verified/reasons；skipped 由「无能为力」的分支单独累加。
    assert (audit.checked, audit.verified, audit.skipped) == (2, 1, 0)
    assert audit.reasons == ["boom"]
    assert audit.accuracy == 0.5


# ────────────────────────────────────────────────────────────────────────────
# compute_metrics：分母语义
# ────────────────────────────────────────────────────────────────────────────


def test_compute_metrics_counts_and_denominators() -> None:
    records = [
        _answer_record(sample_id="a-01", gold_sha1_8=["aaaa0001"], results=[]),
        {
            "sample_id": "n-01",
            "category": "no_evidence",
            "expect": "abstain",
            "repeat": 1,
            "gold_sha1_8": [],
            "results": [],
            "status": "UNKNOWN",
            "answer_text": "知识库中没有检索到相关内容",
            "latency_ms": 5.0,
        },
    ]
    metrics = run_eval.compute_metrics(records)

    # 有 gold、零返回：recall 与 precision 都是 0.0（而不是被 None 丢掉）。
    assert metrics["recall_at_5"] == 0.0
    assert metrics["precision_at_5"] == 0.0
    assert metrics["abstain_accuracy"] == 1.0
    assert metrics["deny_interception"] is None
    assert metrics["citation_accuracy"] is None
    # n=2 的 P95 = 最大值 10.0。
    assert metrics["retrieval_p95_ms"] == 10.0
    counts = metrics["counts"]
    assert counts["samples"] == 2
    assert counts["unique_samples"] == 2
    assert counts["recall_samples"] == 1
    assert counts["precision_samples"] == 1
    assert counts["abstain_samples"] == 1
    assert counts["deny_samples"] == 0
    assert counts["abstain_used_text_fallback"] == 1
    assert counts["citations_checked"] == 0


def test_compute_metrics_citation_denominator_is_answer_samples_only() -> None:
    answer = _answer_record(
        sample_id="a-01",
        citations=[
            _citation(),
            _citation(page_no=None, page_end_no=None),
        ],
        gold_document_ids=["doc-a"],
        expect_facts=["上限 500 元"],
    )
    abstain = {
        "sample_id": "n-01",
        "expect": "abstain",
        "citations": [{"document_id": "zzz", "snippet": "nope"}],
        "status": "ABSTAINED",
        "answer_text": "没有检索到",
    }
    metrics = run_eval.compute_metrics([answer, abstain])

    assert metrics["citation_accuracy"] == 1.0
    assert metrics["counts"]["citations_checked"] == 2
    assert metrics["counts"]["citations_verified"] == 2
    assert metrics["counts"]["citations_skipped"] == 0


def test_compute_metrics_deny_interception() -> None:
    deny_ok = {
        "sample_id": "d-01",
        "expect": "deny",
        "deny": {"intercepted": True, "returned_200": False},
    }
    deny_bad = {
        "sample_id": "d-02",
        "expect": "deny",
        "deny": {"intercepted": False, "returned_200": True},
    }
    metrics = run_eval.compute_metrics([deny_ok, deny_bad])
    assert metrics["deny_interception"] == 0.5
    assert metrics["counts"]["deny_samples"] == 2


# ────────────────────────────────────────────────────────────────────────────
# aggregate：跨轮聚合
# ────────────────────────────────────────────────────────────────────────────


def test_aggregate_means_min_max_and_per_repeat() -> None:
    records = [
        _answer_record(sample_id="a-01", repeat=1, results=_results("aaaa0001"), latency_ms=10.0),
        _answer_record(sample_id="a-01", repeat=2, latency_ms=30.0),
    ]
    block = run_eval.aggregate(records, repeats=2)
    recall = block["metrics"]["recall_at_5"]

    assert recall["value"] == 0.5
    assert recall["min"] == 0.0
    assert recall["max"] == 1.0
    assert recall["per_repeat"] == [1.0, 0.0]
    assert recall["n"] == 2
    assert recall["definition"] == run_eval.METRIC_DEFINITIONS["recall_at_5"]
    assert block["metrics"]["retrieval_p95_ms"]["value"] == 20.0
    assert block["counts"]["samples"] == 2
    assert block["counts"]["unique_samples"] == 1
    assert block["per_category"]["fact"]["samples"] == 1
    assert block["repeats"] == 2


def test_aggregate_rounds_per_repeat_to_four_places() -> None:
    record = _answer_record(sample_id="a-01", gold_sha1_8=["a", "b", "c"], results=_results("a"))
    block = run_eval.aggregate([record], repeats=1)
    assert block["metrics"]["recall_at_5"]["per_repeat"] == [0.3333]


def test_aggregate_keeps_none_when_no_sample_supports_a_metric() -> None:
    abstain = {
        "sample_id": "n-01",
        "category": "no_evidence",
        "expect": "abstain",
        "repeat": 1,
        "status": "ABSTAINED",
        "answer_text": "",
        "citations": [],
    }
    block = run_eval.aggregate([abstain], repeats=1)
    recall = block["metrics"]["recall_at_5"]
    assert recall["value"] is None
    assert recall["min"] is None
    assert recall["max"] is None
    assert recall["n"] == 0
    assert recall["per_repeat"] == [None]


def test_metric_order_covers_every_definition() -> None:
    assert len(run_eval.METRIC_DEFINITIONS) == 8
    assert tuple(run_eval.METRIC_DEFINITIONS) == run_eval.METRIC_ORDER


# ────────────────────────────────────────────────────────────────────────────
# collect_failures：失败分类
# ────────────────────────────────────────────────────────────────────────────


def test_collect_failures_no_hit_vs_gold_miss() -> None:
    no_hit = _answer_record(sample_id="a-01", results=[])
    gold_miss = _answer_record(sample_id="a-02", results=_results("bbbb0002"))

    failures = run_eval.collect_failures([no_hit, gold_miss])
    codes = {item["sample_id"]: item["codes"] for item in failures}

    assert codes["a-01"] == ["NO_HIT"]
    assert codes["a-02"] == ["GOLD_MISS"]
    assert "recall@5=0.00" in failures[0]["detail"]


def test_collect_failures_citation_and_fact_codes() -> None:
    record = _answer_record(
        sample_id="a-01",
        results=_results("aaaa0001"),
        citations=[_citation(document_id="doc-b", snippet="别处")],
        gold_document_ids=["doc-a"],
        expect_facts=["上限 500 元"],
        answer_text="别的答案",
    )
    failures = run_eval.collect_failures([record])
    assert failures[0]["codes"] == ["CITATION_WRONG_DOC", "FACT_MISSING"]


def test_collect_failures_counts_invalid_citations() -> None:
    record = _answer_record(sample_id="a-01", results=_results("aaaa0001"), invalid_citations=2)
    failures = run_eval.collect_failures([record])
    assert failures[0]["codes"] == ["INVALID_CITATION"]
    assert "invalid_citations=2" in failures[0]["detail"]


def test_collect_failures_reports_non_deny_error_code() -> None:
    record = _answer_record(sample_id="a-01", results=[], error_code="INTERNAL_ERROR")
    failures = run_eval.collect_failures([record])
    assert failures[0]["codes"] == ["NO_HIT", "REQUEST_FAILED"]


def test_collect_failures_deny_404_is_not_a_request_failure() -> None:
    # deny 的 404 是正确答案。把它算成 REQUEST_FAILED 等于把安全项判成缺陷。
    record = {
        "sample_id": "d-01",
        "category": "deny",
        "expect": "deny",
        "repeat": 1,
        "error_code": "NOT_FOUND",
        "deny": {
            "returned_200": False,
            "intercepted": True,
            "query_http_status": 404,
            "chat_http_status": 404,
        },
    }
    assert run_eval.collect_failures([record]) == []


def test_collect_failures_deny_returned_200_is_a_forbidden_leak() -> None:
    record = {
        "sample_id": "d-01",
        "category": "deny",
        "expect": "deny",
        "repeat": 1,
        "deny": {"returned_200": True, "intercepted": False},
    }
    failures = run_eval.collect_failures([record])
    assert failures[0]["codes"] == ["FORBIDDEN_LEAK"]


def test_collect_failures_deny_not_intercepted_with_wrong_status() -> None:
    record = {
        "sample_id": "d-01",
        "category": "deny",
        "expect": "deny",
        "repeat": 1,
        "deny": {
            "returned_200": False,
            "intercepted": False,
            "query_http_status": 403,
            "chat_http_status": 500,
        },
    }
    failures = run_eval.collect_failures([record])
    assert failures[0]["codes"] == ["DENY_NOT_404", "REQUEST_FAILED"]


def test_collect_failures_abstain_over_retrieval_vs_gate_miss() -> None:
    over = {
        "sample_id": "n-01",
        "category": "no_evidence",
        "expect": "abstain",
        "repeat": 1,
        "status": "ANSWERED",
        "answer_text": "有答案",
        "citations": [{"document_id": "doc-a"}],
    }
    gate_miss = {
        "sample_id": "n-02",
        "category": "no_evidence",
        "expect": "abstain",
        "repeat": 1,
        "status": "ANSWERED",
        "answer_text": "有答案",
        "citations": [],
    }
    proper = {
        "sample_id": "n-03",
        "category": "no_evidence",
        "expect": "abstain",
        "repeat": 1,
        "status": "ABSTAINED",
        "answer_text": "没有检索到",
        "citations": [],
    }
    codes = {
        item["sample_id"]: item["codes"]
        for item in run_eval.collect_failures([over, gate_miss, proper])
    }
    assert codes["n-01"] == ["OVER_RETRIEVAL"]
    assert codes["n-02"] == ["EVIDENCE_GATE_MISS"]
    assert "n-03" not in codes


# ────────────────────────────────────────────────────────────────────────────
# 文本 / 哈希工具
# ────────────────────────────────────────────────────────────────────────────


def test_sha1_8_matches_known_vectors_and_utf8_encoding() -> None:
    assert run_eval.sha1_8("abc") == "a9993e36"
    assert run_eval.sha1_8("") == "da39a3ee"
    text = "上限 500 元"
    assert run_eval.sha1_8(text) == hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    assert len(run_eval.sha1_8(text)) == 8


def test_truncate_keeps_short_text_and_cuts_long_text_without_ellipsis() -> None:
    assert run_eval.MAX_CONTENT_CHARS == 120
    assert run_eval.truncate("short") == "short"
    assert run_eval.truncate("x" * 120) == "x" * 120
    cut = run_eval.truncate("x" * 121)
    assert cut == "x" * 120
    assert not cut.endswith("…")


def test_error_code_of_only_reads_the_unified_error_body() -> None:
    assert run_eval.error_code_of({"error": {"code": "NOT_FOUND"}}) == "NOT_FOUND"
    assert run_eval.error_code_of({"error": {"code": ""}}) is None
    assert run_eval.error_code_of({"error": "NOT_FOUND"}) is None
    assert run_eval.error_code_of({"code": "NOT_FOUND"}) is None
    assert run_eval.error_code_of("NOT_FOUND") is None
    assert run_eval.error_code_of(None) is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("", False),
        ("   ", False),
        ('{"error":{"code":"NOT_FOUND"},"request_id":"r"}', False),
        ('{"count": 0}', False),
        ('{"results": [], "citations": []}', False),
        ('{"results": [{"content": "片段"}]}', True),
        ('{"citations": [{"snippet": "片段"}]}', True),
        ('{"data": {"content": "片段"}}', True),
        ("502 Bad Gateway", True),
    ],
)
def test_body_leaks(body: str, expected: bool) -> None:
    assert run_eval.body_leaks(body) is expected


def test_safe_json_returns_none_on_bad_json() -> None:
    assert run_eval.safe_json('{"a": 1}') == {"a": 1}
    assert run_eval.safe_json("not json") is None
    assert run_eval.safe_json("") is None


# ────────────────────────────────────────────────────────────────────────────
# _result_row：raw 记录的形状
# ────────────────────────────────────────────────────────────────────────────


def test_result_row_hashes_full_content_but_stores_only_120_chars() -> None:
    content = "中" * 200
    row = run_eval._result_row(
        {
            "content": content,
            "document_id": "doc-a",
            "chunk_ordinal": 3,
            "index_version": 2,
            "rrf_score": 0.5,
            "vector_rank": 1,
            "keyword_rank": None,
        }
    )
    assert row["content"] == content[:120]
    assert row["content_sha1_8"] == run_eval.sha1_8(content)
    assert row["document_id"] == "doc-a"
    assert row["chunk_ordinal"] == 3
    assert row["index_version"] == 2
    assert row["rrf_score"] == 0.5
    assert row["vector_rank"] == 1
    assert row["keyword_rank"] is None


def test_result_row_has_no_active_version_id_and_blank_content_is_empty_string() -> None:
    # 读契约没有 active_document_version_id，实现选择留 None 而不是编一个 UUID。
    row = run_eval._result_row({"content": None})
    assert row["active_document_version_id"] is None
    assert row["content"] == ""
    assert row["content_sha1_8"] == run_eval.sha1_8("")
