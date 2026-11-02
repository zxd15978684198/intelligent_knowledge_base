"""N7 / ADR 0008 D3：电子表格解析器（`parsers/tabular.py`）。

覆盖两层：

1. **解析逻辑**（离线可跑）：xlsx / xls 用**桩模块**顶替 `openpyxl` / `xlrd`，只保留
   `load_workbook` / `open_workbook` 的返回形状。这样行收集、真实范围、单元格格式化、
   跨表行数累计、zip 体积闸的调用顺序都能被真的执行到——本机装不上这两个包（`pyproject`
   里没有它们，且当前环境无外网），而"逻辑跑没跑过"不该赌在能不能联网上。
2. **真库往返**（`importorskip`）：`test_xlsx_real_openpyxl_roundtrip` 在装了 openpyxl 的
   环境里真建工作簿再解析，防止桩与真实库行为漂移。缺包时整条跳过，未验证项在交付报告里。

csv 一支不需要任何第三方包，全部实跑。
"""

from __future__ import annotations

import io
import sys
import types
import zipfile
from datetime import datetime
from typing import Any

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import SheetLocator
from pyknora.services.ingest.guards import ParseGuardError
from pyknora.services.ingest.parsers import tabular
from pyknora.services.ingest.parsers.tabular import SUFFIXES, parse

# --------------------------------------------------------------------------- 桩：openpyxl


class _FakeSheet:
    """只实现 `iter_rows(values_only=True)` 与 `title`。"""

    def __init__(self, title: str, rows: list[tuple[Any, ...]]) -> None:
        self.title = title
        self._rows = rows

    def iter_rows(self, values_only: bool = False) -> Any:
        assert values_only is True, "解析器必须用 values_only=True 取值，不是取 cell 对象"
        return iter(self._rows)


class _FakeWorkbook:
    def __init__(self, sheets: list[_FakeSheet]) -> None:
        self.worksheets = sheets
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _fake_openpyxl(monkeypatch: pytest.MonkeyPatch, sheets: list[_FakeSheet]) -> dict[str, Any]:
    """把 `openpyxl` 换成桩，返回记录调用参数的 dict（空 dict 即"没被调用过"）。"""
    module = types.ModuleType("openpyxl")
    calls: dict[str, Any] = {}
    workbook = _FakeWorkbook(sheets)

    def load_workbook(source: Any, *, read_only: bool = False, data_only: bool = False) -> Any:
        calls.update(
            source=source,
            read_only=read_only,
            data_only=data_only,
            workbook=workbook,
        )
        return workbook

    module.load_workbook = load_workbook  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openpyxl", module)
    return calls


def _zip_bytes() -> bytes:
    """真 zip：`check_zip_size` 只认真的 `zipfile.ZipFile`，桩过不了那一关。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return buffer.getvalue()


# --------------------------------------------------------------------------- 桩：xlrd


class _XLDateError(ValueError):
    """替身：xlrd 的 `XLDateError` 也是 ValueError 的子类。"""


class _FakeCell:
    def __init__(self, ctype: int, value: Any) -> None:
        self.ctype = ctype
        self.value = value


class _FakeXlrdSheet:
    def __init__(self, name: str, rows: list[list[_FakeCell]]) -> None:
        self.name = name
        self._rows = rows
        self.nrows = len(rows)
        self.ncols = max((len(row) for row in rows), default=0)

    def row(self, index: int) -> list[_FakeCell]:
        return self._rows[index]


class _FakeXlrdBook:
    datemode = 0

    def __init__(self, sheets: list[_FakeXlrdSheet]) -> None:
        self._sheets = sheets
        self.released = False

    def sheets(self) -> list[_FakeXlrdSheet]:
        return list(self._sheets)

    def release_resources(self) -> None:
        self.released = True


def _fake_xlrd(monkeypatch: pytest.MonkeyPatch, sheets: list[_FakeXlrdSheet]) -> dict[str, Any]:
    module = types.ModuleType("xlrd")
    module.XL_CELL_EMPTY = 0  # type: ignore[attr-defined]
    module.XL_CELL_TEXT = 1  # type: ignore[attr-defined]
    module.XL_CELL_NUMBER = 2  # type: ignore[attr-defined]
    module.XL_CELL_DATE = 3  # type: ignore[attr-defined]
    module.XLDateError = _XLDateError  # type: ignore[attr-defined]
    calls: dict[str, Any] = {}
    book = _FakeXlrdBook(sheets)

    def open_workbook(file_contents: bytes | None = None) -> Any:
        calls.update(file_contents=file_contents, book=book)
        return book

    def xldate_as_datetime(value: float, datemode: int) -> datetime:
        # 45000 是 Excel 序列号里 2024-01-02 的近似值；桩只负责证明"日期走了转换"。
        assert datemode == 0
        return datetime(2024, 1, 2) if value >= 45000 else datetime(2023, 1, 1)

    module.open_workbook = open_workbook  # type: ignore[attr-defined]
    module.xldate_as_datetime = xldate_as_datetime  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "xlrd", module)
    return calls


# --------------------------------------------------------------------------- 契约


def test_suffixes_is_frozenset_of_three() -> None:
    assert isinstance(SUFFIXES, frozenset)
    # 顺序照 ruff SIM300 的建议（左常量右变量），断言语义不变。
    assert frozenset({".xlsx", ".xls", ".csv"}) == SUFFIXES


def test_tabular_blocks_are_excluded_from_full_text() -> None:
    """D3-1：表格块不进 `full_text`，否则切分层的偏移反算会全错位。"""
    parsed = parse(b"a,b\n1,2\n", "t.csv")
    assert parsed.blocks[0].is_tabular is True
    assert parsed.full_text == ""


# --------------------------------------------------------------------------- xlsx


def test_xlsx_two_sheets_each_one_block(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_openpyxl(
        monkeypatch,
        [
            _FakeSheet("Sales", [("区域", "金额"), ("华东", 1200)]),
            # 首行留空：数据从 B2 起，用来验"行首空单元格不影响真实范围"。
            _FakeSheet("Notes", [(), (None, "说明"), (None, None, "结束")]),
        ],
    )
    parsed = parse(_zip_bytes(), "book.xlsx")

    assert parsed.parser == "openpyxl"
    assert calls["read_only"] is True, "read_only 必须开，否则整本工作簿会全量进内存"
    assert calls["data_only"] is True, "data_only 必须开，公式要取缓存值不是公式串"
    assert calls["workbook"].closed is True, "read_only 模式不 close 会泄漏流"

    assert len(parsed.blocks) == 2
    assert all(block.is_tabular and block.page_no is None for block in parsed.blocks)

    sales, notes = parsed.blocks
    assert isinstance(sales.locator, SheetLocator)
    assert isinstance(notes.locator, SheetLocator)
    assert (sales.locator.sheet, sales.locator.cells) == ("Sales", "A1:B2")
    assert sales.text == "区域\t金额\n华东\t1200"
    # 数据从 B2 起：行首空单元格必须保留，否则列会错位。
    assert (notes.locator.sheet, notes.locator.cells) == ("Notes", "B2:C3")
    assert notes.text == "\t说明\n\t\t结束"


def test_xlsx_skips_blank_rows_but_keeps_real_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """跳过全空行，但它仍占一行：范围是 A1:B3，不是 A1:B2。"""
    _fake_openpyxl(monkeypatch, [_FakeSheet("Gaps", [("a",), (), (None, "b")])])
    (block,) = parse(_zip_bytes(), "gaps.xlsx").blocks
    assert isinstance(block.locator, SheetLocator)
    assert block.locator.cells == "A1:B3"
    assert block.text == "a\n\tb"


def test_xlsx_cell_values_are_rendered_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """`None`→空串、整数值浮点去 `.0`、`datetime` 走 ISO（零点只留日期）。"""
    _fake_openpyxl(
        monkeypatch,
        [
            _FakeSheet(
                "Types",
                [
                    ("x", None, "y"),
                    (1.0,),
                    (datetime(2024, 1, 2),),
                    (datetime(2024, 1, 2, 3, 4, 5),),
                ],
            )
        ],
    )
    (block,) = parse(_zip_bytes(), "types.xlsx").blocks
    assert block.text == "x\t\ty\n1\n2024-01-02\n2024-01-02 03:04:05"
    assert isinstance(block.locator, SheetLocator)
    assert block.locator.cells == "A1:C4"


def test_xlsx_column_letters_beyond_z(monkeypatch: pytest.MonkeyPatch) -> None:
    """第 27 列是 AA，不是 `[`、`A0` 之类的东西。"""
    row: tuple[Any, ...] = ("a", *([None] * 25), "b")
    _fake_openpyxl(monkeypatch, [_FakeSheet("Wide", [row])])
    (block,) = parse(_zip_bytes(), "wide.xlsx").blocks
    assert isinstance(block.locator, SheetLocator)
    assert block.locator.cells == "A1:AA1"
    # A 与 AA 之间有 25 列，分隔符因此是 26 个。
    assert block.text == "a" + "\t" * 26 + "b"


def test_xlsx_all_empty_sheets_yield_no_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_openpyxl(monkeypatch, [_FakeSheet("Empty", [(), (None, None)])])
    parsed = parse(_zip_bytes(), "empty.xlsx")
    assert parsed.blocks == []
    assert parsed.full_text == ""


def test_xlsx_row_limit_enforced_by_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """5 000 行上限由 guards 抛，且必须能穿透解析器的宽 catch。"""
    _fake_openpyxl(monkeypatch, [_FakeSheet("Huge", [("x",)] * 5001)])
    with pytest.raises(AppError) as excinfo:
        parse(_zip_bytes(), "huge.xlsx")
    assert excinfo.value.code == ErrorCode.PARSE_FAILED
    assert excinfo.value.details["metric"] == "rows"


def test_xlsx_row_limit_counts_across_sheets(monkeypatch: pytest.MonkeyPatch) -> None:
    """上限是**单文档**的：两个各 3 000 行的 sheet 一样超。"""
    _fake_openpyxl(
        monkeypatch,
        [_FakeSheet("A", [("x",)] * 3000), _FakeSheet("B", [("x",)] * 2001)],
    )
    with pytest.raises(AppError) as excinfo:
        parse(_zip_bytes(), "two.xlsx")
    assert excinfo.value.details["metric"] == "rows"
    assert excinfo.value.details["actual"] == 5001


def test_xlsx_non_zip_bytes_raise_parse_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_openpyxl(monkeypatch, [])
    with pytest.raises(AppError) as excinfo:
        parse(b"not a zip at all", "broken.xlsx")
    assert excinfo.value.code == ErrorCode.PARSE_FAILED
    assert excinfo.value.details["reason"] == "BadZipFile"


def test_xlsx_zip_guard_runs_before_load_workbook(monkeypatch: pytest.MonkeyPatch) -> None:
    """体积闸必须在解压（load_workbook）之前，且异常不能被吞掉。

    不真造 200 MiB 归档（那要先把 200 MiB 写进内存，与 `test_parse_guards.py` 的取舍一致），
    这里只看"调用顺序 + 短路"：闸一抛，`load_workbook` 就不许被调用。
    """
    calls = _fake_openpyxl(monkeypatch, [_FakeSheet("A", [("x",)])])
    seen: list[int] = []

    def _boom(archive: zipfile.ZipFile) -> None:
        seen.append(len(archive.infolist()))
        raise ParseGuardError(
            "解压后累计体积超限",
            metric="uncompressed_bytes",
            limit=1,
            actual=2,
        )

    monkeypatch.setattr(tabular, "check_zip_size", _boom)
    with pytest.raises(ParseGuardError):
        parse(_zip_bytes(), "book.xlsx")
    assert seen == [1], "check_zip_size 必须拿到真实归档"
    assert calls == {}, "体积闸没过就不该解压"


def test_xlsx_missing_openpyxl_raises_parse_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`sys.modules[name] = None` 等价于"包不在环境里"，`from ... import` 会抛 ImportError。"""
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    with pytest.raises(AppError) as excinfo:
        parse(b"whatever", "book.xlsx")
    assert excinfo.value.code == ErrorCode.PARSE_FAILED
    assert excinfo.value.code == "PARSE_FAILED"
    assert excinfo.value.details["package"] == "openpyxl"
    assert "openpyxl" in excinfo.value.message
    assert excinfo.value.retryable is False, "缺依赖不是可重试错误，重试还是缺"


# --------------------------------------------------------------------------- xls


def test_xls_sheets_dates_and_release(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_xlrd(
        monkeypatch,
        [
            _FakeXlrdSheet(
                "数据",
                [
                    [_FakeCell(1, "区域"), _FakeCell(1, "金额")],
                    [_FakeCell(1, "华东"), _FakeCell(2, 1200.0)],
                    [_FakeCell(3, 45000.0), _FakeCell(0, "")],
                ],
            )
        ],
    )
    parsed = parse(b"\xd0\xcf\x11\xe0", "legacy.xls")

    assert parsed.parser == "xlrd"
    assert calls["file_contents"] == b"\xd0\xcf\x11\xe0"
    assert calls["book"].released is True, "xlrd 解析完必须 release_resources"

    (block,) = parsed.blocks
    assert isinstance(block.locator, SheetLocator)
    assert (block.locator.sheet, block.locator.cells) == ("数据", "A1:B3")
    # 日期序列号还原成 ISO，不是 "45000"；空单元格不把整行变空行。
    assert block.text == "区域\t金额\n华东\t1200\n2024-01-02"


def test_xls_missing_xlrd_raises_parse_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "xlrd", None)
    with pytest.raises(AppError) as excinfo:
        parse(b"\xd0\xcf\x11\xe0", "legacy.xls")
    assert excinfo.value.code == ErrorCode.PARSE_FAILED
    assert excinfo.value.details["package"] == "xlrd"
    assert "xlrd" in excinfo.value.message


# --------------------------------------------------------------------------- csv


def test_csv_quoted_newline_stays_in_one_cell() -> None:
    """引号内换行：2 条记录（不是 3 行），`splitlines()` 写法会在这里露馅。"""
    parsed = parse(b'name,note\n"first\nsecond",x\n', "quoted.csv")
    (block,) = parsed.blocks
    assert isinstance(block.locator, SheetLocator)
    assert block.locator.cells == "A1:B2"
    assert block.text == "name\tnote\nfirst\nsecond\tx"
    assert block.text.count("\n") == 2


def test_csv_gb18030_decoded() -> None:
    data = "姓名,备注\n张三,中文\n".encode("gb18030")
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")  # 前提：UTF-8 确实解不开，才轮到 gb18030

    (block,) = parse(data, "people.csv").blocks
    assert block.locator.sheet == "people"
    assert block.text == "姓名\t备注\n张三\t中文"


def test_csv_latin1_fallback() -> None:
    """`é` 单字节：UTF-8 与 gb18030 都解不开，最后靠 latin-1 兜住。"""
    data = b"caf\xe9,1\n"
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    with pytest.raises(UnicodeDecodeError):
        data.decode("gb18030")

    (block,) = parse(data, "menu.csv").blocks
    assert block.text == "café\t1"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"a;b;c\n1;2;3\n", "a\tb\tc\n1\t2\t3"),
        (b"a\tb\n1\t2\n", "a\tb\n1\t2"),
        (b"a,b\n1,2\n", "a\tb\n1\t2"),
    ],
)
def test_csv_delimiter_sniffed(payload: bytes, expected: str) -> None:
    (block,) = parse(payload, "sniff.csv").blocks
    assert block.text == expected


def test_csv_single_column_falls_back_to_comma() -> None:
    """一个候选分隔符都没有时不能把整行当一个字段以外的东西——退回逗号即可。"""
    (block,) = parse(b"a\nb\nc\n", "one.csv").blocks
    assert block.text == "a\nb\nc"
    assert isinstance(block.locator, SheetLocator)
    assert block.locator.cells == "A1:A3"


@pytest.mark.parametrize(
    ("filename", "sheet"),
    [
        ("Report.CSV", "Report"),
        ("data/report.csv", "report"),
        (r"C:\inbox\report.csv", "report"),
        ("", "csv"),
    ],
)
def test_csv_sheet_name_from_filename_stem(filename: str, sheet: str) -> None:
    (block,) = parse(b"x,y\n", filename).blocks
    assert isinstance(block.locator, SheetLocator)
    assert block.locator.sheet == sheet


@pytest.mark.parametrize(("payload", "filename"), [(b"", "empty.csv"), (b"\n\n\n", "blank.csv")])
def test_csv_empty_content_yields_no_blocks(payload: bytes, filename: str) -> None:
    parsed = parse(payload, filename)
    assert parsed.blocks == []
    assert parsed.full_text == ""


def test_csv_bom_is_stripped() -> None:
    """Excel 另存的 CSV 常带 UTF-8 BOM，不剥掉第一个表头就会变成 `\\ufeff姓名`。"""
    (block,) = parse("\ufeff姓名,备注\n张三,中文\n".encode(), "bom.csv").blocks
    assert block.text.startswith("姓名\t")


# --------------------------------------------------------------------------- 分派


def test_unsupported_suffix_raises_parse_failed() -> None:
    """本模块只认三个后缀；接错线的 AC 要立刻可见，不能悄悄当 csv 解析。"""
    with pytest.raises(AppError) as excinfo:
        parse(b"", "document.docx")
    assert excinfo.value.code == ErrorCode.PARSE_FAILED
    assert ".xlsx" in excinfo.value.details["supported"]


# --------------------------------------------------------------------------- 真库往返


def test_xlsx_real_openpyxl_roundtrip() -> None:
    """真 openpyxl 建簿 → 解析。缺包（未进 pyproject 且离线）时整条跳过。"""
    openpyxl = pytest.importorskip(
        "openpyxl",
        reason="需要 openpyxl 真库：未进 pyproject，当前环境无外网装上",
    )
    workbook = openpyxl.Workbook()
    sales = workbook.active
    sales.title = "Sales"
    sales.append(["区域", "金额"])
    sales.append(["华东", 1200])
    notes = workbook.create_sheet("Notes")
    notes["B2"] = "说明"
    notes["C3"] = datetime(2024, 1, 2)
    buffer = io.BytesIO()
    workbook.save(buffer)

    parsed = parse(buffer.getvalue(), "book.xlsx")
    assert parsed.parser == "openpyxl"
    assert [(block.locator.sheet, block.locator.cells) for block in parsed.blocks] == [
        ("Sales", "A1:B2"),
        ("Notes", "B2:C3"),
    ]
    assert parsed.blocks[0].text == "区域\t金额\n华东\t1200"
    assert parsed.blocks[1].text == "\t说明\n\t\t2024-01-02"
