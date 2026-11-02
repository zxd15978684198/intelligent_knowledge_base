"""N7 / ADR 0008 D3：电子表格解析器（`.xlsx` / `.xls` / `.csv`）。

Lead 按下面两个名字机械接线，本模块不碰 `parser.py`：

    SUFFIXES: frozenset[str]      # frozenset({".xlsx", ".xls", ".csv"})
    def parse(data: bytes, filename: str = "") -> ParsedText

## 产出规则（ADR 0008 D3）

- **每个工作表 / 每个 csv 一个** `Block(is_tabular=True)`，`page_no=None`，
  `locator=SheetLocator(sheet=<表名>, cells="<左上>:<右下>")`。一个 sheet 只出一个块：
  `SheetLocator` 没有 `rows` 字段，块内再切分只会让引用区间变碎，切分层仍能按块内偏移切。
- `text` 是制表符渲染：行内单元格用 `"\\t"` 连接、行间用 `"\\n"` 连接。**行尾空单元格
  截掉、块尾不留换行**；行首与行中的空单元格保留，否则列会错位（`B2` 起始的数据会渲染成
  `"\\t说明"`，这是对的）。
- `cells` 是**真实范围**：非空单元格的最小/最大行列。数据从 `B2` 开始就报 `B2:...`；
  被跳过的全空行仍然计入范围（它确实占了一行，压缩掉会让后续行号与 Excel 对不上）。
- 表格块**不进** `ParsedText.full_text`（D3-1），由 `full_text` 自己排除。

## 几处取舍（README/ADR 没写死的）

1. **空表返回空 `ParsedText`**（`blocks=[]`，不抛错）。但"0 字节的 `.xlsx`"不是空表而是
   坏文件（连 zip 头都没有），仍走 `PARSE_FAILED`。
2. 依赖**函数内 import**：xlsx 要 `openpyxl`、xls 要 `xlrd`，而两者都**不在**
   `pyproject.toml` 的依赖里。缺包必须变成可诊断的 `PARSE_FAILED`
   （`details["package"]` 指明缺谁），不能让 `ImportError` 裸奔到 Worker 被当成未知错误重试。
3. 合并单元格、字体颜色、图表、公式一律不看：`data_only=True` 只要值。合并单元格只在
   左上角那格有值，其余为空——与 Excel 的显示一致。
4. 规模上限走 `guards`：xlsx 解压前 `check_zip_size`（累计 ≤200 MiB），行数按**跨工作表
   累计的非空行**调 `check_document_limits(rows=...)`。两处都不在这里硬编码阈值。
5. `.xls` 的日期是浮点序列号（`XL_CELL_DATE`），用 `xlrd.xldate_as_datetime` 还原成
   `datetime` 再交给统一的 `_cell_text`；还原失败就按数字渲染，不丢整张表。
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import PurePosixPath
from typing import Any

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import SheetLocator
from pyknora.services.ingest.guards import check_document_limits, check_zip_size
from pyknora.services.ingest.parser import Block, ParsedText

#: 本模块负责的后缀。Lead 的 `parser.py` 按它分派，别在别处再抄一份。
#: 显式标注类型，与 `slides.py` / `odf.py` / `mail_rtf.py` 的写法保持一致。
SUFFIXES: frozenset[str] = frozenset({".xlsx", ".xls", ".csv"})

# 编码嗅探顺序，先 UTF-8 再国标，最后 latin-1（它永不失败，天然兜底）。
_CSV_ENCODINGS: tuple[str, ...] = ("utf-8", "gb18030", "latin-1")
# 分隔符候选；`,;	` 的顺序同时决定平票时的偏好（`,` 优先）。
_CSV_DELIMITERS: tuple[str, ...] = (",", ";", "\t")
# Excel 2007+ 的列上限（XFD）。超出就无法用 `SheetLocator` 的 3 字母坐标表达。
_MAX_COLUMNS = 16_384


# --------------------------------------------------------------------------- 单元格


def _column_letters(index: int) -> str:
    """1 起的列号 → Excel 列字母：1→A、26→Z、27→AA、703→AAA。"""
    if index < 1:
        raise ValueError(f"列号从 1 开始，收到 {index}")
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _cells_range(min_row: int, min_col: int, max_row: int, max_col: int) -> str:
    """拼 `B2:C4` 形态的区间；单格也给 `A1:A1`，与契约里 `左上:右下` 的写法保持一致。"""
    return f"{_column_letters(min_col)}{min_row}:{_column_letters(max_col)}{max_row}"


def _cell_text(value: Any) -> str:
    """单元格值 → 字符串。`None`→`""`、日期走 ISO、整数值浮点去掉 `.0`。"""
    if isinstance(value, str):
        # 只 strip 单个值，不动行/列结构：首尾空白不参与"这格有没有内容"的判断。
        return value.strip()
    if value is None:
        return ""
    if isinstance(value, datetime):
        # 零点算"纯日期"：Excel 里 2024-01-02 读回来是 datetime(2024,1,2,0,0)。
        return value.date().isoformat() if value.time() == time(0, 0) else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, float):
        # 1.0 → "1"，3.14 → "3.14"。float("nan"/"inf").is_integer() 是 False，不会被误转。
        return str(int(value)) if value.is_integer() else str(value)
    return str(value)


# --------------------------------------------------------------------------- 收集


@dataclass(slots=True)
class _RowBudget:
    """跨工作表累计的**非空行**数；越界由 guards 抛（阈值不在这里重复一遍）。

    只数留下来的行：`openpyxl` 的 read_only 模式会把只有格式、没有值的行也吐出来，
    把它们算进 5 000 行上限等于让"复制了一列格式"的表直接失败。
    """

    total: int = 0

    def consume(self) -> None:
        self.total += 1
        check_document_limits(rows=self.total)


def _sheet_name(raw: object, fallback: str) -> str:
    """`SheetLocator.sheet` 的 `min_length=1`：空名会给不合法 locator，必须兜底。"""
    name = str(raw).strip() if raw is not None else ""
    return name or fallback


def _build_block(
    sheet_name: str,
    numbered_rows: Iterable[tuple[int, list[str]]],
    budget: _RowBudget,
) -> Block | None:
    """把一个 sheet 的 `(行号, 单元格文本)` 流渲染成一个表格块；全空返回 `None`。"""
    rendered: list[str] = []
    min_row = max_row = 0
    min_col = max_col = 0
    for row_no, values in numbered_rows:
        trimmed = list(values)
        while trimmed and trimmed[-1] == "":
            trimmed.pop()
        first = 0
        while first < len(trimmed) and trimmed[first] == "":
            first += 1
        if first == len(trimmed):  # 全空行：不渲染、不计数
            continue
        budget.consume()
        # 行尾已截空，所以 last 就是最后一个有内容的列；保留行首/行中空串以对齐列。
        last = len(trimmed)
        if not rendered:
            min_row, min_col, max_col = row_no, first + 1, last
        else:
            min_col = min(min_col, first + 1)
            max_col = max(max_col, last)
        max_row = row_no
        rendered.append("\t".join(trimmed))
    if not rendered:
        return None
    if max_col > _MAX_COLUMNS:
        raise AppError(
            ErrorCode.PARSE_FAILED,
            f"表格列数 {max_col} 超过上限 {_MAX_COLUMNS}（最多 XFD 列）",
            details={"columns": max_col, "limit": _MAX_COLUMNS},
        )
    return Block(
        text="\n".join(rendered),
        page_no=None,
        locator=SheetLocator(
            sheet=sheet_name,
            cells=_cells_range(min_row, min_col, max_row, max_col),
        ),
        is_tabular=True,
    )


def _text_rows(rows: Iterable[tuple[Any, ...]]) -> Iterator[tuple[int, list[str]]]:
    """`(values_only=True)` 的行流 → `(1 起的行号, 文本单元格)`。"""
    for row_no, values in enumerate(rows, start=1):
        yield row_no, [_cell_text(value) for value in values]


# --------------------------------------------------------------------------- xlsx


def _parse_xlsx(data: bytes) -> ParsedText:
    """xlsx 走 openpyxl 的 read_only 流式读取，解压前先过 zip 体积闸。"""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # 依赖不在 pyproject 里，缺包是可能发生的
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "xlsx 解析需要 openpyxl 包（当前环境未安装）",
            details={"package": "openpyxl"},
        ) from exc

    # 先查体积再解压：openpyxl 会把 sheet XML 整个读进内存，ZIP 炸弹必须挡在它前面。
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            check_zip_size(archive)
    except zipfile.BadZipFile as exc:
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "XLSX 解析失败（不是有效的 zip 容器）",
            details={"reason": type(exc).__name__},
        ) from exc

    budget = _RowBudget()
    blocks: list[Block] = []
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            for position, sheet in enumerate(workbook.worksheets, start=1):
                block = _build_block(
                    _sheet_name(sheet.title, f"Sheet{position}"),
                    _text_rows(sheet.iter_rows(values_only=True)),
                    budget,
                )
                if block is not None:
                    blocks.append(block)
        finally:
            # read_only 模式必须 close，否则底层流与临时文件不释放。
            workbook.close()
    except AppError:  # ParseGuardError 是 AppError，不能被下面的宽 catch 吞成 PARSE_FAILED
        raise
    except Exception as exc:  # noqa: BLE001 — 库内异常类型随版本变化，按类名记录即可
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "XLSX 解析失败（文件损坏或不是有效的 xlsx）",
            details={"reason": type(exc).__name__},
        ) from exc
    return ParsedText(blocks=blocks, parser="openpyxl")


# --------------------------------------------------------------------------- xls


def _xlrd_cell_text(cell: Any, book: Any, xlrd_module: Any) -> str:
    """xlrd 单元格 → 文本；日期是序列号，先还原成 `datetime` 再交给 `_cell_text`。"""
    if cell.ctype == xlrd_module.XL_CELL_DATE:
        try:
            return _cell_text(xlrd_module.xldate_as_datetime(cell.value, book.datemode))
        except (ValueError, OverflowError, xlrd_module.XLDateError):
            # 越界/损坏的日期序列号按原数字渲染，不让一行坏数据毁掉整张表。
            return _cell_text(cell.value)
    return _cell_text(cell.value)


def _parse_xls(data: bytes) -> ParsedText:
    """老 .xls 走 xlrd 2.x（BIFF 只读；2.x 已不支持 xlsx，正好各管一段）。"""
    try:
        import xlrd
    except ImportError as exc:
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "xls 解析需要 xlrd 包（当前环境未安装）",
            details={"package": "xlrd"},
        ) from exc

    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as exc:  # noqa: BLE001 — xlrd 对坏文件抛的类型很杂（XLRDError 等）
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "XLS 解析失败（文件损坏或不是有效的 xls）",
            details={"reason": type(exc).__name__},
        ) from exc

    budget = _RowBudget()
    blocks: list[Block] = []
    try:
        for position, sheet in enumerate(book.sheets(), start=1):
            numbered = (
                (
                    row_index + 1,
                    [_xlrd_cell_text(cell, book, xlrd) for cell in sheet.row(row_index)],
                )
                for row_index in range(sheet.nrows)
            )
            block = _build_block(_sheet_name(sheet.name, f"Sheet{position}"), numbered, budget)
            if block is not None:
                blocks.append(block)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 — 同上
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "XLS 解析失败（表格内容无法读取）",
            details={"reason": type(exc).__name__},
        ) from exc
    finally:
        book.release_resources()
    return ParsedText(blocks=blocks, parser="xlrd")


# --------------------------------------------------------------------------- csv


def _decode_csv(data: bytes) -> str:
    """按 utf-8 → gb18030 → latin-1 嗅探，返回第一个"能解码且非空白"的结果。"""
    for encoding in _CSV_ENCODINGS:
        try:
            decoded = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        text = decoded.removeprefix("\ufeff")  # Excel 另存的 CSV 常带 BOM
        if text.strip():
            return text
    return ""


def _sniff_delimiter(text: str) -> str:
    """首行里出现次数最多的候选分隔符；一个都没有就按 `,` 处理。

    `max` 比较的是 `(计数, 下标)`：平票时下标小的赢，即 `,` → `;` → `\\t` 的顺序，
    所以 "a;b" 与 "a,b" 同时出现时仍走逗号这种最常见的形态。
    """
    first_line = text.split("\n", 1)[0]
    ranked = [(first_line.count(d), index, d) for index, d in enumerate(_CSV_DELIMITERS)]
    count, _, delimiter = max(ranked)
    return delimiter if count else ","


def _csv_sheet_name(filename: str) -> str:
    """csv 没有工作表，用文件名 stem 当 sheet 名；拿不到就 `csv`。"""
    flattened = filename.replace("\\", "/") if filename else ""
    return _sheet_name(PurePosixPath(flattened).stem, "csv")


def _parse_csv(data: bytes, filename: str) -> ParsedText:
    """标准库 `csv`：`csv.reader` 处理引号内换行，**不能用 `splitlines()`**。"""
    text = _decode_csv(data)
    if not text:
        return ParsedText(blocks=[], parser="csv")
    budget = _RowBudget()
    # `newline=""` 关掉通用换行翻译，引号内的 `\n` / `\r\n` 才不会被提前切断。
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_sniff_delimiter(text))
    try:
        block = _build_block(_csv_sheet_name(filename), enumerate(reader, start=1), budget)
    except csv.Error as exc:
        # 超长字段、未闭合引号等：csv 模块只抛 csv.Error，转成可诊断的 PARSE_FAILED。
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "CSV 解析失败（引号或分隔符不合法）",
            details={"reason": type(exc).__name__},
        ) from exc
    return ParsedText(blocks=[block] if block is not None else [], parser="csv")


# --------------------------------------------------------------------------- 入口


def parse(data: bytes, filename: str = "") -> ParsedText:
    """按后缀分派。空表返回空 `ParsedText`；坏文件与缺依赖都抛 `PARSE_FAILED`。"""
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower() if filename else ""
    if suffix == ".xlsx":
        return _parse_xlsx(data)
    if suffix == ".xls":
        return _parse_xls(data)
    if suffix in {"", ".csv"}:
        # 空后缀按 csv 处理：本模块只有它可能没有扩展名（调用方传 filename="" 的路径）。
        return _parse_csv(data, filename)
    raise AppError(
        ErrorCode.PARSE_FAILED,
        f"表格解析器不支持的后缀：{suffix}",
        details={"supported": sorted(SUFFIXES)},
    )
