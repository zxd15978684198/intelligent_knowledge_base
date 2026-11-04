"""`eval/review.py` 的单测：三态映射、账本合并、raw 回填的字节级约束。

为什么盯这些点：

- `unknown -> null` 是 P4 的口径核心。`null` 不是 `false`、也不是通过——用 `is` 断言，
  顺手证明 `0`/`"pass"` 这类手写值不会被静默当成"评过"。
- 回填只许动 `review` 一个键。整文件 `json.loads` + `json.dumps` 重写会让每条未复核的
  行也跟着重排格式，所以用例按**字节**比对：行数、行尾（CRLF）、其余行原样。
- `results` 里嵌套的 `"review"` 不能被误认成记录自己的复核字段。

加载方式照 `tests/unit/test_eval_harness.py`：`eval/` 不是包，用 importlib 按路径加载
并登记进 `sys.modules`。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"


def load_eval_module(name: str, module_name: str) -> ModuleType:
    path = EVAL_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - eval/ 被删才会发生
        raise RuntimeError(f"加载不到 {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


review = load_eval_module("review", "_eval_review_under_test")


def _record(case_id: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "case_id": case_id,
        "sample_id": case_id,
        "answer_text": "答案[资料 1]",
        "citations": [{"chunk_id": "c-1"}],
    }
    payload.update(extra)
    return payload


def _write_raw(path: Path, records: list[dict[str, Any]], newline: str = "\r\n") -> None:
    """按 run_eval 的写法落盘：一行一条、UTF-8 不转义（真实文件是 CRLF）。"""
    path.write_bytes(
        "".join(json.dumps(record, ensure_ascii=False) + newline for record in records).encode(
            "utf-8"
        )
    )


def _run_dir(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_raw(run_dir / "raw.jsonl", records)
    return run_dir


def _raw_lines(path: Path) -> list[str]:
    return path.read_bytes().decode("utf-8").splitlines(keepends=True)


# --------------------------------------------------------------------------- 三态


def test_state_tokens_map_to_true_false_null() -> None:
    assert review.STATE_TO_BOOL == {"pass": True, "fail": False, "unknown": None}


def test_unknown_is_neither_failure_nor_pass() -> None:
    """P4 的硬口径：`unknown` 落 `null`，既不等于 `false`，也不等于通过。"""
    unknown = review.STATE_TO_BOOL["unknown"]
    assert unknown is None
    assert unknown is not False
    assert unknown is not True
    assert review.STATE_TO_BOOL["fail"] is False
    assert review.STATE_TO_BOOL["pass"] is True


def test_tristate_accepts_only_json_tristate() -> None:
    assert review.tristate(None, "x") is None
    assert review.tristate(True, "x") is True
    assert review.tristate(False, "x") is False
    for bad in ("pass", "unknown", 0, 1, "", [], {}):
        with pytest.raises(review.ReviewError):
            review.tristate(bad, "x")


def test_normalize_review_fills_missing_keys_with_null() -> None:
    """缺键 = 没评 = `null`；不能因为缺键就当成 False。"""
    normalized = review.normalize_review({"fact_pass": True}, "x")
    assert normalized == {"fact_pass": True, "citation_pass": None, "security_pass": None}
    assert review.normalize_review(None, "x") == review.empty_review()


# --------------------------------------------------------------------------- 账本


def test_ledger_roundtrip_keeps_order_and_overwrites_in_place(tmp_path: Path) -> None:
    path = tmp_path / "review.jsonl"
    ledger = {
        "b-case": {"fact_pass": None, "citation_pass": None, "security_pass": None, "note": None},
        "a-case": {"fact_pass": True, "citation_pass": False, "security_pass": None, "note": "n"},
    }
    review.save_ledger(path, ledger)
    loaded = review.load_ledger(path)
    assert list(loaded) == ["b-case", "a-case"]
    assert loaded["a-case"]["fact_pass"] is True
    assert loaded["a-case"]["citation_pass"] is False
    assert loaded["a-case"]["note"] == "n"


def test_ledger_rejects_illegal_tristate(tmp_path: Path) -> None:
    path = tmp_path / "review.jsonl"
    path.write_text('{"case_id": "c", "fact_pass": "pass"}\n', encoding="utf-8")
    with pytest.raises(review.ReviewError):
        review.load_ledger(path)


def test_apply_updates_only_touches_given_keys() -> None:
    ledger: dict[str, dict[str, Any]] = {}
    first = review.apply_updates(
        ledger, "c1", _args(fact="pass", citation=None, security=None, note="首次")
    )
    assert first == {"fact_pass": True, "citation_pass": None, "security_pass": None}
    assert ledger["c1"]["note"] == "首次"
    second = review.apply_updates(
        ledger, "c1", _args(fact=None, citation="fail", security=None, note=None)
    )
    assert second["fact_pass"] is True  # 没给的键保持原值
    assert second["citation_pass"] is False
    assert ledger["c1"]["note"] == "首次"  # note 未给也不清空


def _args(**kwargs: Any) -> Any:
    class Args:
        pass

    args = Args()
    for key, value in kwargs.items():
        setattr(args, key, value)
    return args


# --------------------------------------------------------------------------- raw 回填


def test_backfill_appends_review_and_leaves_other_lines_byte_identical(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw.jsonl"
    _write_raw(raw, [_record("c1"), _record("c2")])
    before = _raw_lines(raw)
    target = {"fact_pass": True, "citation_pass": False, "security_pass": None}

    changed, total = review.backfill_raw(raw, {"c1": target})

    after = _raw_lines(raw)
    assert (changed, total) == (1, 2)
    assert len(after) == len(before)
    assert after[0].endswith("\r\n") and after[1].endswith("\r\n")  # 行尾没有被换成 LF
    assert after[1] == before[1]  # 没被点名的行一个字节都没动
    old_body = before[0][: -len("\r\n")]
    new_body = after[0][: -len("\r\n")]
    assert new_body.startswith(old_body[:-1])  # 只在结尾的 } 前插入，前面原样
    assert json.loads(new_body)["review"] == target


def test_backfill_uses_sample_id_when_case_id_missing(tmp_path: Path) -> None:
    raw = tmp_path / "raw.jsonl"
    record = _record("legacy-01")
    record.pop("case_id")
    _write_raw(raw, [record])

    changed, _ = review.backfill_raw(
        raw, {"legacy-01": {"fact_pass": None, "citation_pass": None, "security_pass": True}}
    )

    assert changed == 1
    parsed = json.loads(_raw_lines(raw)[0])
    assert parsed["review"]["security_pass"] is True
    assert parsed["sample_id"] == "legacy-01"


def test_backfill_replaces_existing_review_without_duplicating_the_key(tmp_path: Path) -> None:
    raw = tmp_path / "raw.jsonl"
    record = _record("c1")
    record["review"] = {"fact_pass": None, "citation_pass": None, "security_pass": None}
    _write_raw(raw, [record])
    before = _raw_lines(raw)

    review.backfill_raw(
        raw, {"c1": {"fact_pass": False, "citation_pass": None, "security_pass": True}}
    )

    after = _raw_lines(raw)
    assert after[0].count('"review"') == 1
    assert len(after) == len(before)
    assert json.loads(after[0])["review"] == {
        "fact_pass": False,
        "citation_pass": None,
        "security_pass": True,
    }


def test_backfill_never_touches_nested_review_key(tmp_path: Path) -> None:
    """`results` 里的 `"review"` 属于检索结果，不是这条记录的复核字段。"""
    raw = tmp_path / "raw.jsonl"
    record = _record("c1", results=[{"chunk_id": "c-1", "review": {"fact_pass": False}}])
    _write_raw(raw, [record])

    review.backfill_raw(
        raw, {"c1": {"fact_pass": True, "citation_pass": None, "security_pass": None}}
    )

    parsed = json.loads(_raw_lines(raw)[0])
    assert parsed["results"][0]["review"] == {"fact_pass": False}  # 嵌套的一格没被改写
    assert parsed["review"]["fact_pass"] is True


def test_backfill_skips_when_review_already_matches(tmp_path: Path) -> None:
    raw = tmp_path / "raw.jsonl"
    target = {"fact_pass": True, "citation_pass": True, "security_pass": None}
    record = _record("c1")
    record["review"] = dict(target)
    _write_raw(raw, [record])
    before = raw.read_bytes()

    changed, total = review.backfill_raw(raw, {"c1": target})

    assert (changed, total) == (0, 1)
    assert raw.read_bytes() == before  # 没改动就不写盘（连 mtime 都不动）


def test_backfill_keeps_line_without_trailing_newline(tmp_path: Path) -> None:
    raw = tmp_path / "raw.jsonl"
    raw.write_bytes(json.dumps(_record("c1"), ensure_ascii=False).encode("utf-8"))

    review.backfill_raw(
        raw, {"c1": {"fact_pass": True, "citation_pass": None, "security_pass": None}}
    )

    text = raw.read_bytes().decode("utf-8")
    assert not text.endswith("\n")
    assert json.loads(text)["review"]["fact_pass"] is True


def test_backfill_replaces_review_in_the_middle_like_run_eval_writes_it(
    tmp_path: Path,
) -> None:
    """`run_eval` 把 `review` 放在 `deny` 与 `gold_sha1_8` 之间，不是行尾。

    换值只能动 `review` 的 value 区间：前后的 `, ` 与其余键的字节都不许变，
    否则 `review` 之前的一整段会被顺手重排。
    """
    raw = tmp_path / "raw.jsonl"
    record = {
        "sample_id": "c1",
        "case_id": "c1",
        "deny": False,
        "review": {"fact_pass": None, "citation_pass": None, "security_pass": None},
        "gold_sha1_8": "deadbeef",
        "notes": "员工手册v2",
    }
    _write_raw(raw, [record])
    before = _raw_lines(raw)

    review.backfill_raw(
        raw, {"c1": {"fact_pass": True, "citation_pass": False, "security_pass": None}}
    )

    after = _raw_lines(raw)
    prefix = before[0][: before[0].index('"review"')]
    assert after[0].startswith(prefix)
    assert after[0].endswith(', "gold_sha1_8": "deadbeef", "notes": "员工手册v2"}\r\n')
    parsed = json.loads(after[0])
    assert parsed["review"] == {
        "fact_pass": True,
        "citation_pass": False,
        "security_pass": None,
    }
    assert list(parsed) == ["sample_id", "case_id", "deny", "review", "gold_sha1_8", "notes"]


# --------------------------------------------------------------------------- null_ratio


def _view(case_id: str, fact: bool | None, citation: bool | None, security: bool | None):
    return review.CaseView(
        case_id,
        {"fact_pass": fact, "citation_pass": citation, "security_pass": security},
        None,
        "raw.jsonl",
    )


def test_null_ratio_counts_only_fully_null_cases() -> None:
    views = [
        _view("a", None, None, None),
        _view("b", True, None, None),  # 只评了一项：不算"三项全 null"
        _view("c", None, None, None),
        _view("d", False, False, False),
    ]
    all_null, total, ratio = review.null_ratio_of(views)
    assert (all_null, total) == (2, 4)
    assert ratio == pytest.approx(0.5)


def test_null_ratio_threshold_is_strictly_greater_than_20_percent() -> None:
    at_limit = [_view(f"c{n}", None, None, None) for n in range(1, 5)]
    at_limit += [_view(f"ok{n}", True, True, True) for n in range(1, 17)]
    assert review.null_ratio_of(at_limit)[2] == pytest.approx(0.2)
    assert "PASS" in review.null_ratio_line(at_limit)
    assert "0.2000" in review.null_ratio_line(at_limit)

    over_limit = [_view(f"c{n}", None, None, None) for n in range(1, 6)]
    over_limit += [_view(f"ok{n}", True, True, True) for n in range(1, 16)]
    assert review.null_ratio_of(over_limit)[2] == pytest.approx(0.25)
    assert "FAIL" in review.null_ratio_line(over_limit)


def test_null_ratio_of_empty_run_is_not_zero() -> None:
    """没有 case 时占比是 `None`（`n/a`），不能被读成"全部复核完毕"。"""
    all_null, total, ratio = review.null_ratio_of([])
    assert (all_null, total, ratio) == (0, 0, None)
    assert "n/a" in review.null_ratio_line([])


# --------------------------------------------------------------------------- 命令行


def test_main_list_creates_empty_ledger_and_prints_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _run_dir(tmp_path, [_record("c1"), _record("c2")])

    code = review.main(["--run", str(run_dir), "--list"])

    out = capsys.readouterr().out
    assert code == 0
    assert (run_dir / "review.jsonl").exists()
    assert (run_dir / "review.jsonl").read_text(encoding="utf-8") == ""
    assert "null_ratio = 1.0000" in out
    assert "FAIL" in out
    assert "c1" in out and "c2" in out


def test_main_set_writes_ledger_and_backfills_raw(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _run_dir(tmp_path, [_record("c1"), _record("c2")])

    code = review.main(
        ["--run", str(run_dir), "--case", "c1", "--fact", "pass", "--note", "注册资本在切片 0"]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "回填 1 行" in out
    lines = (run_dir / "review.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry == {
        "case_id": "c1",
        "fact_pass": True,
        "citation_pass": None,
        "security_pass": None,
        "note": "注册资本在切片 0",
    }
    raw = [json.loads(line) for line in _raw_lines(run_dir / "raw.jsonl")]
    assert raw[0]["review"] == {"fact_pass": True, "citation_pass": None, "security_pass": None}
    assert "review" not in raw[1]  # 没点名的 case 不回填


def test_main_set_twice_overwrites_in_place(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _run_dir(tmp_path, [_record("c1"), _record("c2")])
    review.main(["--run", str(run_dir), "--case", "c1", "--fact", "pass"])
    review.main(["--run", str(run_dir), "--case", "c1", "--citation", "unknown"])
    capsys.readouterr()

    lines = (run_dir / "review.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # 同一 case 后写覆盖前值，不追加第二行
    entry = json.loads(lines[0])
    assert entry["fact_pass"] is True
    assert entry["citation_pass"] is None
    assert _raw_lines(run_dir / "raw.jsonl")[0].count('"review"') == 1


def test_main_show_case_without_flags_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _run_dir(tmp_path, [_record("c1")])
    before = (run_dir / "raw.jsonl").read_bytes()

    code = review.main(["--run", str(run_dir), "--case", "c1"])

    out = capsys.readouterr().out
    assert code == 0
    assert "fact_pass=unknown" in out
    assert (run_dir / "review.jsonl").read_text(encoding="utf-8") == ""
    assert (run_dir / "raw.jsonl").read_bytes() == before


def test_main_missing_run_dir_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "nope"

    code = review.main(["--run", str(missing), "--list"])

    err = capsys.readouterr().err
    assert code == 2
    assert "不存在" in err
    assert not missing.exists()  # 报错路径不许悄悄建目录


def test_main_run_dir_without_raw_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """目录存在但没有 raw.jsonl：不是"还没复核的 run"，是 `--run` 指错了。"""
    run_dir = tmp_path / "empty-run"
    run_dir.mkdir()

    code = review.main(["--run", str(run_dir), "--list"])

    err = capsys.readouterr().err
    assert code == 2
    assert "raw.jsonl" in err


def test_main_unknown_case_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _run_dir(tmp_path, [_record("c1")])

    code = review.main(["--run", str(run_dir), "--case", "typo-01", "--fact", "pass"])

    err = capsys.readouterr().err
    assert code == 2
    assert "不在" in err
    assert (run_dir / "review.jsonl").read_text(encoding="utf-8") == ""


def test_main_list_and_case_are_mutually_exclusive(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, [_record("c1")])
    with pytest.raises(SystemExit) as excinfo:
        review.main(["--run", str(run_dir), "--list", "--case", "c1"])
    assert excinfo.value.code == 2


def test_main_requires_a_mode(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, [_record("c1")])
    with pytest.raises(SystemExit) as excinfo:
        review.main(["--run", str(run_dir)])
    assert excinfo.value.code == 2


def test_resolve_run_accepts_bare_directory_name(tmp_path: Path) -> None:
    assert review.resolve_run(str(EVAL_DIR / "results" / "baseline-20261030")).name == (
        "baseline-20261030"
    )
    with pytest.raises(review.ReviewError):
        review.resolve_run("definitely-not-a-run-dir")
