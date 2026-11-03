"""N6a-2：解析层的 `locator` / `is_tabular` 接线位 + 表格块的 I6 受控例外。

口径来自 `docs/adr/0008_Document_Parsing_Contract.md` 的 D2/D3，只验解析层与切分层：

- `Block` 新增 `locator` / `is_tabular`，四个解析器**不崩**且默认不填 `locator`
  （真正回填是后续 S2/S4 节点的事，所以断言"为 None"会随那时改动而更新）；
- 表格块：不进 `full_text`、`char_*` 是块内偏移、`page_at`/`heading_at` 不求值、
  `locator_ref` 写 `locator` 的紧凑串；
- 普通块（`is_tabular=False`）行为**逐字节不变**——本文件用"加了一个表格块前后，
  普通块的每个字段都相同"的对照用例证明，`tests/unit/test_parser_splitter.py`
  的既有用例继续兜底。

不碰数据层（`models/tables.py`、`migrations/`）与 `db/repos/reads.py`。
"""

from __future__ import annotations

import io

import pytest

from pyknora.schemas.locator import (
    PageLocator,
    ParaLocator,
    RegionLocator,
    SheetLocator,
    SlideLocator,
    TableLocator,
)
from pyknora.services.ingest.parser import Block, ParsedText, parse
from pyknora.services.ingest.splitter import chunk_parsed, compact_locator

# 表格块用的工作表 locator：`B2:C4` 是 `_CELLS_PATTERN` 允许的形态。
SHEET_LOCATOR = SheetLocator(sheet="Sheet1", cells="B2:C4")


def _tabular(text: str = "A1:B2\nB2:C4") -> Block:
    return Block(text=text, is_tabular=True, locator=SHEET_LOCATOR)


def _fields(chunk) -> tuple[object, ...]:  # noqa: ANN001 — 测试内的字段快照
    """chunk 的全部可观察字段，用来做"逐字段不变"的对照。"""
    return (
        chunk.ordinal,
        chunk.content,
        chunk.page_no,
        chunk.page_end_no,
        chunk.heading_path,
        chunk.char_start,
        chunk.char_end,
        chunk.token_count,
        chunk.locator_ref,
    )


# --------------------------------------------------------------------------
# (a) parser：字段存在、默认值向后兼容、四个解析函数不崩
# --------------------------------------------------------------------------


def test_block_new_fields_default_to_absent() -> None:
    """老调用方式（只给 text）仍然可用：两个新字段都有缺省值。"""
    block = Block(text="一段正文")
    assert block.locator is None
    assert block.is_tabular is False
    assert (block.page_no, block.heading_path) == (None, None)


def test_block_keeps_locator_when_given() -> None:
    """字段只是承载位：给什么存什么，不做改写。"""
    block = Block(text="x", locator=PageLocator(page=7), is_tabular=True)
    assert isinstance(block.locator, PageLocator)
    assert block.locator.page == 7
    assert block.is_tabular is True


@pytest.mark.parametrize(
    ("payload", "filename"),
    [
        (b"alpha\n\nbeta\n", "a.txt"),
        (b"# T\n\nbody\n", "a.md"),
        (b"<p>alpha</p><p>beta</p>", "a.html"),
    ],
)
def test_existing_parsers_do_not_crash_and_leave_locator_empty(
    payload: bytes, filename: str
) -> None:
    """四个解析器只需"不崩"：本节点不填 locator，也不产出表格块。

    后续 S2/S4 节点真正回填 `locator` 后，`is None` 这条断言应改成"非 None"。
    """
    parsed = parse(payload, filename)
    assert parsed.blocks
    assert all(b.locator is None for b in parsed.blocks)
    assert all(b.is_tabular is False for b in parsed.blocks)


def test_docx_with_a_table_still_parses_without_crashing() -> None:
    """含表格的 DOCX 仍然按段落解析。

    本节点**不做**表格提取（`_parse_docx` 不读 `document.tables`），这里只钉住
    "有表格的文件不会把解析炸掉"——表格块是后续节点的活。
    """
    import docx

    document = docx.Document()
    document.add_paragraph("引言段落")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "表头A"
    table.cell(0, 1).text = "表头B"
    document.add_paragraph("结论段落")
    buffer = io.BytesIO()
    document.save(buffer)

    parsed = parse(buffer.getvalue(), "a.docx")
    texts = [b.text for b in parsed.blocks]
    assert "引言段落" in texts
    assert "结论段落" in texts


# --------------------------------------------------------------------------
# (b) D3-1：表格块不进 full_text 拼接
# --------------------------------------------------------------------------


def test_plain_documents_full_text_is_unchanged() -> None:
    """没有表格块时，full_text 与"逐块 \\n\\n 拼接"完全一致（旧口径）。"""
    blocks = [Block(text="AAAA"), Block(text="BBBB"), Block(text="CCCC")]
    assert ParsedText(blocks=blocks).full_text == "AAAA\n\nBBBB\n\nCCCC"


def test_full_text_excludes_tabular_block() -> None:
    """表格块的内容不得混进 full_text——混进去所有偏移都会错位。"""
    ordinary_a, ordinary_b = "普通段落一", "普通段落二"
    table_text = "Sheet1!B2:C4 的表格内容"
    parsed = ParsedText(
        blocks=[
            Block(text=ordinary_a),
            Block(text=table_text, is_tabular=True, locator=SHEET_LOCATOR),
            Block(text=ordinary_b),
        ],
        parser="test",
    )

    assert parsed.full_text == f"{ordinary_a}\n\n{ordinary_b}"
    assert table_text not in parsed.full_text


def test_tabular_chunk_content_not_in_full_text_and_ordinary_text_exact() -> None:
    """切分层同样不把表格拼进全文：表格 chunk 有内容，但全文里没有它。"""
    parsed = ParsedText(
        blocks=[
            Block(text="普通块 A"),
            Block(text="TABLE-CELLS", is_tabular=True, locator=SHEET_LOCATOR),
            Block(text="普通块 B"),
        ],
        parser="test",
    )
    chunks = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)

    assert parsed.full_text == "普通块 A\n\n普通块 B"
    tabular = [c for c in chunks if c.locator_ref == "sheet:Sheet1!B2:C4"]
    assert len(tabular) == 1
    assert tabular[0].content == "TABLE-CELLS"
    # 普通块内容一字不差（只 strip 首尾，不折叠内部空白）。
    ordinary = [c for c in chunks if c.content != "TABLE-CELLS"]
    assert [c.content for c in ordinary] == ["普通块 A\n\n普通块 B"]


# --------------------------------------------------------------------------
# (c) D3-2：表格块的 char_* 是块内偏移，page_at / heading_at 不求值
# --------------------------------------------------------------------------


def test_tabular_chunk_offsets_are_block_local_not_document_offsets() -> None:
    """构造一个"全文位置远大于块内偏移"的用例来证明语义。

    前一块 40 字符：若表格块按全文偏移算，`char_start` 至少是 42（还有 `\\n\\n`）。
    实际必须是块内偏移 0（表格块不进拼接，根本不存在全文位置）。
    """
    prefix = "A" * 40
    parsed = ParsedText(
        blocks=[
            Block(text=prefix, page_no=1),
            Block(text="B2:C4", is_tabular=True, locator=SHEET_LOCATOR),
        ],
        parser="test",
    )
    chunks = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)
    tabular = [c for c in chunks if c.locator_ref.startswith("sheet:")][0]

    assert tabular.content == "B2:C4"
    assert (tabular.char_start, tabular.char_end) == (0, 5)
    # 关键对照：块内偏移明显小于"若按全文偏移"会落到的位置。
    assert tabular.char_end < len(prefix) + 2
    # 而普通块仍然是全文坐标：它从 0 开始，长度就是前缀长度。
    ordinary = [c for c in chunks if c.locator_ref.startswith("offsets:")][0]
    assert (ordinary.char_start, ordinary.char_end) == (0, len(prefix))


def test_tabular_chunk_offsets_exclude_leading_whitespace_but_stay_block_local() -> None:
    """块内偏移同样从 strip 之后算起：前导空白不贡献 start。"""
    parsed = ParsedText(
        blocks=[Block(text="   \n  cells here\n", is_tabular=True, locator=SHEET_LOCATOR)],
        parser="test",
    )
    chunk = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)[0]

    assert chunk.content == "cells here"
    assert (chunk.char_start, chunk.char_end) == (6, 16)


def test_tabular_chunk_has_no_page_and_no_heading() -> None:
    """表格块带 page_no / heading_path 时，chunk 上仍然必须是 None（D3-2）。

    否则前端会拿一个"页内第 N 个字"的偏移去跳原文，跳到完全无关的位置。
    """
    parsed = ParsedText(
        blocks=[
            Block(text="正文第一页", page_no=1, heading_path="第一章"),
            Block(
                text="表格内容",
                page_no=2,
                heading_path="第二章 / 表 1",
                is_tabular=True,
                locator=SHEET_LOCATOR,
            ),
        ],
        parser="test",
    )
    chunks = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)

    tabular = [c for c in chunks if c.content == "表格内容"][0]
    assert tabular.page_no is None
    assert tabular.page_end_no is None
    assert tabular.heading_path is None

    # 普通块的 page/heading 照旧求值。
    ordinary = [c for c in chunks if c.content != "表格内容"][0]
    assert (ordinary.page_no, ordinary.page_end_no) == (1, 1)
    assert ordinary.heading_path == "第一章"


# --------------------------------------------------------------------------
# (d) D3-4：表格块的 locator_ref 是 locator 的紧凑串
# --------------------------------------------------------------------------


def test_tabular_locator_ref_is_compact_sheet_string() -> None:
    """验收指定形态：`sheet:Sheet1!B2:C4`。"""
    parsed = ParsedText(
        blocks=[Block(text="B2:C4 内容", is_tabular=True, locator=SHEET_LOCATOR)],
        parser="test",
    )
    chunk = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)[0]
    assert chunk.locator_ref == "sheet:Sheet1!B2:C4"
    assert not chunk.locator_ref.startswith("offsets:")


def test_tabular_locator_ref_without_cells_is_sheet_only() -> None:
    parsed = ParsedText(
        blocks=[
            Block(text="整张表", is_tabular=True, locator=SheetLocator(sheet="数据源")),
        ],
        parser="test",
    )
    chunk = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)[0]
    assert chunk.locator_ref == "sheet:数据源"


def test_tabular_locator_ref_falls_back_to_offsets_without_locator() -> None:
    """`locator=None` 时保持既有行为不变：`offsets:{start}-{end}`。"""
    parsed = ParsedText(blocks=[Block(text="no locator", is_tabular=True)], parser="test")
    chunk = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)[0]

    assert chunk.content == "no locator"
    assert chunk.locator_ref == f"offsets:{chunk.char_start}-{chunk.char_end}"
    assert chunk.locator_ref == "offsets:0-10"


def test_ordinary_chunk_locator_ref_is_still_offsets() -> None:
    """普通块的 locator_ref 形态一个字都不许变。"""
    parsed = ParsedText(blocks=[Block(text="abcdefghij")], parser="test")
    chunk = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)[0]
    assert chunk.locator_ref == "offsets:0-10"


@pytest.mark.parametrize(
    ("locator", "expected"),
    [
        (PageLocator(page=12), "page:12"),
        (SlideLocator(slide=3), "slide:3"),
        (TableLocator(index=2, rows="R1200:R1350"), "table:R1200:R1350"),
        (TableLocator(index=2, cells="A1:D10"), "table:A1:D10"),
        (TableLocator(index=2), "table:2"),
        (SheetLocator(sheet="Sheet1", cells="B2:C4"), "sheet:Sheet1!B2:C4"),
        (SheetLocator(sheet="Sheet1"), "sheet:Sheet1"),
        (ParaLocator(index=7), "para:7"),
        (ParaLocator(), "para"),
        (RegionLocator(page=1, bbox=(0.0, 0.0, 595.0, 842.0)), "region:1"),
    ],
)
def test_compact_locator_strings(locator: object, expected: str) -> None:
    assert compact_locator(locator) == expected  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# (e) 普通块回归：加一个表格块不改变任何普通块
# --------------------------------------------------------------------------


def _ordinary_blocks() -> list[Block]:
    return [
        Block(text="第一段：" + "甲乙丙丁" * 20 + "。", page_no=1, heading_path="第一章"),
        Block(text="第二段：" + "戊己庚辛" * 20 + "。", page_no=2, heading_path="第二章"),
    ]


def test_ordinary_chunks_identical_with_and_without_a_tabular_block() -> None:
    """对照用例：仅多一个表格块，普通块的 9 个可观察字段必须逐个相同。

    这条同时保护两件事：表格块不参与拼接（所以不产生 `\\n\\n` 位移），
    以及普通块的 char_* 仍是全文偏移。
    """
    before = chunk_parsed(ParsedText(blocks=_ordinary_blocks()), chunk_size=150, chunk_overlap=20)
    after = chunk_parsed(
        ParsedText(blocks=[*_ordinary_blocks(), _tabular("表格块内容")]),
        chunk_size=150,
        chunk_overlap=20,
    )

    # 表格块排在文档最后 → 它的 ordinal 是末位，前面普通块的 ordinal 不受影响。
    assert len(after) == len(before) + 1
    assert [_fields(c) for c in after[: len(before)]] == [_fields(c) for c in before]


def test_ordinary_chunk_content_is_exact_full_text_slice() -> None:
    """红线 I6：普通块的 content 是 full_text 的原样切片，内部空白不折叠。"""
    parsed = ParsedText(
        blocks=[
            Block(text="a  b   c\td", page_no=1),
            Block(text="TABLE", is_tabular=True, locator=SHEET_LOCATOR),
            Block(text="e  f", page_no=2),
        ],
        parser="test",
    )
    chunks = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)
    ordinary = [c for c in chunks if not c.locator_ref.startswith("sheet:")]
    for chunk in ordinary:
        assert parsed.full_text[chunk.char_start : chunk.char_end] == chunk.content
    # 内部空白必须原样保留（两个空格、制表符都不许被折叠）。
    assert "a  b   c\td" in "".join(c.content for c in ordinary)


def test_ordinals_follow_document_order_with_tabular_block_in_the_middle() -> None:
    """表格块在中间时，chunk 顺序仍按文档顺序，ordinal 从 0 连续。"""
    parsed = ParsedText(
        blocks=[
            Block(text="前段文字"),
            Block(text="中间表格", is_tabular=True, locator=SHEET_LOCATOR),
            Block(text="后段文字"),
        ],
        parser="test",
    )
    chunks = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)

    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert [c.content for c in chunks] == ["前段文字\n\n后段文字", "中间表格"]


def test_tabular_only_document_still_yields_a_chunk() -> None:
    """只有表格块时不能报 PARSE_INCOMPLETE——表格块本身就是可索引内容。"""
    parsed = ParsedText(
        blocks=[Block(text="只有表格", is_tabular=True, locator=SHEET_LOCATOR)], parser="test"
    )
    chunks = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)
    assert [c.content for c in chunks] == ["只有表格"]
    assert chunks[0].locator_ref == "sheet:Sheet1!B2:C4"


def test_empty_tabular_block_is_skipped_not_fatal() -> None:
    """空白表格块不产出 chunk，也不影响普通块的产出。"""
    parsed = ParsedText(
        blocks=[
            Block(text="正文"),
            Block(text="   \n ", is_tabular=True, locator=SHEET_LOCATOR),
        ],
        parser="test",
    )
    chunks = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)
    assert [c.content for c in chunks] == ["正文"]


# --------------------------------------------------------------------------
# (e) 三类块都必须把 block.locator 带到 chunk 上（2026-11-03 补的缺口）
# --------------------------------------------------------------------------
# 曾经只有视觉块传了 `locator=`，文本块与表格块只写 `locator_ref`，于是真实 PDF /
# Excel 的 chunk 落库后 `chunks.locator` 是 NULL，`/query` 与 chat 出参只能给 null，
# 前端空有字段没法高亮。这一节把"三类块都传"钉住。


def test_ordinary_chunk_carries_page_locator() -> None:
    """文本块的结构位置必须出现在 chunk 上，而不只是 offsets 句柄。"""
    parsed = ParsedText(
        blocks=[Block(text="第一段正文", page_no=3, locator=PageLocator(page=3))], parser="test"
    )
    chunks = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)

    assert len(chunks) == 1
    assert isinstance(chunks[0].locator, PageLocator)
    assert chunks[0].locator.page == 3
    # offsets 句柄仍在（两者分工，不互相取代）。
    assert chunks[0].locator_ref == "offsets:0-5"


def test_tabular_chunk_carries_sheet_locator() -> None:
    """表格块的 page_no 让路，但 locator 必须留下——它是唯一的表/单元格位置。"""
    parsed = ParsedText(blocks=[_tabular("A1:B2\nB2:C4")], parser="test")
    chunks = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)

    assert chunks[0].page_no is None
    assert chunks[0].locator == SHEET_LOCATOR
    assert chunks[0].locator_ref == "sheet:Sheet1!B2:C4"


def test_chunk_spanning_two_blocks_takes_the_start_block_locator() -> None:
    """跨块 chunk 取起点所在块的位置——与 page_no/heading_path 同一口径。"""
    parsed = ParsedText(
        blocks=[
            Block(text="甲" * 30, page_no=1, locator=PageLocator(page=1)),
            Block(text="乙" * 30, page_no=2, locator=PageLocator(page=2)),
        ],
        parser="test",
    )
    chunks = chunk_parsed(parsed, chunk_size=70, chunk_overlap=0)

    assert len(chunks) >= 1
    assert chunks[0].locator == PageLocator(page=1)


def test_block_without_locator_still_yields_null_not_offsets_leak() -> None:
    """解析器没给位置时保持 None（旧格式/降级路径），不许编一个假位置出来。"""
    parsed = ParsedText(blocks=[Block(text="没有位置的正文", page_no=5)], parser="test")
    chunks = chunk_parsed(parsed, chunk_size=512, chunk_overlap=0)

    assert chunks[0].locator is None
    assert chunks[0].page_no == 5
