"""tsvector 写入口径的唯一性守卫（结构测试，不需要数据库）。

来由：中文 FTS 修好之后，"写 tsvector"的位置一共四处——
`workers/ingest_worker.py`、`services/migration/reindex.py`、
`services/migration/snapshot.py`（快照恢复）、`scripts/seed_for_m3.py`。
它们必须全部用 `to_tsvector('simple', pyknora_cjk_bigram(content))`。

为什么值得一条结构测试：只要有一处退回**裸 content** 的旧口径，中文短词就
**在那条路径上静默搜不到**——不报错、不抛异常，只是返回 0 行。"重建索引后
中文又搜不到了"这种事故，排查成本远高于这条测试的维护成本。

判定规则（故意写得窄，避免把正文里的反例当代码）：
逐行扫描 `src/ scripts/ migrations/ tests/`，跳过注释行与文档字符串行；
若某行既调用了 `to_tsvector(`，又在该调用之外提到 `tsvector`（即它确实在
给这一列赋值，而不是在注释里举例），则要求同一行出现 `pyknora_cjk_bigram`。

已知盲区（写清楚，不假装覆盖）：跨行的 `to_tsvector(` 续行（例如测试夹具里
把调用写在下一行）不会被本测试判到。这类位置由 `tests/integration/
test_migration_tsvector.py` 用真库行为兜底——那条路径若用错口径，中文短词
就搜不到，用例会红。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("src", "scripts", "migrations", "tests")

SUFFIXES = (".py", ".sql")

_CALL = re.compile(r"to_tsvector\s*\([^)]*\)?", re.IGNORECASE)
_WORD = re.compile(r"\btsvector\b", re.IGNORECASE)
_BIGRAM = "pyknora_cjk_bigram"

#: 本文件自己会在文档字符串里引用"错误写法"当反例，不能把自己算进去。
_SELF = "tests/unit/test_tsvector_encoding_sites.py"


def _is_comment(stripped: str) -> bool:
    return stripped.startswith(("#", "--", '"""', "'''", "*"))


def _write_sites() -> list[tuple[str, int, str]]:
    """返回真正在给 `tsvector` 列赋值的行：[(相对路径, 行号, 行原文)]。"""
    sites: list[tuple[str, int, str]] = []
    for directory in SCAN_DIRS:
        root = REPO_ROOT / directory
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in SUFFIXES or not path.is_file():
                continue
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative == _SELF:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if _is_comment(stripped) or not _CALL.search(stripped):
                    continue
                # 把调用本身剥掉后还提到 tsvector 的，才是赋值行；否则是正文举例。
                outside = _CALL.sub("", stripped)
                if not _WORD.search(outside):
                    continue
                sites.append((relative, number, stripped))
    return sites


def test_every_tsvector_write_goes_through_cjk_bigram() -> None:
    offenders = [item for item in _write_sites() if _BIGRAM not in item[2]]

    assert offenders == [], (
        "以下位置在写 tsvector 时没有走 pyknora_cjk_bigram，"
        "中文短词会在该路径上静默搜不到：\n"
        + "\n".join(f"  {path}:{line}  {text}" for path, line, text in offenders)
    )


def test_write_sites_are_not_empty() -> None:
    """防"测试自己假绿"：扫描规则失效时，上面的全绿毫无意义。"""
    paths = {path for path, _line, _text in _write_sites()}

    assert "src/pyknora/workers/ingest_worker.py" in paths
    assert "src/pyknora/services/migration/reindex.py" in paths
    assert "src/pyknora/services/migration/snapshot.py" in paths
    assert "scripts/seed_for_m3.py" in paths
    assert len(paths) >= 4
