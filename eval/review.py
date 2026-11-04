"""人工复核载体（P4）：三态写入 `review.jsonl`，并把结果回填进 `raw.jsonl` 的 `review` 键。

## 三态语义（硬口径，别读错）

| 输入 | 落盘值 | 含义 |
| --- | --- | --- |
| `pass` | `true` | 人工确认这一项通过 |
| `fail` | `false` | 人工确认这一项失败——**唯一**算失败的取值 |
| `unknown` | `null` | 未评 / 评不了 / 证据不足 |

`null` **不是** `false`，更**不是**通过：它表示"这一格没有人下过结论"。把 `unknown`
写成 `false`，会把"没评"混进"评过且失败"；把 `unknown` 当通过，则是凭空发合格证。
两种做法都会让 `null_ratio` 的硬口径失效——本项目 P4 规定 **"三项全 `null` 的条数 /
总条数 > 0.20 → 整体判 FAIL"**（不是"有条件通过"），评分缺失一律阻断、不当作通过。

## 两个文件各自的角色

- `eval/results/<run>/review.jsonl`：**账本的真相**。每行一个 case，字段
  `case_id,fact_pass,citation_pass,security_pass,note`。同一 case 后写覆盖前值：
  先读全量、按 `case_id` 合并、再重写（保持首次出现的顺序，新 case 追加到末尾）。
- `eval/results/<run>/raw.jsonl`：**只回填 `review` 一个键**。整行 `json.loads` +
  `json.dumps` 重写会让未复核的行也跟着变格式（diff 爆炸，`content` 里的转义也跟着变），
  所以这里定位顶层 `"review"` 值所在的字节区间、只替换那一段：行数、行序、行尾
  （CRLF/LF）与其余字节全部原样；回填后逐行校验"除 `review` 外解析结果全等"，
  不一致就抛错而不是写盘。

一条 raw 记录里 `case_id` 缺失时回落到 `sample_id`（DAG 计划 §2.4：`case_id` 的初值
就是现有 `sample_id`，不重新编号），这样 N4 落地前后都能跑。

## 用法

    # 看全量：每个 case 的三态 + null 占比汇总
    uv run --frozen python eval/review.py --run eval/results/baseline-20261030 --list

    # 写一个 case（只给想改的项；没给的项保持原值）
    uv run --frozen python eval/review.py --run eval/results/baseline-20261030 \\
        --case fact-01 --fact pass --citation unknown --security pass --note "注册资本在切片 0"

    # 只给 --case 不给三态：只读展示该 case 当前的三态，不写任何文件
    uv run --frozen python eval/review.py --run eval/results/baseline-20261030 --case fact-01

`--run` 既接受结果目录的路径，也接受 `eval/results/` 下的目录名（如 `baseline-20261030`）。

## 退出码

- `0`：成功；
- `2`：参数/目录/数据错误——`--run` 不存在、`--case` 不在 raw 里、`review` 值非法、
  `review.jsonl` 或 `raw.jsonl` 内容损坏。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: 命令行三种输入，顺序即文档顺序。
STATE_CHOICES: tuple[str, ...] = ("pass", "fail", "unknown")

#: 三态到 JSON 值的**唯一**映射：`unknown` 落成 `None`（`json.dumps` 出 `null`）。
#: 不要往里加 `"null"` 之类的别名——多一条入口就多一种"这条是评过还是没评"的歧义。
STATE_TO_BOOL: dict[str, bool | None] = {"pass": True, "fail": False, "unknown": None}

#: `review` 对象的三个键，顺序即落盘顺序。
REVIEW_KEYS: tuple[str, ...] = ("fact_pass", "citation_pass", "security_pass")

#: 每个键对应的命令行开关，报错时原文回显。
STATE_FLAG: dict[str, str] = {
    "fact_pass": "--fact",
    "citation_pass": "--citation",
    "security_pass": "--security",
}

#: P4 硬口径：三项全 null 的占比超过它，整体判 FAIL。
NULL_RATIO_LIMIT = 0.20

LEDGER_NAME = "review.jsonl"
RAW_NAME = "raw.jsonl"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


class ReviewError(RuntimeError):
    """输入/数据不合法。命令行入口统一翻成 exit 2，绝不当成"写成功了"。"""


@dataclass(frozen=True)
class CaseView:
    """一个 case 的当前三态；`source` 说明这条结论来自哪个文件。"""

    case_id: str
    review: dict[str, Any]
    note: str | None
    source: str


# --------------------------------------------------------------------------- 三态


def empty_review() -> dict[str, Any]:
    """三项全 `null` 的空复核——**未评**，既不通过也不失败。"""
    return {key: None for key in REVIEW_KEYS}


def tristate(value: Any, where: str) -> bool | None:
    """严格只收 `true` / `false` / `null`，别的一律报错。

    宁可炸也不静默归一：`"pass"`、`1`、`"true"` 这类手写值一旦被悄悄当成 `null`，
    复核人以为自己记了，`null_ratio` 却照旧计入缺失，最后一查全是"没记上"。
    """
    if value is None or value is True or value is False:
        return value
    raise ReviewError(f"{where} 的 review 值非法：{value!r}；只允许 true/false/null")


def normalize_review(payload: Any, where: str) -> dict[str, Any]:
    """从任意来源的 `review` 取三键；缺键按 `null`（缺 = 没评）。"""
    if payload is None:
        return empty_review()
    if not isinstance(payload, dict):
        raise ReviewError(f"{where} 的 review 必须是对象，实际 {type(payload).__name__}")
    return {key: tristate(payload.get(key), f"{where} 的 {key}") for key in REVIEW_KEYS}


def _note_of(payload: Mapping[str, Any], where: str) -> str | None:
    note = payload.get("note")
    if note is None or isinstance(note, str):
        return note
    raise ReviewError(f"{where} 的 note 必须是字符串或 null，实际 {type(note).__name__}")


# --------------------------------------------------------------------------- 账本


def load_ledger(path: Path) -> dict[str, dict[str, Any]]:
    """读 `review.jsonl`，返回 `case_id -> {三态 + note}`，保持文件内的先后顺序。

    同一 case 出现多次时后者覆盖前者（文件是"后写覆盖"的账本，重复行只说明历史遗留）。
    """
    entries: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return entries
    text = path.read_bytes().decode("utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        where = f"{path}:{lineno}"
        payload = _loads(line, where)
        case_id = payload.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ReviewError(f"{where} 缺少非空 case_id")
        entries[case_id] = {
            **normalize_review(payload, where),
            "note": _note_of(payload, where),
        }
    return entries


def save_ledger(path: Path, entries: Mapping[str, dict[str, Any]]) -> None:
    """整文件重写，顺序沿用 dict 的插入顺序；先写临时文件再替换，不留半截文件。"""
    lines = []
    for case_id, entry in entries.items():
        payload = {"case_id": case_id, **{key: entry[key] for key in REVIEW_KEYS}}
        payload["note"] = entry["note"]
        lines.append(json.dumps(payload, ensure_ascii=False) + "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8", newline="\n")
    tmp.replace(path)


def ensure_ledger(path: Path) -> None:
    """账本不存在就先建空文件（P4 要求：`--list` 也要能跑）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


# --------------------------------------------------------------------------- raw


def _loads(line: str, where: str) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ReviewError(f"{where} 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ReviewError(f"{where} 的顶层必须是对象，实际 {type(payload).__name__}")
    return payload


def case_id_of(record: Mapping[str, Any], where: str) -> str:
    """`case_id` 优先，缺失回落到 `sample_id`（N4 落地前后同一条记录指向同一个 case）。"""
    for key in ("case_id", "sample_id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    raise ReviewError(f"{where} 既没有 case_id 也没有 sample_id")


def read_raw_lines(path: Path) -> list[str]:
    """按字节读、保留每行原样的行尾（CRLF/LF）——回填时行尾必须一个字节都不动。

    `raw.jsonl` 不存在就报错而不是当空文件：一个只有 `report.md` 的目录不是"还没复核
    的 run"，是 `--run` 指错了。悄悄返回空列表会让 `--list` 打出 `null_ratio = n/a`
    然后 exit 0，看起来像"这一轮没有未复核项"。
    """
    if not path.is_file():
        raise ReviewError(f"{path} 不存在：run 目录里没有 raw.jsonl，--run 大概指错了")
    return path.read_bytes().decode("utf-8").splitlines(keepends=True)


def _string_end(text: str, start: int) -> int:
    """`text[start] == '"'`，返回闭合引号之后的下标。"""
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index + 1
        index += 1
    raise ReviewError("JSON 行里的字符串没有闭合")


def _value_end(text: str, start: int) -> int:
    """从一个值的首字符出发，返回该值的结束下标（不含）。"""
    char = text[start]
    if char == '"':
        return _string_end(text, start)
    if char in "{[":
        depth = 0
        index = start
        while index < len(text):
            current = text[index]
            if current == '"':
                index = _string_end(text, index)
                continue
            if current in "{[":
                depth += 1
            elif current in "}]":
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        raise ReviewError("JSON 行里的对象/数组没有闭合")
    index = start
    while index < len(text) and text[index] not in ",}] \t\r\n":
        index += 1
    return index


def top_level_members(text: str) -> dict[str, tuple[int, int]]:
    """扫出顶层对象的每个成员，返回 `键 -> (值起, 值止)`。

    只认深度 1 的键：`results` 里那些嵌套对象也带 `"review"` 的话，不能被误认成
    记录自己的 `review`——认错了就会把检索结果的字段改成复核字段。
    """
    members: dict[str, tuple[int, int]] = {}
    depth = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == '"':
            end = _string_end(text, index)
            if depth == 1:
                probe = end
                while probe < length and text[probe] in " \t\r\n":
                    probe += 1
                if probe < length and text[probe] == ":":
                    value_start = probe + 1
                    while value_start < length and text[value_start] in " \t\r\n":
                        value_start += 1
                    if value_start >= length:
                        raise ReviewError("JSON 行的键没有取值")
                    key = json.loads(text[index:end])
                    members.setdefault(key, (value_start, _value_end(text, value_start)))
            index = end
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth < 0:
                raise ReviewError("JSON 行的括号不配对")
        index += 1
    return members


def patch_review_text(line: str, review: Mapping[str, Any]) -> str:
    """只替换/插入 `review` 值，其余字节原样返回（含行尾）。"""
    body = line.rstrip("\r\n")
    terminator = line[len(body) :]
    payload = json.dumps({key: review[key] for key in REVIEW_KEYS}, ensure_ascii=False)
    members = top_level_members(body)
    if "review" in members:
        start, end = members["review"]
        patched = body[:start] + payload + body[end:]
    else:
        stripped = body.rstrip()
        trailing = body[len(stripped) :]
        if not stripped.endswith("}"):
            raise ReviewError("raw 行不以 } 结尾，无法插入 review")
        close = len(stripped) - 1
        patched = stripped[:close] + ', "review": ' + payload + stripped[close:] + trailing
    return patched + terminator


def _assert_only_review_changed(old_line: str, new_line: str, review: Mapping[str, Any]) -> None:
    """回填后的自证：行尾不变、行数不变、除 `review` 外解析结果全等。"""
    old_body = old_line.rstrip("\r\n")
    new_body = new_line.rstrip("\r\n")
    if old_line[len(old_body) :] != new_line[len(new_body) :]:
        raise ReviewError("内部不变式被打破：回填改动了行尾")
    before = _loads(old_body, "回填前")
    after = _loads(new_body, "回填后")
    if after.get("review") != dict(review):
        raise ReviewError("内部不变式被打破：回填后的 review 与目标不一致")
    before.pop("review", None)
    after.pop("review", None)
    if before != after:
        raise ReviewError("内部不变式被打破：回填改动了 review 之外的字段")


def backfill_raw(path: Path, reviews: Mapping[str, Mapping[str, Any]]) -> tuple[int, int]:
    """把 `case_id -> review` 回填进 raw.jsonl，返回 `(改动行数, 总行数)`。

    只改 `review` 一个键；没被点名的行原样搬运，整文件只在确有改动时才落盘。
    """
    if not path.exists():
        raise ReviewError(f"raw.jsonl 不存在，无从回填：{path}")
    lines = read_raw_lines(path)
    new_lines: list[str] = []
    changed = 0
    for lineno, line in enumerate(lines, 1):
        where = f"{path}:{lineno}"
        if not line.strip():
            new_lines.append(line)
            continue
        record = _loads(line.rstrip("\r\n"), where)
        case_id = case_id_of(record, where)
        target = reviews.get(case_id)
        if target is None:
            new_lines.append(line)
            continue
        current = normalize_review(record.get("review"), f"{where} 的 review")
        if "review" in record and current == dict(target):
            new_lines.append(line)
            continue
        patched = patch_review_text(line, target)
        _assert_only_review_changed(line, patched, target)
        new_lines.append(patched)
        changed += 1
    if len(new_lines) != len(lines):
        raise ReviewError(f"内部不变式被打破：行数 {len(lines)} -> {len(new_lines)}")
    if changed:
        path.write_bytes("".join(new_lines).encode("utf-8"))
    return changed, len(lines)


# --------------------------------------------------------------------------- 汇总


def raw_case_ids(path: Path) -> list[str]:
    """raw 里出现过的 case（按文件顺序去重）——报「case 不存在」时用来列现有值。"""
    case_ids: list[str] = []
    for lineno, line in enumerate(read_raw_lines(path), 1):
        if not line.strip():
            continue
        where = f"{path}:{lineno}"
        case_id = case_id_of(_loads(line.rstrip("\r\n"), where), where)
        if case_id not in case_ids:
            case_ids.append(case_id)
    return case_ids


def collect_cases(run_dir: Path) -> tuple[list[CaseView], int, int]:
    """合并账本与 raw：返回 `(每个 case 的视图, raw 行数, 账本条数)`。

    `review.jsonl` 是人工结论的真相，优先于 raw 里的副本；raw 单独出现的 case
    （从没复核过）按三项全 `null` 计。
    """
    ledger = load_ledger(run_dir / LEDGER_NAME)
    raw_path = run_dir / RAW_NAME
    order: list[str] = []
    raw_reviews: dict[str, dict[str, Any]] = {}
    raw_lines = 0
    for lineno, line in enumerate(read_raw_lines(raw_path), 1):
        where = f"{raw_path}:{lineno}"
        if not line.strip():
            continue
        raw_lines += 1
        record = _loads(line.rstrip("\r\n"), where)
        case_id = case_id_of(record, where)
        if case_id in raw_reviews:
            continue
        raw_reviews[case_id] = normalize_review(record.get("review"), f"{where} 的 review")
        order.append(case_id)
    for case_id in ledger:
        if case_id not in raw_reviews:
            order.append(case_id)
    views = []
    for case_id in order:
        if case_id in ledger:
            entry = ledger[case_id]
            views.append(
                CaseView(
                    case_id, {key: entry[key] for key in REVIEW_KEYS}, entry["note"], LEDGER_NAME
                )
            )
        else:
            views.append(CaseView(case_id, raw_reviews[case_id], None, RAW_NAME))
    return views, raw_lines, len(ledger)


def null_ratio_of(views: Sequence[CaseView]) -> tuple[int, int, float | None]:
    """三项全 `null` 的条数、总条数、占比；总数为 0 时占比是 `None`（不是 0）。"""
    total = len(views)
    all_null = sum(1 for view in views if all(view.review[key] is None for key in REVIEW_KEYS))
    return all_null, total, (all_null / total if total else None)


def state_label(value: Any) -> str:
    """`true/false/null` 回显成 `pass/fail/unknown`，与命令行输入同一套词。"""
    if value is True:
        return "pass"
    if value is False:
        return "fail"
    return "unknown"


def null_ratio_line(views: Sequence[CaseView]) -> str:
    all_null, total, ratio = null_ratio_of(views)
    if ratio is None:
        return "[review] null_ratio = n/a（没有可复核的 case）-> UNKNOWN"
    verdict = "FAIL" if ratio > NULL_RATIO_LIMIT else "PASS"
    return (
        f"[review] null_ratio = {ratio:.4f}"
        f"（三项全 null {all_null}/{total}）-> {verdict}（阈值 {NULL_RATIO_LIMIT:.2f}）"
    )


def case_line(view: CaseView) -> str:
    parts = [f"{view.case_id:<14}"]
    parts += [f"{key}={state_label(view.review[key]):<7}" for key in REVIEW_KEYS]
    line = " ".join(parts).rstrip()
    if view.note:
        note = view.note if len(view.note) <= 40 else view.note[:39] + "..."
        line = f"{line}  # {note}"
    return line


def summary_line(views: Sequence[CaseView]) -> str:
    chunks = []
    for key in REVIEW_KEYS:
        counts = {"pass": 0, "fail": 0, "unknown": 0}
        for view in views:
            counts[state_label(view.review[key])] += 1
        chunks.append(
            f"{key}=pass:{counts['pass']}/fail:{counts['fail']}/unknown:{counts['unknown']}"
        )
    return "[review] 三态合计 " + " | ".join(chunks)


# --------------------------------------------------------------------------- 命令


def resolve_run(value: str) -> Path:
    """`--run` 既接受目录路径，也接受 `eval/results/` 下的目录名。"""
    path = Path(value)
    if path.is_dir():
        return path
    fallback = RESULTS_DIR / value
    if fallback.is_dir():
        return fallback
    raise ReviewError(f"--run 指向的目录不存在：{value}（也试过 {fallback}）")


def apply_updates(
    ledger: dict[str, dict[str, Any]],
    case_id: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """按 `case_id` 合并三态：没给的键保持原值（新 case 从全 `null` 起步）。"""
    entry = ledger.get(case_id)
    if entry is None:
        entry = {**empty_review(), "note": None}
        ledger[case_id] = entry
    for key in REVIEW_KEYS:
        flag = getattr(args, key.split("_")[0])
        if flag is not None:
            entry[key] = STATE_TO_BOOL[flag]
    if args.note is not None:
        entry["note"] = args.note
    return {key: entry[key] for key in REVIEW_KEYS}


def cmd_list(run_dir: Path) -> int:
    views, raw_lines, ledger_count = collect_cases(run_dir)
    raw_path = run_dir / RAW_NAME
    raw_desc = f"{raw_lines} 行"
    print(f"[review] run      = {run_dir}")
    print(f"[review] 账本     = {run_dir / LEDGER_NAME}（{ledger_count} 条）")
    print(f"[review] raw      = {raw_path}（{raw_desc}，去重 {len(views)} 个 case）")
    if not views:
        print("[review] raw.jsonl 里没有记录（0 行），没有 case 可复核")
        return 0
    print()
    for view in views:
        print(case_line(view))
    print()
    print(summary_line(views))
    print(null_ratio_line(views))
    return 0


def cmd_set(run_dir: Path, args: argparse.Namespace) -> int:
    ledger_path = run_dir / LEDGER_NAME
    ledger = load_ledger(ledger_path)
    raw_path = run_dir / RAW_NAME
    case_id = args.case
    known = raw_case_ids(raw_path)
    if case_id not in known:
        sample = "、".join(known[:5])
        raise ReviewError(
            f"case {case_id!r} 不在 {raw_path} 里（现有 {len(known)} 个，前几个：{sample}）"
        )
    has_updates = any(getattr(args, key.split("_")[0]) is not None for key in REVIEW_KEYS)
    if not has_updates and args.note is None:
        views, _, _ = collect_cases(run_dir)
        for view in views:
            if view.case_id == case_id:
                print(case_line(view))
                print(null_ratio_line(views))
                return 0
        print(f"[review] case {case_id!r} 还没有任何复核记录（三项全 null）")
        print(null_ratio_line(views))
        return 0
    review = apply_updates(ledger, case_id, args)
    save_ledger(ledger_path, ledger)
    print(f"[review] 已合并 case={case_id}（账本 {len(ledger)} 条，顺序稳定）")
    if raw_path.exists():
        changed, total = backfill_raw(raw_path, {case_id: review})
        print(f"[review] raw.jsonl 回填 {changed} 行（行数 {total} -> {total}，未变）")
    else:
        print(f"[review] raw.jsonl 不存在，跳过回填：{raw_path}")
    print(
        f"[review] {case_id}: "
        + " ".join(f"{key}={state_label(review[key])}" for key in REVIEW_KEYS)
    )
    views, _, _ = collect_cases(run_dir)
    print(null_ratio_line(views))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eval/review.py",
        description="人工复核三态（pass/fail/unknown -> true/false/null）。unknown 落 null，"
        "不代表失败、更不代表通过。",
    )
    parser.add_argument(
        "--run",
        required=True,
        metavar="DIR",
        help="结果目录（eval/results/<run>），也可直接给目录名",
    )
    parser.add_argument("--case", metavar="CASE_ID", help="要写复盘的 case_id")
    parser.add_argument("--fact", choices=STATE_CHOICES, help="事实正确性：pass/fail/unknown")
    parser.add_argument("--citation", choices=STATE_CHOICES, help="引用可核验：pass/fail/unknown")
    parser.add_argument("--security", choices=STATE_CHOICES, help="安全性：pass/fail/unknown")
    parser.add_argument("--note", metavar="TEXT", help="备注（最多随账本一起落盘）")
    parser.add_argument(
        "--list", action="store_true", dest="list_mode", help="打印全部 case 的三态与 null 占比"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_mode and args.case:
        parser.error("--list 与 --case 不能同时用")
    if not args.list_mode and not args.case:
        parser.error("要么 --list，要么 --case <case_id>")
    try:
        run_dir = resolve_run(args.run)
        ensure_ledger(run_dir / LEDGER_NAME)
        if args.list_mode:
            return cmd_list(run_dir)
        return cmd_set(run_dir, args)
    except ReviewError as exc:
        print(f"[review] 错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
