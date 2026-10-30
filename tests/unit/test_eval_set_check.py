"""`eval/build_eval_set.py` 与 `eval/make_configs.py` 的纯函数单测。

两块都不连网、不连库、不跑模型：

* 评测集侧：配额 6+4+3+3+2+2、`category`/`expect` 一致性、gold 形状、
  `resolve_gold` 的定位与快照一致性；
* 配置生成侧：snapshot 裁剪、15 字段顺序、生成的 A/B 两份必须只差一个主因素、
  yaml 头注释、评测集与 snapshot 的对账。

为什么这些用例要自己造 20 条 jsonl 而不是直接读 `eval/eval_set_v0.1.jsonl`：
产物是 `--build` 的输出，依赖真库与 API；单测不能因为它被重新生成（或还没生成）
就红。真实产物另有一条 skip-if-missing 的用例兜底。

加载方式与 `test_eval_harness.py` 相同（importlib 按路径 + 先登记 `sys.modules`），
模块名带 `_evalset` 后缀，避免和另一个测试文件互相覆盖。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from collections import Counter
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
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_eval_set = load_eval_module("build_eval_set", "_eval_build_eval_set")
make_configs = load_eval_module("make_configs", "_eval_make_configs")
run_eval = load_eval_module("run_eval", "_eval_run_eval_evalset")

WS = "11111111-1111-4111-8111-111111111111"
KB = "kb-aaaa"
DENY_KB = "kb-bbbb"
HEAD = "0123456789abcdef0123456789abcdef01234567"


# ────────────────────────────────────────────────────────────────────────────
# 造数据的小工具
# ────────────────────────────────────────────────────────────────────────────


def _setup() -> Any:
    return build_eval_set.EvalSetup(kb_id=KB, workspace_id=WS, docs={}, deny_kb_id=DENY_KB)


def _gold(seed: Any) -> list[dict[str, Any]]:
    return [
        {
            "corpus": seed.corpus,
            "document_id": f"doc-{seed.corpus}",
            "chunk_ordinal": 0,
            "content_sha1_8": "deadbeef",
        }
    ]


def _records() -> list[dict[str, Any]]:
    """用脚本内置的 20 条种子现造一份合格评测集（不读仓库产物）。"""
    setup = _setup()
    records = []
    for seed in build_eval_set.SEEDS:
        gold = _gold(seed) if build_eval_set.EXPECT_OF[seed.category] == "answer" else []
        records.append(build_eval_set._record(seed, setup, gold))
    return records


def _first(category: str) -> dict[str, Any]:
    for record in _records():
        if record["category"] == category:
            return record
    raise AssertionError(f"种子里没有 {category}")


def _validate(record: dict[str, Any], *, workspace_id: str | None = None) -> list[str]:
    problems: list[str] = []
    build_eval_set._validate_record(record, 1, problems, workspace_id=workspace_id)
    return problems


def _write_yaml(path: Path, cfg: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _base() -> dict[str, Any]:
    return {
        "prompt_version": "pyknora-rag-v1",
        "chat_model": "deepseek-v4.1-flash",
        "chat_base_url": "http://127.0.0.1:8010/v1",
        "chat_timeout_s": 60.0,
        "embedding_model_key": "BAAI/bge-small-zh-v1.5",
        "embedding_dim": 512,
        "embedding_backend": "hashing",
    }


def _raw_setup() -> dict[str, Any]:
    return {
        "workspace_id": WS,
        "kb_id": KB,
        "documents": [
            {
                "document_id": "doc-a",
                "active_document_version_id": "ver-2",
                "active_build_id": "build-2",
                "active_index_version": 2,
                "chunks": 12,
            }
        ],
    }


def _snapshot() -> dict[str, Any]:
    return make_configs.build_snapshot(_raw_setup())


def _config(strategy: str) -> dict[str, Any]:
    return make_configs.build_config(
        base=_base(), snapshot=_snapshot(), strategy=strategy, head=HEAD
    )


def _chunk(ordinal: int, content: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "chunk_ordinal": ordinal,
        "content": content,
        "index_version": 2,
        "build_id": "build-2",
        "active_document_version_id": "ver-2",
    }
    row.update(overrides)
    return row


def _snapshot_of_doc() -> Any:
    return build_eval_set.CorpusSnapshot(
        corpus="corpus-a",
        document_id="doc-a",
        active_document_version_id="ver-2",
        active_build_id="build-2",
        active_index_version=2,
    )


def _seed(**overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "sample_id": "ver-01",
        "category": "version",
        "query": "新版上限是多少",
        "corpus": "corpus-a",
        "locator": "每月至少对库存票据",
        # 默认不声明事实：多数用例只关心定位与快照一致性，事实校验单独测。
        "expect_facts": (),
        "notes": "对应 v2 第 3 页",
    }
    kwargs.update(overrides)
    return build_eval_set.Seed(**kwargs)


# ────────────────────────────────────────────────────────────────────────────
# 种子与配额：6 + 4 + 3 + 3 + 2 + 2
# ────────────────────────────────────────────────────────────────────────────


def test_quota_is_the_plan_quota_and_sums_to_twenty() -> None:
    assert build_eval_set.QUOTA == {
        "fact": 6,
        "exact_term": 4,
        "synonym": 3,
        "no_evidence": 3,
        "deny": 2,
        "version": 2,
    }
    assert sum(build_eval_set.QUOTA.values()) == 20
    assert len(build_eval_set.SEEDS) == 20
    assert Counter(seed.category for seed in build_eval_set.SEEDS) == build_eval_set.QUOTA


def test_seed_ids_are_unique_and_category_ids_are_stable() -> None:
    ids = [seed.sample_id for seed in build_eval_set.SEEDS]
    assert len(set(ids)) == 20
    assert ids == [
        *(f"fact-{index:02d}" for index in range(1, 7)),
        *(f"exact-{index:02d}" for index in range(1, 5)),
        *(f"syn-{index:02d}" for index in range(1, 4)),
        *(f"noev-{index:02d}" for index in range(1, 4)),
        *(f"deny-{index:02d}" for index in range(1, 3)),
        *(f"ver-{index:02d}" for index in range(1, 3)),
    ]


def test_expect_of_covers_every_category_without_mixing_deny_and_abstain() -> None:
    assert set(build_eval_set.EXPECT_OF) == set(build_eval_set.CATEGORIES)
    assert build_eval_set.EXPECT_OF["no_evidence"] == "abstain"
    assert build_eval_set.EXPECT_OF["deny"] == "deny"
    for category in ("fact", "exact_term", "synonym", "version"):
        assert build_eval_set.EXPECT_OF[category] == "answer"


def test_answer_seeds_carry_locator_and_facts_while_negative_seeds_do_not() -> None:
    for seed in build_eval_set.SEEDS:
        assert seed.notes, f"{seed.sample_id} 没有 notes"
        if build_eval_set.EXPECT_OF[seed.category] == "answer":
            assert seed.corpus and seed.locator, f"{seed.sample_id} 缺定位"
            assert seed.expect_facts, f"{seed.sample_id} 缺 expect_facts"
        else:
            # abstain / deny 没有可回填的 gold，也没有可断言的事实；
            # 有的话 _validate_record 会直接判不合格。
            assert seed.corpus is None and seed.locator is None
            assert seed.expect_facts == ()


# ────────────────────────────────────────────────────────────────────────────
# 文本 / 数值小工具
# ────────────────────────────────────────────────────────────────────────────


def test_norm_ws_drops_whitespace_but_keeps_character_order() -> None:
    assert build_eval_set.norm_ws("每月至少 对库存票据") == "每月至少对库存票据"
    assert build_eval_set.norm_ws("每月至少\n对库存\t票据") == "每月至少对库存票据"
    assert build_eval_set.norm_ws("每月至少\u3000对库存票据") == "每月至少对库存票据"
    # 只丢空白：标点与字序必须原样保留，否则两条不同条款会被匹配成一条。
    assert build_eval_set.norm_ws("上限 500 元，不是 600 元") == "上限500元，不是600元"


def test_sha1_8_is_the_same_algorithm_as_the_runner() -> None:
    for text in ("", "abc", "上限 500 元"):
        expected = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        assert build_eval_set.sha1_8(text) == expected
        assert build_eval_set.sha1_8(text) == run_eval.sha1_8(text)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3, 3), (0, 0), (True, None), (False, None), ("3", None), (3.0, None), (None, None)],
)
def test_optional_int_rejects_bool_and_non_int(value: Any, expected: int | None) -> None:
    assert build_eval_set._optional_int(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("x", "x"), ("", None), ("   ", "   "), (1, None), (None, None)],
)
def test_optional_str_only_accepts_non_empty_strings(value: Any, expected: str | None) -> None:
    assert build_eval_set._optional_str(value) is expected


@pytest.mark.parametrize(
    ("actual", "expected", "mismatch"),
    [
        (1, 2, True),
        (1, 1, False),
        (None, 2, False),
        (1, None, False),
        (None, None, False),
        ("a", "b", True),
    ],
)
def test_mismatch_never_flags_a_missing_side(actual: Any, expected: Any, mismatch: bool) -> None:
    assert build_eval_set._mismatch(actual, expected) is mismatch


# ────────────────────────────────────────────────────────────────────────────
# _record + _validate_record：形状与 category/expect 一致性
# ────────────────────────────────────────────────────────────────────────────


def test_every_seed_becomes_a_valid_record() -> None:
    for record in _records():
        assert _validate(record) == [], record["id"]


def test_deny_record_shape() -> None:
    record = _first("deny")
    assert record["expect"] == "deny"
    assert record["kb_id"] is None
    assert record["deny_kb_id"] == DENY_KB
    assert record["gold_chunks"] == []
    assert record["expect_facts"] == []


def test_answer_record_shape() -> None:
    record = _first("fact")
    assert record["expect"] == "answer"
    assert record["kb_id"] == KB
    assert record["deny_kb_id"] is None
    assert record["workspace_id"] == WS
    assert record["top_k"] == build_eval_set.TOP_K == 5
    assert record["category"] == "fact"
    seed = next(item for item in build_eval_set.SEEDS if item.sample_id == record["id"])
    # gold 的 corpus/document_id 由 setup 回填，不手写：这里只验证它确实来自该 seed。
    assert record["gold_chunks"][0]["corpus"] == seed.corpus
    assert record["gold_chunks"][0]["document_id"] == f"doc-{seed.corpus}"


def test_validate_record_rejects_expect_mismatch() -> None:
    record = _first("no_evidence")
    record["expect"] = "deny"
    problems = _validate(record)
    assert any("no_evidence 的 expect 必须是 'abstain'" in item for item in problems)


def test_validate_record_rejects_unknown_category() -> None:
    record = _first("fact")
    record["category"] = "weird"
    problems = _validate(record)
    assert any("category 必须是 fact/exact_term/synonym" in item for item in problems)


def test_validate_record_rejects_missing_field() -> None:
    record = _first("fact")
    del record["notes"]
    problems = _validate(record)
    assert problems == ["fact-01：缺字段 ['notes']（字段名照计划 §3.3，别用讲义那套）"]


def test_validate_record_rejects_top_k_drift() -> None:
    record = _first("fact")
    record["top_k"] = 3
    problems = _validate(record)
    assert any("top_k 必须是 5（Recall@5 口径），实际 3" in item for item in problems)


@pytest.mark.parametrize(
    ("field", "value", "needle"),
    [
        ("query", "   ", "query 不能为空"),
        ("notes", "", "notes 不能为空"),
        ("workspace_id", "", "workspace_id 不能为空"),
        ("workspace_id", "not-a-uuid", "workspace_id 不是合法 UUID"),
        ("kb_id", "", "answer/abstain 样本必须有 kb_id"),
        ("deny_kb_id", DENY_KB, "非 deny 样本的 deny_kb_id 必须是 null"),
        ("expect_facts", [], "answer 样本必须有 expect_facts"),
        ("expect_facts", "x", "expect_facts 必须是数组"),
        ("expect_facts", [""], "expect_facts 里混入空值或非字符串"),
    ],
)
def test_validate_record_rejects_bad_scalars(field: str, value: Any, needle: str) -> None:
    record = _first("fact")
    record[field] = value
    problems = _validate(record)
    assert any(needle in item for item in problems), problems


def test_validate_record_enforces_the_expected_workspace() -> None:
    record = _first("fact")
    problems = _validate(record, workspace_id="22222222-2222-4222-8222-222222222222")
    assert any("workspace_id 必须是 2222" in item for item in problems)


@pytest.mark.parametrize(
    ("gold", "needle"),
    [
        ([], "gold_chunks 不能为空（先跑 --build 回填，不许手填）"),
        ("oops", "gold_chunks 不能为空（先跑 --build 回填，不许手填）"),
        ([1], "gold_chunks[0] 必须是对象"),
        (
            [{"corpus": "c", "document_id": "d", "chunk_ordinal": 0}],
            "gold_chunks[0] 缺 ['content_sha1_8']",
        ),
        (
            [
                {
                    "corpus": "c",
                    "document_id": "d",
                    "chunk_ordinal": True,
                    "content_sha1_8": "deadbeef",
                }
            ],
            "chunk_ordinal 必须是 0 起的整数",
        ),
        (
            [
                {
                    "corpus": "c",
                    "document_id": "d",
                    "chunk_ordinal": -1,
                    "content_sha1_8": "deadbeef",
                }
            ],
            "chunk_ordinal 必须是 0 起的整数",
        ),
        (
            [{"corpus": "c", "document_id": "d", "chunk_ordinal": 0, "content_sha1_8": "DEADBEEF"}],
            "content_sha1_8 必须是 sha1 前 8 位小写 hex",
        ),
        (
            [{"corpus": "c", "document_id": "d", "chunk_ordinal": 0, "content_sha1_8": "abc"}],
            "content_sha1_8 必须是 sha1 前 8 位小写 hex",
        ),
        (
            [{"corpus": "", "document_id": "d", "chunk_ordinal": 0, "content_sha1_8": "deadbeef"}],
            "gold_chunks[0].corpus 不能为空",
        ),
        (
            [{"corpus": "c", "document_id": "", "chunk_ordinal": 0, "content_sha1_8": "deadbeef"}],
            "gold_chunks[0].document_id 不能为空",
        ),
    ],
)
def test_validate_record_rejects_bad_gold_shape(gold: Any, needle: str) -> None:
    record = _first("fact")
    record["gold_chunks"] = gold
    problems = _validate(record)
    assert any(needle in item for item in problems), problems


def test_validate_record_deny_rules() -> None:
    record = _first("deny")
    record["deny_kb_id"] = None
    assert any("deny 样本必须有 deny_kb_id" in item for item in _validate(record))

    record = _first("deny")
    record["kb_id"] = KB
    assert any("deny 样本的 kb_id 必须是 null" in item for item in _validate(record))

    record = _first("deny")
    record["gold_chunks"] = _gold(_seed())
    assert any("deny 样本的 gold_chunks 必须为空" in item for item in _validate(record))

    record = _first("deny")
    record["expect_facts"] = ["上限 500 元"]
    assert any("deny 样本不该有 expect_facts" in item for item in _validate(record))


def test_validate_record_abstain_rules() -> None:
    record = _first("no_evidence")
    record["gold_chunks"] = _gold(_seed())
    assert any("abstain 样本的 gold_chunks 必须为空" in item for item in _validate(record))

    record = _first("no_evidence")
    record["expect_facts"] = ["上限 500 元"]
    assert any("abstain 样本不该有 expect_facts" in item for item in _validate(record))


# ────────────────────────────────────────────────────────────────────────────
# cmd_check：配额多一条 / 少一条都要抓住
# ────────────────────────────────────────────────────────────────────────────


def test_cmd_check_accepts_exactly_twenty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "set.jsonl"
    build_eval_set._write_jsonl(path, _records())

    code = build_eval_set.cmd_check(argparse.Namespace(out=str(path)))
    captured = capsys.readouterr()

    assert code == 0
    assert "[通过]" in captured.out
    assert "20 条" in captured.out
    assert captured.err == ""


def test_cmd_check_catches_one_extra_sample(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = _records()
    records.append(copy.deepcopy(records[0]))
    path = tmp_path / "set.jsonl"
    build_eval_set._write_jsonl(path, records)

    code = build_eval_set.cmd_check(argparse.Namespace(out=str(path)))
    captured = capsys.readouterr()

    assert code == 1
    assert "配额不符：fact 应为 6 条，实际 7 条" in captured.err
    assert "样本总数应为 20，实际 21" in captured.err
    assert "id 重复：['fact-01']" in captured.err


def test_cmd_check_catches_one_missing_sample(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = _records()
    records = [record for record in records if record["id"] != "fact-06"]
    path = tmp_path / "set.jsonl"
    build_eval_set._write_jsonl(path, records)

    code = build_eval_set.cmd_check(argparse.Namespace(out=str(path)))
    captured = capsys.readouterr()

    assert code == 1
    assert "配额不符：fact 应为 6 条，实际 5 条" in captured.err
    assert "样本总数应为 20，实际 19" in captured.err


def test_cmd_check_catches_a_category_outside_the_quota_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = _records()
    records[0]["category"] = "weird"
    path = tmp_path / "set.jsonl"
    build_eval_set._write_jsonl(path, records)

    code = build_eval_set.cmd_check(argparse.Namespace(out=str(path)))
    captured = capsys.readouterr()

    assert code == 1
    assert "出现配额表之外的 category：['weird']" in captured.err


def test_cmd_check_reports_a_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = build_eval_set.cmd_check(argparse.Namespace(out=str(tmp_path / "nope.jsonl")))
    captured = capsys.readouterr()

    assert code == 1
    assert "[失败] 找不到评测集" in captured.err


def test_real_dataset_artifact_passes_if_present(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """产物存在时必须过 --check；不存在就跳过，不让单测依赖 --build 是否跑过。"""
    path = EVAL_DIR / "eval_set_v0.1.jsonl"
    if not path.is_file():
        pytest.skip(f"{path} 不存在（还没跑 --build）")

    code = build_eval_set.cmd_check(argparse.Namespace(out=str(path)))
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert "[通过]" in captured.out


# ────────────────────────────────────────────────────────────────────────────
# resolve_gold：定位句匹配、多命中取最小、快照一致性、事实子串
# ────────────────────────────────────────────────────────────────────────────


def test_resolve_gold_matches_across_line_breaks(capsys: pytest.CaptureFixture[str]) -> None:
    content = "第三章 票据管理\n每月至少\n对库存票据进行盘点"
    gold = build_eval_set.resolve_gold(_seed(), _snapshot_of_doc(), [_chunk(3, content)])

    assert gold == {
        "corpus": "corpus-a",
        "document_id": "doc-a",
        "chunk_ordinal": 3,
        "content_sha1_8": build_eval_set.sha1_8(content),
    }
    assert capsys.readouterr().out == ""


def test_resolve_gold_takes_the_smallest_ordinal_on_overlap(
    capsys: pytest.CaptureFixture[str],
) -> None:
    locator = "每月至少对库存票据"
    chunks = [_chunk(29, f"{locator}，季度末加盘"), _chunk(5, f"开头 {locator} 结尾")]

    gold = build_eval_set.resolve_gold(_seed(), _snapshot_of_doc(), chunks)

    assert gold["chunk_ordinal"] == 5
    out = capsys.readouterr().out
    assert "定位句命中 2 片 [5, 29]，取最小 5" in out


def test_resolve_gold_raises_on_zero_hits() -> None:
    with pytest.raises(build_eval_set.BuildError) as excinfo:
        build_eval_set.resolve_gold(_seed(), _snapshot_of_doc(), [_chunk(0, "完全不相干的正文")])
    assert "0 命中" in str(excinfo.value)


def test_resolve_gold_requires_a_locator() -> None:
    with pytest.raises(build_eval_set.BuildError) as excinfo:
        build_eval_set.resolve_gold(_seed(locator=None), _snapshot_of_doc(), [_chunk(0, "正文")])
    assert "没有定位句" in str(excinfo.value)


def test_resolve_gold_rejects_a_stale_snapshot() -> None:
    # 取数期间文档被重切：gold 会指向已退休的 build，Recall 无故为 0。
    content = "每月至少对库存票据进行盘点"
    chunk = _chunk(0, content, index_version=1)
    with pytest.raises(build_eval_set.BuildError) as excinfo:
        build_eval_set.resolve_gold(_seed(), _snapshot_of_doc(), [chunk])
    message = str(excinfo.value)
    assert "现行活动指针不一致" in message
    assert "index_version 切片=1 现行=2" in message


def test_resolve_gold_ignores_missing_snapshot_fields() -> None:
    # 任一侧缺失时无法判定，不许当成不一致（否则没有 build_id 的切片永远回填不了）。
    content = "每月至少对库存票据进行盘点"
    chunk = {"chunk_ordinal": 0, "content": content}
    gold = build_eval_set.resolve_gold(_seed(), _snapshot_of_doc(), [chunk])
    assert gold["chunk_ordinal"] == 0


def test_resolve_gold_raises_when_a_fact_exists_nowhere() -> None:
    content = "每月至少对库存票据进行盘点"
    with pytest.raises(build_eval_set.BuildError) as excinfo:
        build_eval_set.resolve_gold(
            _seed(expect_facts=("根本不存在的条款",)), _snapshot_of_doc(), [_chunk(0, content)]
        )
    assert "写进去就是错的" in str(excinfo.value)


def test_resolve_gold_warns_when_a_fact_sits_in_the_neighbour_chunk(
    capsys: pytest.CaptureFixture[str],
) -> None:
    locator = "每月至少对库存票据"
    chunks = [
        _chunk(0, f"{locator}进行盘点"),
        _chunk(1, "单张票据上限 500 元"),
    ]

    gold = build_eval_set.resolve_gold(
        _seed(expect_facts=("上限 500 元",)), _snapshot_of_doc(), chunks
    )

    assert gold["chunk_ordinal"] == 0
    out = capsys.readouterr().out
    assert "[注意]" in out
    assert "不在 gold 片内" in out


# ────────────────────────────────────────────────────────────────────────────
# make_configs.build_snapshot：只留计划 §3.5 的字段
# ────────────────────────────────────────────────────────────────────────────


def test_build_snapshot_keeps_only_the_plan_fields() -> None:
    raw = _raw_setup()
    raw["documents"][0]["extra"] = "drop-me"
    snapshot = make_configs.build_snapshot(raw)

    assert snapshot == {
        "workspace_id": WS,
        "kb_id": KB,
        "documents": [
            {
                "document_id": "doc-a",
                "active_document_version_id": "ver-2",
                "active_build_id": "build-2",
                "active_index_version": 2,
                "chunks": 12,
            }
        ],
    }
    assert "extra" not in snapshot["documents"][0]


@pytest.mark.parametrize("documents", [None, [], "x"])
def test_build_snapshot_requires_a_non_empty_document_list(documents: Any) -> None:
    raw = _raw_setup()
    raw["documents"] = documents
    with pytest.raises(make_configs.EnvError) as excinfo:
        make_configs.build_snapshot(raw)
    assert "documents 必须是非空列表" in str(excinfo.value)


def test_build_snapshot_names_the_missing_document_field() -> None:
    raw = _raw_setup()
    del raw["documents"][0]["chunks"]
    with pytest.raises(make_configs.EnvError) as excinfo:
        make_configs.build_snapshot(raw)
    message = str(excinfo.value)
    assert "documents[0].chunks" in message
    assert "先重跑" in message


def test_build_snapshot_requires_the_top_level_ids() -> None:
    raw = _raw_setup()
    raw["workspace_id"] = "   "
    with pytest.raises(make_configs.EnvError) as excinfo:
        make_configs.build_snapshot(raw)
    assert "缺少 workspace_id" in str(excinfo.value)


# ────────────────────────────────────────────────────────────────────────────
# make_configs.build_config / render_config：15 字段与公平性
# ────────────────────────────────────────────────────────────────────────────


def test_build_config_has_the_plan_field_order_and_values() -> None:
    cfg = _config("baseline")

    assert list(cfg) == [
        "experiment_id",
        "app_commit",
        "prompt_version",
        "eval_dataset",
        "snapshot",
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
    ]
    assert cfg["experiment_id"] == make_configs.EXPERIMENT_ID
    assert cfg["app_commit"] == HEAD
    assert cfg["retrieval_strategy"] == "baseline"
    assert cfg["eval_dataset"] == make_configs.DATASET_RELATIVE
    assert cfg["query_top_k"] == make_configs.QUERY_TOP_K == 5
    assert cfg["chat_top_k"] == make_configs.CHAT_TOP_K == 6
    assert cfg["reranker"] is None
    assert cfg["snapshot"] == _snapshot()


def test_generated_pair_differs_only_in_retrieval_strategy(tmp_path: Path) -> None:
    """生成器产出的 A/B 必须恰好一个主因素不同——否则 compare-config 会打回。"""
    left = _config("baseline")
    right = _config("candidate")

    assert run_eval.build_diff(left, right) == [("retrieval_strategy", "baseline", "candidate")]

    ok, lines = run_eval.compare_configs(
        _write_yaml(tmp_path / "base.yaml", left),
        _write_yaml(tmp_path / "cand.yaml", right),
    )
    assert ok is True
    assert "retrieval_strategy" in lines[0]


def test_render_config_writes_four_header_lines_and_round_trips(tmp_path: Path) -> None:
    cfg = _config("baseline")
    text = make_configs.render_config(cfg, note="基线：retrieval_strategy=baseline")
    lines = text.splitlines()

    assert lines[0] == "# 由 eval/make_configs.py 生成，勿手改；改字段请改生成器后重跑。"
    assert lines[1].startswith("# 生成时间：")
    assert (
        lines[2] == "# 来源：.tmp_ref/eval_setup.json（snapshot）、pyknora.core.settings、git HEAD"
    )
    assert lines[3] == "# 基线：retrieval_strategy=baseline"
    assert yaml.safe_load(text) == cfg


def test_prompt_version_from_source_reads_the_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "answer.py"
    source.write_text('PROMPT_VERSION = "pyknora-rag-v2"\n', encoding="utf-8")
    monkeypatch.setattr(make_configs, "ANSWER_PATH", source)

    assert make_configs._prompt_version_from_source() == "pyknora-rag-v2"


def test_prompt_version_from_source_reports_a_missing_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "answer.py"
    source.write_text("# 没有 PROMPT_VERSION\n", encoding="utf-8")
    monkeypatch.setattr(make_configs, "ANSWER_PATH", source)

    with pytest.raises(make_configs.EnvError) as excinfo:
        make_configs._prompt_version_from_source()
    assert "找不到" in str(excinfo.value)


def test_prompt_version_from_source_reports_a_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(make_configs, "ANSWER_PATH", tmp_path / "nope.py")
    with pytest.raises(make_configs.EnvError) as excinfo:
        make_configs._prompt_version_from_source()
    assert "不存在" in str(excinfo.value)


# ────────────────────────────────────────────────────────────────────────────
# make_configs.inspect_dataset：评测集与 snapshot 对账
# ────────────────────────────────────────────────────────────────────────────


def _dataset_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": "fact-01",
            "category": "fact",
            "expect": "answer",
            "kb_id": KB,
            "workspace_id": WS,
            "gold_chunks": [{"document_id": "doc-a"}],
        },
        {
            "id": "deny-01",
            "category": "deny",
            "expect": "deny",
            "kb_id": None,
            "workspace_id": WS,
            "deny_kb_id": DENY_KB,
            "gold_chunks": [],
        },
    ]


def _dataset_cfg(directory: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path = directory / "set.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return {
        "eval_dataset": "set.jsonl",  # 相对路径：要能被解析到配置文件所在目录
        "snapshot": {
            "workspace_id": WS,
            "kb_id": KB,
            "documents": [{"document_id": "doc-a"}],
        },
    }


def test_inspect_dataset_passes_on_a_consistent_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _dataset_cfg(tmp_path, _dataset_rows())
    problems = make_configs.inspect_dataset(run_eval, cfg, tmp_path / "base.yaml")
    assert problems == []
    assert "2 条" in capsys.readouterr().out


def test_inspect_dataset_flags_a_gold_document_outside_the_snapshot(
    tmp_path: Path,
) -> None:
    rows = _dataset_rows()
    rows[0]["gold_chunks"] = [{"document_id": "doc-b"}]
    problems = make_configs.inspect_dataset(
        run_eval, _dataset_cfg(tmp_path, rows), tmp_path / "base.yaml"
    )
    assert "gold_chunks 指向 snapshot 之外的文档：['doc-b']" in problems


def test_inspect_dataset_flags_a_kb_mismatch_for_answer_samples(
    tmp_path: Path,
) -> None:
    rows = _dataset_rows()
    rows[0]["kb_id"] = "kb-other"
    problems = make_configs.inspect_dataset(
        run_eval, _dataset_cfg(tmp_path, rows), tmp_path / "base.yaml"
    )
    assert "样本 fact-01 的 kb_id 与 snapshot.kb_id 不一致" in problems


def test_inspect_dataset_flags_a_workspace_mismatch(tmp_path: Path) -> None:
    rows = _dataset_rows()
    rows[0]["workspace_id"] = "22222222-2222-4222-8222-222222222222"
    problems = make_configs.inspect_dataset(
        run_eval, _dataset_cfg(tmp_path, rows), tmp_path / "base.yaml"
    )
    assert "样本 fact-01 的 workspace_id 与 snapshot.workspace_id 不一致" in problems


def test_inspect_dataset_reports_an_unresolvable_dataset(tmp_path: Path) -> None:
    cfg = _dataset_cfg(tmp_path, _dataset_rows())
    cfg["eval_dataset"] = "missing.jsonl"
    problems = make_configs.inspect_dataset(run_eval, cfg, tmp_path / "base.yaml")
    assert problems and "eval_dataset 解析不到文件" in problems[0]


def test_inspect_dataset_reports_broken_jsonl(tmp_path: Path) -> None:
    (tmp_path / "set.jsonl").write_text("{not json\n", encoding="utf-8")
    cfg = {
        "eval_dataset": "set.jsonl",
        "snapshot": {"workspace_id": WS, "kb_id": KB, "documents": []},
    }
    problems = make_configs.inspect_dataset(run_eval, cfg, tmp_path / "base.yaml")
    assert problems and "不是合法 jsonl" in problems[0]
