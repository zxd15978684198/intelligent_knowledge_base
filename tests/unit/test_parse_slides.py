"""N8：PPTX 解析器（ADR 0008 D3 的 `slide` / `table` 两个 locator 分支）。

样张全部由 ``python-pptx`` **现场生成**，仓库里不放二进制夹具（``.pptx`` 进 git 会让
diff 不可读，也容易被"改样张不改断言"污染）。

依赖说明：``python-pptx`` 目前**不在** ``pyproject.toml`` 里（本节点按硬约束不改依赖），
所以需要生成样张的用例在缺包时 ``pytest.skip``；三条纯契约用例（``SUFFIXES``、
缺包、坏容器）不依赖 pptx，任何环境下都会**真正执行**。验收时用
``uv run --with python-pptx …`` 让整组用例跑起来（见交付报告里的两条命令）。

口径随实现（``services/ingest/parsers/slides.py`` 的模块 docstring 有完整理由）：

- ``slide`` 从 1 开始、按幻灯片顺序连续，空页不产出块；
- ``TableLocator.index`` 从 **1** 开始（``schemas/locator.py`` 是 ``Field(ge=1)``，
  任务书写的"从 0 开始"会被 pydantic 直接拒），且是全文维度、跨幻灯片累加；
- ``rows`` 含表头那一行，所以 3 列 × 4 行（1 表头 + 3 数据）的样张是 ``R1:R4``；
- 图片只被跳过、不 OCR，也不产出块。
"""

from __future__ import annotations

import io
import struct
import sys
import zipfile
import zlib
from typing import Any

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import SlideLocator, TableLocator
from pyknora.services.ingest.guards import ParseGuardError
from pyknora.services.ingest.parsers import slides as slides_parser
from pyknora.services.ingest.parsers.slides import SUFFIXES, parse

TITLE = "PyKnora 解析层"
BODY_LINES: tuple[str, str] = ("第一段：解析器按后缀分派。", "第二段：每块带 locator。")
#: 第 2 页表格之外的文本框：`TableLocator` 不带 `slide` 字段，没有它就无法证明
#: "`slide` 从 1 起、跨页连续"——第 2 页必须有一个文本块才能观察到 slide=2。
CAPTION = "第 2 页：表格样张"
#: 4 行 × 3 列：表头 + 3 条数据，正好对应 `rows == "R1:R4"`（列数不影响行区间）。
#: "N8" 只出现在表格里，用来证明表格块**不进** `ParsedText.full_text`。
TABLE_ROWS: tuple[tuple[str, str, str], ...] = (
    ("模块", "状态", "负责人"),
    ("解析", "进行中", "N8"),
    ("切片", "未开始", "N9"),
    ("评测", "未开始", "N10"),
)


# --------------------------------------------------------------------------- 样张


def _pptx() -> Any:
    """取 python-pptx；项目依赖里还没有它，缺包时跳过需要样张的用例。"""
    return pytest.importorskip(
        "pptx",
        reason="样张由 python-pptx 现场生成；用 uv run --with python-pptx 跑本组用例",
    )


def _tiny_png() -> bytes:
    """1×1 PNG（红）：不引 Pillow，用 zlib + struct 手搓最小合法文件。"""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 宽 1、高 1、8 bit truecolor
    raw_scanline = b"\x00" + b"\xff\x00\x00"  # 行过滤字节 0 + RGB
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw_scanline))
        + chunk(b"IEND", b"")
    )


def _save(presentation: Any) -> bytes:
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def _deck_bytes() -> bytes:
    """3 页样张：第 1 页标题 + 正文，第 2 页 4×3 表格 + 文本框，第 3 页完全空白。

    ``slide_layouts[1]`` 是默认模板的 "Title and Content"（标题 + 正文占位符），
    ``slide_layouts[6]`` 是 "Blank"（一个占位符都没有，正好验证空页不产块）。
    第 2 页的文本框是"观察窗口"：表格的 ``TableLocator`` 不带 ``slide``，只有文本块
    能证明幻灯片序号连续（见 ``CAPTION`` 注释）。
    """
    pptx = _pptx()
    from pptx.util import Inches

    presentation = pptx.Presentation()

    first = presentation.slides.add_slide(presentation.slide_layouts[1])
    first.shapes.title.text = TITLE
    body = first.placeholders[1].text_frame
    body.text = BODY_LINES[0]
    body.add_paragraph().text = BODY_LINES[1]

    second = presentation.slides.add_slide(presentation.slide_layouts[6])
    table = second.shapes.add_table(
        len(TABLE_ROWS), 3, Inches(1), Inches(1), Inches(6), Inches(2)
    ).table
    for row_no, row in enumerate(TABLE_ROWS):
        for col_no, value in enumerate(row):
            table.cell(row_no, col_no).text = value
    second.shapes.add_textbox(Inches(1), Inches(5), Inches(6), Inches(1)).text_frame.text = CAPTION

    presentation.slides.add_slide(presentation.slide_layouts[6])
    return _save(presentation)


def _two_table_deck_bytes() -> bytes:
    """2 页，各放一张表：验证 ``TableLocator.index`` 是全文维度、跨幻灯片累加。"""
    pptx = _pptx()
    from pptx.util import Inches

    presentation = pptx.Presentation()
    for row_count in (2, 3):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        table = slide.shapes.add_table(
            row_count, 2, Inches(1), Inches(1), Inches(4), Inches(2)
        ).table
        for row_no in range(row_count):
            for col_no in range(2):
                table.cell(row_no, col_no).text = f"r{row_no}c{col_no}"
    return _save(presentation)


def _picture_only_deck_bytes() -> bytes:
    """1 页，只有一张图片：没有文字也没有表格 → 空 `ParsedText`，且**不**抛错。"""
    pptx = _pptx()
    from pptx.util import Inches

    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_picture(io.BytesIO(_tiny_png()), Inches(1), Inches(1))
    return _save(presentation)


# --------------------------------------------------------------------------- 契约


def test_suffixes_is_exactly_pptx() -> None:
    assert frozenset({".pptx"}) == SUFFIXES
    assert isinstance(SUFFIXES, frozenset)


def test_missing_python_pptx_raises_parse_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺依赖路径：``sys.modules["pptx"] = None`` 让函数内 import 抛 ImportError。

    用最小合法 zip 过掉容器预检，冲突点因此**只**剩 import——不依赖 pptx 是否安装，
    两种环境都走同一条分支。
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
    monkeypatch.setitem(sys.modules, "pptx", None)

    with pytest.raises(AppError) as excinfo:
        parse(buffer.getvalue(), filename="deck.pptx")

    error = excinfo.value
    assert error.code == ErrorCode.PARSE_FAILED
    assert error.code == "PARSE_FAILED"
    assert "python-pptx" in error.message
    assert error.http_status == 422


def test_non_zip_bytes_raise_parse_failed() -> None:
    with pytest.raises(AppError) as excinfo:
        parse(b"this is definitely not a zip archive")
    assert excinfo.value.code == ErrorCode.PARSE_FAILED
    assert excinfo.value.details["reason"] == "BadZipFile"


def test_zip_without_ooxml_parts_raises_parse_failed() -> None:
    """合法 zip 但不是 pptx：``Presentation()`` 抛库内异常，必须包成 PARSE_FAILED。

    缺 python-pptx 时这条会先撞上 ImportError（同样是 PARSE_FAILED），所以断言只钉错误码。
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "not a presentation")
    with pytest.raises(AppError) as excinfo:
        parse(buffer.getvalue())
    assert excinfo.value.code == ErrorCode.PARSE_FAILED


# --------------------------------------------------------------------------- 三页样张


def test_three_slide_deck_produces_documented_blocks() -> None:
    parsed = parse(_deck_bytes(), filename="deck.pptx")

    assert parsed.parser == "python-pptx"
    # 第 1 页标题 + 正文、第 2 页表格 + 文本框，共 4 块；第 3 页空白，不产出块。
    assert len(parsed.blocks) == 4

    slide_locators = [b.locator for b in parsed.blocks if isinstance(b.locator, SlideLocator)]
    # `slide` 从 1 开始且连续：出现的是 1、2；空页 3 不出现。
    assert sorted({locator.slide for locator in slide_locators}) == [1, 2]

    title_block = parsed.blocks[0]
    assert isinstance(title_block.locator, SlideLocator)
    assert title_block.locator.kind == "slide"
    assert title_block.locator.slide == 1
    assert title_block.locator.shape  # 形状名非空
    assert isinstance(title_block.locator.shape, str)
    assert title_block.text == TITLE
    assert title_block.is_tabular is False

    body_block = parsed.blocks[1]
    assert isinstance(body_block.locator, SlideLocator)
    assert body_block.locator.slide == 1
    assert body_block.locator.shape  # 正文占位符同样带形状名
    assert body_block.text == "\n".join(BODY_LINES)
    assert body_block.is_tabular is False

    table_block = parsed.blocks[2]
    assert table_block.is_tabular is True
    assert isinstance(table_block.locator, TableLocator)
    assert table_block.locator.kind == "table"
    assert table_block.locator.index == 1
    # 含表头那一行：4 行 → R1:R4。
    assert table_block.locator.rows == "R1:R4"
    rendered = table_block.text.splitlines()
    assert rendered[0] == "\t".join(TABLE_ROWS[0])
    assert rendered[3] == "\t".join(TABLE_ROWS[3])
    assert len(rendered) == len(TABLE_ROWS)

    caption_block = parsed.blocks[3]
    assert isinstance(caption_block.locator, SlideLocator)
    assert caption_block.locator.slide == 2
    assert caption_block.locator.shape  # 文本框形状名非空
    assert caption_block.text == CAPTION
    assert caption_block.is_tabular is False

    # 空页不产出块：没有任何 SlideLocator 指向第 3 页。
    assert all(locator.slide != 3 for locator in slide_locators)


def test_empty_slide_and_table_are_separated_in_full_text() -> None:
    """表格块是独立单元：不进 `full_text`（ADR 0008 D3-1）。"""
    parsed = parse(_deck_bytes())

    assert TITLE in parsed.full_text
    assert BODY_LINES[0] in parsed.full_text
    # "N8" 只存在于表格里。
    assert "N8" not in parsed.full_text


def test_table_index_accumulates_across_slides() -> None:
    parsed = parse(_two_table_deck_bytes())

    tables = [block for block in parsed.blocks if block.is_tabular]
    assert len(tables) == 2
    indexes = []
    rows = []
    for block in tables:
        assert isinstance(block.locator, TableLocator)
        indexes.append(block.locator.index)
        rows.append(block.locator.rows)
    # 全文维度、从 1 开始：第 2 页的表是"第 2 张表"，序号不按页重置。
    assert indexes == [1, 2]
    assert rows == ["R1:R2", "R1:R3"]


# --------------------------------------------------------------------------- 空内容


def test_deck_without_any_text_or_table_returns_empty_parsed_text() -> None:
    """整篇无内容 → 空 ParsedText，**不**抛 PARSE_FAILED（只有图片的稿子不是坏文件）。"""
    pptx = _pptx()
    presentation = pptx.Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[6])

    parsed = parse(_save(presentation))

    assert parsed.blocks == []
    assert parsed.parser == "python-pptx"
    assert parsed.full_text == ""


def test_picture_is_skipped() -> None:
    parsed = parse(_picture_only_deck_bytes())

    assert parsed.blocks == []
    assert parsed.full_text == ""


# --------------------------------------------------------------------------- 预检接线


def test_zip_size_guard_is_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """`check_zip_size` 必须在真实数据路径上被调用，且收到这一次打开的归档。"""
    data = _deck_bytes()
    seen: list[Any] = []

    def spy(archive: Any) -> None:
        assert isinstance(archive, zipfile.ZipFile)
        seen.append(archive)

    monkeypatch.setattr(slides_parser, "check_zip_size", spy)
    parse(data)

    assert len(seen) == 1


def test_document_limit_receives_slide_count(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _deck_bytes()
    calls: list[dict[str, int]] = []

    def spy(**kwargs: int) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(slides_parser, "check_document_limits", spy)
    parse(data)

    # 样张正好 3 页；只传 slides，不传 rows/pages（PPTX 没有这两个维度）。
    assert calls == [{"slides": 3}]


def test_guard_error_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """超限的 `ParseGuardError` 必须原样穿透，不能被兜底的 `except Exception` 改写。"""
    data = _deck_bytes()

    def boom(**kwargs: int) -> None:
        raise ParseGuardError("幻灯片数超限", metric="slides", limit=2000, actual=2001)

    monkeypatch.setattr(slides_parser, "check_document_limits", boom)

    with pytest.raises(AppError) as excinfo:
        parse(data)

    assert excinfo.value.code == ErrorCode.PARSE_FAILED
    assert excinfo.value.details["metric"] == "slides"
    assert excinfo.value.details["actual"] == 2001
