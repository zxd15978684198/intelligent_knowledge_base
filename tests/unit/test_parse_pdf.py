"""N13：PDF 解析器（ADR 0008 D5 / D5-1，pdfplumber 后端）。

样张**现场生成**（手写 PDF 字节 + 正确 xref 偏移），仓库里不放二进制夹具——理由同
``test_parse_slides.py``：``.pdf`` 进 git 后 diff 不可读，也容易被"改样张不改断言"污染。

口径（与 ``services/ingest/parsers/pdf.py`` 的模块 docstring 一一对应）：

- **一份文档只 ``pdfplumber.open()`` 一次**：D5-1 实测，文本与表格分开 open 会让 S2
  从 3.630s 涨到 11.267s（12.8×）。用例 monkeypatch ``pdfplumber.open`` 数调用次数。
- **分批**：``> 100`` 页按 100 页一批、``>= 150`` 页强制分批；每页处理完 ``page.close()``。
  纯函数 ``_page_batches`` / ``_should_batch`` 直测，再用 101 页样张真跑一遍。
- **表格准入四条**（``_table_rejection`` 逐条测）：不达标的表**当普通文本**，
  内容留在文本块里，一个字不丢。
- **表格块只出一次**：准入通过的表覆盖的文本行从文本流剔除，且不另出纯文本块，
  否则同一份数据进检索两次。
- **扫描件**：``details["reason"] == "no_text_layer"`` 是 N14 触发 OCR 的信号，不许改。
- **locator**：文本块 ``PageLocator``（``page`` 1 基 + 所属 ``heading``），
  表格块 ``TableLocator``（``index`` 全文 1 基 + ``rows="R1:Rn"``）。
"""

from __future__ import annotations

import io
import re
from typing import Any

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import PageLocator
from pyknora.services.ingest.parsers import pdf as pdf_parser
from pyknora.services.ingest.parsers.pdf import SUFFIXES, parse

PAGE_WIDTH, PAGE_HEIGHT = 595, 842
TITLE = "Quarterly Report"
BODY_LINE = "The finance team reviews every voucher before it is filed."
MARKER = "RESERVED-MARKER"  #: 只出现在表格里，用来证明表格块不进 `full_text`

# --------------------------------------------------------------------------- 样张


def _escape(text: str) -> str:
    """PDF 字符串转义：反斜杠与两种括号。"""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _text(text: str, x: float, y: float, size: float = 12.0) -> str:
    """一行文本的绘制指令（基线在 `y`）。"""
    return f"BT /F1 {size} Tf {x} {y} Td ({_escape(text)}) Tj ET"


def _grid(
    left: float,
    bottom: float,
    right: float,
    top: float,
    columns: int,
    rows: int,
) -> str:
    """画一张有框的表格：横竖各若干条线。pdfplumber 的默认策略认这种显式线。"""
    ops = ["0.7 w"]
    for step in range(rows + 1):
        y = bottom + (top - bottom) * step / rows
        ops.append(f"{left} {y:.2f} m {right} {y:.2f} l S")
    for step in range(columns + 1):
        x = left + (right - left) * step / columns
        ops.append(f"{x:.2f} {bottom} m {x:.2f} {top} l S")
    return "\n".join(ops)


def _cell_rows(
    left: float,
    top: float,
    column_width: float,
    row_height: float,
    matrix: list[list[str]],
) -> str:
    """把文本放进单元格：第 0 行在表格最上面，与 `find_tables()` 的行序一致。"""
    ops = []
    for row_index, row in enumerate(matrix):
        baseline = top - row_index * row_height - row_height + 12
        for column_index, cell in enumerate(row):
            if cell:
                x = left + column_index * column_width + 6
                ops.append(_text(cell, x, baseline, size=10.0))
    return "\n".join(ops)


def _build_pdf(pages: list[str]) -> bytes:
    """把若干页内容流拼成一份最小合法 PDF（经典 xref 表 + 正确字节偏移）。"""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(len(pages)))
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    for index, content in enumerate(pages):
        raw = content.encode("latin-1")
        page_id = 4 + 2 * index
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(raw)).encode() + b" >>\nstream\n" + raw + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _text_page() -> str:
    """一页正文：20pt 标题 + 6 行 12pt 正文（行距固定，段内不空行）。"""
    ops = [_text(TITLE, 72, 770, size=20.0)]
    ops += [_text(BODY_LINE, 72, 720 - 18 * index, size=12.0) for index in range(6)]
    return "\n".join(ops)


def _body_page() -> str:
    """只有正文、没有标题的一页：用来观察标题跨页沿用。"""
    return "\n".join(_text(BODY_LINE, 72, 720 - 18 * index, size=12.0) for index in range(6))


def _table_page() -> str:
    """一页两张表 + 表上方一段正文。两张表分别是 4 行 × 3 列、3 行 × 2 列。"""
    matrix_one = [
        ["Code", "Name", "Score"],
        ["A1", "Alice", "90"],
        ["B2", "Bob", "80"],
        ["C3", MARKER, "70"],
    ]
    matrix_two = [["Key", "Value"], ["k", "v"], ["k2", "v2"]]
    ops = [_text(BODY_LINE, 72, 760, size=12.0)]
    # 列宽 170：`MARKER` 长 11 字符（10pt ≈ 61pt 宽），必须留足余量，
    # 否则 pdfplumber 会按 x 坐标把文字劈到相邻列（实测 `RESERVED-MARKE` + `R70`）。
    ops.append(_grid(40, 560, 550, 720, columns=3, rows=4))
    ops.append(_cell_rows(40, 720, 170, 40, matrix_one))
    ops.append(_grid(40, 360, 240, 480, columns=2, rows=3))
    ops.append(_cell_rows(40, 480, 100, 40, matrix_two))
    return "\n".join(ops)


def _rejected_page() -> str:
    """一张 3 列 × 2 行的有框表，但表头第一格是空的 —— 准入被拒，文字必须留在正文里。"""
    matrix = [["", "Kept", "Visible"], ["1", "2", "3"]]
    ops = [_grid(72, 560, 372, 640, columns=3, rows=2)]
    ops.append(_cell_rows(72, 640, 100, 40, matrix))
    return "\n".join(ops)


def _blank_page() -> str:
    """扫描件：一页只有一条线，没有任何文本层。"""
    return "0.7 w 72 72 m 500 700 l S"


# --------------------------------------------------------------------------- 计数工具


def _counting_open(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """把 `pdfplumber.open` 包一层，返回"调用次数"的容器（单元素列表）。"""
    pdfplumber = pytest.importorskip("pdfplumber")
    calls: list[int] = [0]
    original = pdfplumber.open

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pdfplumber, "open", counting)
    return calls


def _counting_page_close(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """统计 `Page.close()` 被调了几次（分批路径"释放 page"的唯一可观测证据）。"""
    pdfplumber = pytest.importorskip("pdfplumber")
    page_cls = type(pdfplumber.open(io.BytesIO(_build_pdf([_text_page()]))).pages[0])
    calls: list[int] = [0]
    original = page_cls.close

    def counting(self: Any) -> Any:
        calls[0] += 1
        return original(self)

    monkeypatch.setattr(page_cls, "close", counting)
    return calls


# --------------------------------------------------------------------------- 契约


def test_suffixes_and_parser_name() -> None:
    assert frozenset({".pdf"}) == SUFFIXES
    assert pdf_parser.PARSER_NAME == "pdfplumber"


def test_opens_document_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """D5-1 硬约束：一份文档只 open 一次（文本与表格走同一个 Page 对象）。"""
    calls = _counting_open(monkeypatch)
    parsed = parse(_build_pdf([_text_page(), _text_page()]), "report.pdf")
    assert calls[0] == 1
    assert parsed.parser == "pdfplumber"


def test_opens_once_even_with_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """有表格的文档也只能 open 一次：重复 open 是 12.8× 的坑。"""
    calls = _counting_open(monkeypatch)
    parse(_build_pdf([_table_page()]), "table.pdf")
    assert calls[0] == 1


# --------------------------------------------------------------------------- 文本块


def test_text_blocks_pages_and_headings() -> None:
    parsed = parse(_build_pdf([_text_page(), _body_page()]), "report.pdf")
    assert parsed.blocks, "正文 PDF 不该产出空块"
    assert all(not block.is_tabular for block in parsed.blocks)

    # 第 1 页：标题块 + 正文块；第 2 页：正文块（标题跨页沿用）。
    pages = [block.page_no for block in parsed.blocks]
    assert pages == sorted(pages), "块顺序必须按页递增"
    assert min(pages) == 1 and max(pages) == 2, "page_no 是 1 基"

    title_blocks = [block for block in parsed.blocks if block.text == TITLE]
    assert len(title_blocks) == 1
    assert title_blocks[0].page_no == 1

    # 每块都是 PageLocator，且 locator.heading 与 heading_path 同口径。
    for block in parsed.blocks:
        assert isinstance(block.locator, PageLocator)
        assert block.locator.page == block.page_no
        assert block.locator.heading == block.heading_path

    # 标题之后（含跨页）的正文块都挂在标题下。
    body = [block for block in parsed.blocks if block.text != TITLE]
    assert body and all(block.heading_path == TITLE for block in body)
    assert any(BODY_LINE in block.text for block in body)


def test_table_blocks_are_tabular_and_use_table_locator() -> None:
    parsed = parse(_build_pdf([_table_page()]), "table.pdf")
    tables = [block for block in parsed.blocks if block.is_tabular]
    assert len(tables) == 2, "同页两张表各出一块"

    first, second = tables
    assert first.locator.kind == "table"
    assert [first.locator.index, second.locator.index] == [1, 2], "序号是全文 1 基"
    assert first.locator.rows == "R1:R4"
    assert second.locator.rows == "R1:R3"
    assert first.text.splitlines()[0] == "Code\tName\tScore"
    assert first.text.splitlines()[-1] == "C3\tRESERVED-MARKER\t70"
    assert second.text == "Key\tValue\nk\tv\nk2\tv2"

    # 原先这里断言的是"契约缺口存在"（`TableLocator` 无 `page` 字段）。2026-11-02
    # Lead 按本节点上报补了 `page: int | None`，缺口的断言就该反过来——表格块必须
    # 自己带页号，不能只靠 `Block.page_no`（`compact_locator` 只读 locator）。
    assert first.locator.page == 1
    assert second.locator.page == 1
    assert first.page_no == 1


def test_table_block_is_emitted_once(tmp_path: Any) -> None:
    """表格块只出一次：表格覆盖的文本行不出现在任何文本块里（不重复计入检索）。"""
    parsed = parse(_build_pdf([_table_page()]), "table.pdf")
    texts = [block.text for block in parsed.blocks if not block.is_tabular]
    joined = "\n".join(texts)
    for table in (block for block in parsed.blocks if block.is_tabular):
        for row in table.text.splitlines():
            assert row not in joined, f"表格行重复进了文本流：{row}"
    assert BODY_LINE in joined, "表格之外的正文必须保留"
    assert MARKER not in joined, "表格内容不该在文本流里再出现一次"


def test_rejected_table_falls_back_to_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """准入不通过的表当普通文本：不产出表格块，但内容一个字都不许丢。"""
    reasons: list[str] = []
    original = pdf_parser._table_rejection

    def recording(rows: list[list[str]]) -> str | None:
        reason = original(rows)
        reasons.append(reason or "admitted")
        return reason

    monkeypatch.setattr(pdf_parser, "_table_rejection", recording)
    parsed = parse(_build_pdf([_rejected_page()]), "rejected.pdf")

    assert reasons == ["empty_header_cell"], "这张表必须真的被检出且被准入规则拦下"
    assert not any(block.is_tabular for block in parsed.blocks)
    joined = "\n".join(block.text for block in parsed.blocks)
    for cell in ("Kept", "Visible", "1", "2", "3"):
        assert cell in joined


def test_page_limit_guard_is_wired() -> None:
    """规模闸（D6）：超页数在解析前就被 `guards` 拦下，错误码仍是 `PARSE_FAILED`。"""
    from pyknora.services.ingest.guards import MAX_PAGES, ParseGuardError

    with pytest.raises(ParseGuardError) as caught:
        parse(_build_pdf([""] * (MAX_PAGES + 1)), "huge.pdf")
    assert caught.value.code is ErrorCode.PARSE_FAILED


# --------------------------------------------------------------------------- 表格准入


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([["A", "B"]], "too_few_rows"),
        ([["A"], ["1"]], "too_few_columns"),
        ([["c"] * 13, ["v"] * 13], "too_many_columns"),
        ([["A", ""], ["1", "2"]], "empty_header_cell"),
        ([["A" * 21, "B"], ["1", "2"]], "header_too_long"),
        ([["A", "B"], ["1", "x" * 201]], "cell_too_long"),
        ([["A", "B"], ["1", "2"]], None),
        ([["c"] * 12, ["v"] * 12], None),
    ],
)
def test_table_admission_rules(rows: list[list[str]], reason: str | None) -> None:
    """准入四条逐条：行数、列数下限、列数上限、表头空格/超长、单元格超长。"""
    assert pdf_parser._table_rejection(rows) == reason


def test_render_table_trims_trailing_empty_cells() -> None:
    """行尾空单元格截掉、行首与行中保留（与 `tabular.py` 同口径）；全空行跳过。"""
    rendered = pdf_parser._render_table(
        [["A", "B", ""], ["", "B", "C"], ["", "", ""], ["A", "", "C"]]
    )
    assert rendered == ["A\tB", "\tB\tC", "A\t\tC"]


def test_cell_text_collapses_whitespace() -> None:
    assert pdf_parser._cell_text(None) == ""
    assert pdf_parser._cell_text("  A \n B  ") == "A B"


# --------------------------------------------------------------------------- 错误出口


def test_no_text_layer_signal() -> None:
    """扫描件契约：`reason="no_text_layer"` 是 N14 触发 OCR 的信号，原样保留。"""
    with pytest.raises(AppError) as caught:
        parse(_build_pdf([_blank_page()]), "scan.pdf")
    error = caught.value
    assert error.code is ErrorCode.PARSE_FAILED
    assert error.details is not None
    assert error.details["reason"] == "no_text_layer"
    assert error.details["pages"] == 1
    assert "扫描件" in error.message


def test_encrypted_pdf_reports_reason() -> None:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(io.BytesIO(_build_pdf([_text_page()])))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(AppError) as caught:
        parse(buffer.getvalue(), "locked.pdf")
    assert caught.value.code is ErrorCode.PARSE_FAILED
    assert caught.value.details is not None
    assert caught.value.details["reason"] == "encrypted"


def test_truncated_pdf_reports_broken() -> None:
    with pytest.raises(AppError) as caught:
        parse(b"%PDF-1.4\nthis is not a real pdf\n", "broken.pdf")
    assert caught.value.code is ErrorCode.PARSE_FAILED
    assert caught.value.details is not None
    assert caught.value.details["reason"] == "broken"


def test_empty_bytes_reports_empty() -> None:
    with pytest.raises(AppError) as caught:
        parse(b"", "empty.pdf")
    assert caught.value.details is not None
    assert caught.value.details["reason"] == "empty"


def test_no_error_codes_invented() -> None:
    """只用既有错误码：本模块只抛 `PARSE_FAILED`（`ErrorCode` 是 str 常量，不是 Enum）。"""
    assert ErrorCode.PARSE_FAILED == "PARSE_FAILED"


# --------------------------------------------------------------------------- 分批


def test_should_batch_thresholds() -> None:
    """`> 100` 页按批处理，`>= 150` 页是 D5-1 的红线。"""
    assert pdf_parser._should_batch(40) is False
    assert pdf_parser._should_batch(100) is False
    assert pdf_parser._should_batch(101) is True
    assert pdf_parser._should_batch(150) is True
    assert pdf_parser._should_batch(1_032) is True


def test_page_batches_are_hundred_sized() -> None:
    assert list(pdf_parser._page_batches(250)) == [
        range(0, 100),
        range(100, 200),
        range(200, 250),
    ]


def test_batch_path_closes_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """101 页真跑：走分批路径，每页处理完都 `close()` 掉（内存不累积）。"""
    closes = _counting_page_close(monkeypatch)
    page_count = 101
    pages = [_text(f"Page {index + 1}", 72, 760) for index in range(page_count)]
    parsed = parse(_build_pdf(pages), "big.pdf")

    assert sorted({block.page_no for block in parsed.blocks}) == list(range(1, page_count + 1))
    assert closes[0] >= page_count, "分批路径必须逐页 close"


# --------------------------------------------------------------------------- 行工具


def test_is_furniture_drops_small_margin_lines() -> None:
    line = pdf_parser._Line
    assert pdf_parser._is_furniture(line(text="Q/XZG", top=10, bottom=20, size=10), 14.0, 842)
    assert pdf_parser._is_furniture(line(text="12", top=800, bottom=810, size=14), 14.0, 842)
    # 页顶但字号大于正文：是文档大标题，不是页眉。
    assert not pdf_parser._is_furniture(line(text="Title", top=10, bottom=32, size=22), 12.0, 842)
    # 不在页眉页脚带里。
    assert not pdf_parser._is_furniture(line(text="body", top=400, bottom=412, size=9), 12.0, 842)


def test_paragraph_gap_splits_blocks() -> None:
    """段间距 > 基线行距 ×1.6 时切块：两段之间留一个大空隙，必须成两块。"""
    ops = [
        _text("first paragraph line one", 72, 700),
        _text("first paragraph line two", 72, 682),
        _text("second paragraph", 72, 600),
    ]
    parsed = parse(_build_pdf(["\n".join(ops)]), "paragraphs.pdf")
    assert len(parsed.blocks) == 2
    assert parsed.blocks[0].text.count("\n") == 1
    assert re.search("second paragraph", parsed.blocks[1].text)
