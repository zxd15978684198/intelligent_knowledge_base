"""N9：OpenDocument（odt/ods/odp）解析器单测。

`content.xml` 全部**现场拼**：ODF 的正文只取决于命名空间与三个元素
（`text:p` / `table:table` / `draw:page`），不需要 LibreOffice 导出的真实文件，
也不需要 `mimetype` 条目——解析器只读 `content.xml`。

三条口径与需求文档不同，这里按**代码里的真实契约**断言（详见 `odf.py` 模块
docstring）：`ParaLocator.index` / `TableLocator.index` 都是 `ge=1`、从 1 开始；
`draw:page` 在 drawing 命名空间而不是 presentation 命名空间。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import ParaLocator, SlideLocator, TableLocator
from pyknora.services.ingest.guards import MAX_ODF_REPEATED, ParseGuardError
from pyknora.services.ingest.parsers import odf

_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<office:document-content"
    ' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
    ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
    ' xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"'
    ' xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"'
    ' office:version="1.2">'
)


def _document(body: str) -> str:
    return f"{_HEAD}<office:body>{body}</office:body></office:document-content>"


def _odt(*paragraphs: str) -> str:
    return _document("<office:text>" + "".join(paragraphs) + "</office:text>")


def _ods(*sheets: str) -> str:
    return _document("<office:spreadsheet>" + "".join(sheets) + "</office:spreadsheet>")


def _odp(*pages: str) -> str:
    return _document("<office:presentation>" + "".join(pages) + "</office:presentation>")


def _zip(entries: dict[str, str | bytes], *, mime: str | None = None) -> bytes:
    """把条目打成内存 zip；`mime` 给了就按 ODF 规范放在第一条。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if mime is not None:
            zf.writestr("mimetype", mime)
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _parse_odt(xml: str, filename: str = "doc.odt"):
    return odf.parse(_zip({"content.xml": xml}), filename)


# --------------------------------------------------------------------- 契约


def test_suffixes() -> None:
    expected = frozenset({".odt", ".ods", ".odp"})
    assert expected == odf.SUFFIXES


def test_parser_name_is_odf() -> None:
    parsed = _parse_odt(_odt("<text:p>hi</text:p>"))
    assert parsed.parser == "odf"


# --------------------------------------------------------------------- .odt 段落与标题


def test_odt_two_paragraphs() -> None:
    parsed = _parse_odt(
        _odt(
            "<text:p>第一段</text:p>",
            "<text:p>第二段</text:p>",
        )
    )
    assert [b.text for b in parsed.blocks] == ["第一段", "第二段"]
    assert all(b.is_tabular is False for b in parsed.blocks)
    # index 从 1 起（locator.py 的 `ge=1`），连续递增。
    assert [b.locator.index for b in parsed.blocks] == [1, 2]
    assert all(isinstance(b.locator, ParaLocator) for b in parsed.blocks)
    # 没有标题时 heading 为 None，而不是空串。
    assert [b.locator.heading for b in parsed.blocks] == [None, None]


def test_odt_heading_binds_to_following_paragraphs() -> None:
    parsed = _parse_odt(
        _odt(
            "<text:h>第一章</text:h>",
            "<text:p>正文一</text:p>",
            "<text:p>正文二</text:p>",
            "<text:h>第二章</text:h>",
            "<text:p>正文三</text:p>",
        )
    )
    assert [b.locator.heading for b in parsed.blocks] == [
        "第一章",
        "第一章",
        "第一章",
        "第二章",
        "第二章",
    ]
    # heading_path 与 locator.heading 同口径（与 docx 解析器一致）。
    assert [b.heading_path for b in parsed.blocks] == [
        "第一章",
        "第一章",
        "第一章",
        "第二章",
        "第二章",
    ]
    assert parsed.blocks[-1].text == "正文三"


def test_odt_paragraph_keeps_nested_span_text_and_inline_space() -> None:
    """`text:s` 是空元素，`itertext()` 拿不到它——必须补出空格。"""
    parsed = _parse_odt(
        _odt(
            '<text:p>Hello<text:s/>World<text:s text:c="3"/>!</text:p>',
            "<text:p><text:span>红</text:span>与<text:span>蓝</text:span></text:p>",
        )
    )
    assert [b.text for b in parsed.blocks] == ["Hello World !", "红与蓝"]


def test_odt_empty_paragraphs_are_skipped() -> None:
    parsed = _parse_odt(_odt("<text:p>只有一段</text:p>", "<text:p>  </text:p>"))
    assert [b.text for b in parsed.blocks] == ["只有一段"]
    assert len(parsed.blocks) == 1


def test_odt_list_items_are_walked() -> None:
    """列表段落藏在 `text:list/text:list-item` 里，不能因为不是直接子节点就漏掉。"""
    parsed = _parse_odt(
        _odt(
            "<text:list><text:list-item><text:p>项一</text:p></text:list-item>"
            "<text:list-item><text:p>项二</text:p></text:list-item></text:list>"
        )
    )
    assert [b.text for b in parsed.blocks] == ["项一", "项二"]


# --------------------------------------------------------------------- .odt 表格


_TABLE_3X2 = (
    '<table:table table:name="T1">'
    "<table:table-row>"
    "<table:table-cell><text:p>A1</text:p></table:table-cell>"
    "<table:table-cell><text:p>B1</text:p></table:table-cell>"
    "</table:table-row>"
    "<table:table-row>"
    "<table:table-cell><text:p>A2</text:p></table:table-cell>"
    "<table:table-cell><text:p>B2</text:p></table:table-cell>"
    "</table:table-row>"
    "<table:table-row>"
    "<table:table-cell><text:p>A3</text:p></table:table-cell>"
    "<table:table-cell><text:p>B3</text:p></table:table-cell>"
    "</table:table-row>"
    "</table:table>"
)


def test_odt_table_block_is_tabular_with_row_range() -> None:
    parsed = _parse_odt(_odt("<text:p>表前</text:p>", _TABLE_3X2, "<text:p>表后</text:p>"))
    table_block = parsed.blocks[1]
    assert table_block.is_tabular is True
    assert isinstance(table_block.locator, TableLocator)
    assert table_block.locator.rows == "R1:R3"
    assert table_block.locator.index == 1
    assert table_block.text == "A1\tB1\nA2\tB2\nA3\tB3"


def test_odt_table_block_does_not_consume_paragraph_index() -> None:
    """表格块不产生 ParaLocator，也不占段落序号：表前是 1，表后仍是 2。"""
    parsed = _parse_odt(_odt("<text:p>表前</text:p>", _TABLE_3X2, "<text:p>表后</text:p>"))
    assert [b.text for b in parsed.blocks] == [
        "表前",
        "A1\tB1\nA2\tB2\nA3\tB3",
        "表后",
    ]
    para_locators = [b.locator for b in parsed.blocks if not b.is_tabular]
    assert [loc.index for loc in para_locators] == [1, 2]
    assert all(isinstance(loc, ParaLocator) for loc in para_locators)


def test_odt_table_cell_text_is_flat_single_line() -> None:
    """单元格里的多段/换行要压平，否则一行的 `\\t` 结构会被破坏。"""
    parsed = _parse_odt(
        _odt(
            "<table:table><table:table-row><table:table-cell>"
            "<text:p>甲</text:p><text:p>乙</text:p>"
            "<text:p>x<text:line-break/>y</text:p>"
            "</table:table-cell></table:table-row></table:table>"
        )
    )
    assert parsed.blocks[0].text == "甲 乙 x y"


def test_odt_table_with_empty_leading_cell_keeps_column_alignment() -> None:
    """首列为空的表不能被 strip 掉 `\\t`，否则整行左移、列数少一格。"""
    parsed = _parse_odt(
        _odt(
            "<table:table><table:table-row>"
            "<table:table-cell/>"
            "<table:table-cell><text:p>A1</text:p></table:table-cell>"
            "</table:table-row></table:table>"
        )
    )
    assert parsed.blocks[0].text == "\tA1"
    assert parsed.blocks[0].text.split("\t") == ["", "A1"]


def test_odt_table_rows_in_header_wrapper_are_collected() -> None:
    parsed = _parse_odt(
        _odt(
            "<table:table><table:table-header-rows>"
            "<table:table-row><table:table-cell><text:p>H</text:p></table:table-cell>"
            "</table:table-row></table:table-header-rows>"
            "<table:table-row><table:table-cell><text:p>V</text:p></table:table-cell>"
            "</table:table-row></table:table>"
        )
    )
    block = parsed.blocks[0]
    assert block.text == "H\nV"
    assert block.locator.rows == "R1:R2"


def test_odt_column_repeat_expands() -> None:
    parsed = _parse_odt(
        _odt(
            "<table:table><table:table-row>"
            '<table:table-cell table:number-columns-repeated="3">'
            "<text:p>x</text:p></table:table-cell>"
            "</table:table-row></table:table>"
        )
    )
    assert parsed.blocks[0].text == "x\tx\tx"


def test_odt_full_text_excludes_tabular_blocks() -> None:
    """ADR 0008 D3-1：表格块不参与 `full_text` 拼接。"""
    parsed = _parse_odt(_odt("<text:p>正文</text:p>", _TABLE_3X2))
    assert parsed.full_text == "正文"


# --------------------------------------------------------------------- .ods


def test_ods_one_block_per_sheet() -> None:
    sheet_a = (
        '<table:table table:name="S1">'
        "<table:table-row><table:table-cell><text:p>a1</text:p></table:table-cell>"
        "<table:table-cell><text:p>b1</text:p></table:table-cell></table:table-row>"
        "</table:table>"
    )
    sheet_b = (
        '<table:table table:name="S2">'
        "<table:table-row><table:table-cell><text:p>a2</text:p></table:table-cell>"
        "</table:table-row>"
        "</table:table>"
    )
    parsed = odf.parse(_zip({"content.xml": _ods(sheet_a, sheet_b)}), "book.ods")
    assert len(parsed.blocks) == 2
    assert all(b.is_tabular for b in parsed.blocks)
    assert parsed.blocks[0].text == "a1\tb1"
    assert parsed.blocks[1].text == "a2"
    # 一个 sheet 一个块，index = 工作表序号
    assert [b.locator.index for b in parsed.blocks] == [1, 2]
    assert [b.locator.rows for b in parsed.blocks] == ["R1:R1", "R1:R1"]


def test_ods_cell_column_repeat_expands_to_three_columns() -> None:
    parsed = odf.parse(
        _zip(
            {
                "content.xml": _ods(
                    '<table:table table:name="S1"><table:table-row>'
                    '<table:table-cell table:number-columns-repeated="3">'
                    "<text:p>v</text:p></table:table-cell>"
                    "</table:table-row></table:table>"
                )
            }
        ),
        "book.ods",
    )
    assert parsed.blocks[0].text == "v\tv\tv"
    assert len(parsed.blocks[0].text.split("\t")) == 3


def test_repeat_with_leading_zeros_is_still_numeric() -> None:
    """`0000001` 是 1 次，不是 10 000 次：长度闸必须先去前导 0 再判。"""
    parsed = odf.parse(
        _zip(
            {
                "content.xml": _ods(
                    '<table:table table:name="S1">'
                    '<table:table-row table:number-rows-repeated="0000001">'
                    "<table:table-cell><text:p>r</text:p></table:table-cell>"
                    "</table:table-row></table:table>"
                )
            }
        ),
        "book.ods",
    )
    assert parsed.blocks[0].text == "r"
    assert parsed.blocks[0].locator.rows == "R1:R1"


def test_ods_row_repeat_expands() -> None:
    parsed = odf.parse(
        _zip(
            {
                "content.xml": _ods(
                    '<table:table table:name="S1">'
                    '<table:table-row table:number-rows-repeated="3">'
                    "<table:table-cell><text:p>r</text:p></table:table-cell>"
                    "</table:table-row></table:table>"
                )
            }
        ),
        "book.ods",
    )
    assert parsed.blocks[0].text == "r\nr\nr"
    assert parsed.blocks[0].locator.rows == "R1:R3"


def test_ods_numeric_cell_falls_back_to_office_value() -> None:
    """纯数字单元格正文是空的，数值只在 `office:value` 属性里。"""
    parsed = odf.parse(
        _zip(
            {
                "content.xml": _ods(
                    '<table:table table:name="S1"><table:table-row>'
                    '<table:table-cell office:value-type="float" office:value="42"/>'
                    "<table:table-cell><text:p>总价</text:p></table:table-cell>"
                    "</table:table-row></table:table>"
                )
            }
        ),
        "book.ods",
    )
    assert parsed.blocks[0].text == "42\t总价"


def test_ods_empty_sheet_is_skipped() -> None:
    parsed = odf.parse(
        _zip(
            {
                "content.xml": _ods(
                    '<table:table table:name="Empty"/>',
                    '<table:table table:name="Data"><table:table-row>'
                    "<table:table-cell><text:p>v</text:p></table:table-cell>"
                    "</table:table-row></table:table>",
                )
            }
        ),
        "book.ods",
    )
    assert len(parsed.blocks) == 1
    # 空表不产出块，但序号按工作表位置算：数据在第 2 张表。
    assert parsed.blocks[0].locator.index == 2


# ------------------------------------------------------- 重复次数超限：抛错（不截断）


def test_ods_rows_repeated_at_limit_passes() -> None:
    """闭区间：恰好 10 000 放行。"""
    parsed = odf.parse(
        _zip(
            {
                "content.xml": _ods(
                    '<table:table table:name="S1">'
                    f'<table:table-row table:number-rows-repeated="{MAX_ODF_REPEATED}">'
                    "<table:table-cell><text:p>r</text:p></table:table-cell>"
                    "</table:table-row></table:table>"
                )
            }
        ),
        "book.ods",
    )
    text = parsed.blocks[0].text
    assert len(text.splitlines()) == MAX_ODF_REPEATED
    assert parsed.blocks[0].locator.rows == f"R1:R{MAX_ODF_REPEATED}"


@pytest.mark.parametrize(
    ("attr", "filename", "body"),
    [
        (
            "number-rows-repeated",
            "big.ods",
            '<table:table table:name="S1">'
            '<table:table-row table:number-rows-repeated="20000">'
            "<table:table-cell><text:p>r</text:p></table:table-cell>"
            "</table:table-row></table:table>",
        ),
        (
            "number-columns-repeated",
            "doc.odt",
            "<table:table><table:table-row>"
            '<table:table-cell table:number-columns-repeated="20000"/>'
            "</table:table-row></table:table>",
        ),
    ],
)
def test_repeated_over_limit_raises_guard_error(attr: str, filename: str, body: str) -> None:
    """超限选择**抛错**而不是截断：`check_odf_repeated` 的契约就是闭区间拒绝。

    截断会让一份结构性异常的文档"看起来解析成功"，下游拿到静默丢失的行；
    而 `ParseGuardError` 本身已经是 `PARSE_FAILED` + `limit/actual/metric`，
    直接透出即可定位，不必也不该再包一层。
    """
    xml = _ods(body) if filename.endswith(".ods") else _odt(body)
    with pytest.raises(ParseGuardError) as excinfo:
        odf.parse(_zip({"content.xml": xml}), filename)
    err = excinfo.value
    assert isinstance(err, AppError)
    assert err.code == ErrorCode.PARSE_FAILED
    assert err.details["metric"] == attr
    assert err.details["limit"] == MAX_ODF_REPEATED
    assert err.details["actual"] == 20000


# --------------------------------------------------------------------- .odp


def test_odp_slides_numbered_from_one() -> None:
    page_a = (
        '<draw:page draw:name="p1">'
        "<draw:frame><text:p>标题一</text:p></draw:frame>"
        "<draw:frame><text:p>正文一</text:p></draw:frame>"
        "</draw:page>"
    )
    page_b = (
        '<draw:page draw:name="p2"><draw:frame><text:p>标题二</text:p></draw:frame></draw:page>'
    )
    parsed = odf.parse(_zip({"content.xml": _odp(page_a, page_b)}), "deck.odp")
    assert [b.text for b in parsed.blocks] == ["标题一", "正文一", "标题二"]
    assert [b.locator.slide for b in parsed.blocks] == [1, 1, 2]
    assert all(isinstance(b.locator, SlideLocator) for b in parsed.blocks)


# --------------------------------------------------------------------- 坏输入


def test_non_zip_raises_parse_failed() -> None:
    with pytest.raises(AppError) as excinfo:
        odf.parse(b"this is definitely not a zip archive", "doc.odt")
    assert excinfo.value.code == ErrorCode.PARSE_FAILED
    assert excinfo.value.message == "不是有效的 OpenDocument"
    assert excinfo.value.retryable is False


def test_missing_content_xml_raises_parse_failed() -> None:
    data = _zip({"styles.xml": "<x/>", "meta.xml": "<y/>"})
    with pytest.raises(AppError) as excinfo:
        odf.parse(data, "doc.odt")
    assert excinfo.value.code == ErrorCode.PARSE_FAILED
    assert excinfo.value.message == "不是有效的 OpenDocument"


def test_malformed_xml_raises_parse_failed() -> None:
    with pytest.raises(AppError) as excinfo:
        odf.parse(_zip({"content.xml": "<office:document-content>"}), "doc.odt")
    assert excinfo.value.code == ErrorCode.PARSE_FAILED


def test_empty_document_raises_parse_failed() -> None:
    with pytest.raises(AppError) as excinfo:
        _parse_odt(_odt("<text:p>   </text:p>"))
    assert excinfo.value.code == ErrorCode.PARSE_FAILED


def test_zip_bomb_size_guard_is_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """闸门 1 真的接上了：必须在读 content.xml 之前调用，且异常不被吞掉。

    不在内存里真造 200 MiB 归档（那要写磁盘级数据），而是把闸门换成一个
    会抛错的桩——被验证的是**接线**与**异常透传**这两件事，闸门自身的阈值
    语义由 `tests/unit/test_parse_guards.py` 覆盖。
    """
    seen: list[list[str]] = []

    def fake_guard(archive: zipfile.ZipFile) -> None:
        seen.append(archive.namelist())
        raise ParseGuardError(
            "解压后累计体积超限",
            metric="uncompressed_bytes",
            limit=1,
            actual=2,
        )

    monkeypatch.setattr(odf, "check_zip_size", fake_guard)
    with pytest.raises(ParseGuardError) as excinfo:
        _parse_odt(_odt("<text:p>x</text:p>"))
    assert excinfo.value.details["metric"] == "uncompressed_bytes"
    # 证明闸门拿到的是打开着的归档，且 content.xml 已经就位。
    assert seen == [["content.xml"]]


# ------------------------------------------------------- 后缀缺失 / 不认的后缀


def test_missing_suffix_sniffs_body() -> None:
    """`filename` 默认空串：按正文结构判定，而不是必然抛错。"""
    parsed = odf.parse(
        _zip(
            {
                "content.xml": _ods(
                    '<table:table table:name="S1"><table:table-row>'
                    "<table:table-cell><text:p>v</text:p></table:table-cell>"
                    "</table:table-row></table:table>"
                )
            }
        )
    )
    assert parsed.parser == "odf"
    assert parsed.blocks[0].is_tabular is True
    assert parsed.blocks[0].text == "v"


def test_unknown_suffix_raises_unsupported_format() -> None:
    with pytest.raises(AppError) as excinfo:
        odf.parse(_zip({"content.xml": _odt("<text:p>x</text:p>")}), "doc.pdf")
    assert excinfo.value.code == ErrorCode.UNSUPPORTED_FORMAT
