r"""N9：OpenDocument 家族（`.odt` / `.ods` / `.odp`）解析器。

**零新增依赖**：只吃标准库的 `zipfile` + `xml.etree.ElementTree`。ODF 本质是个 zip，
正文全在 `content.xml` 里；`mimetype`、`styles.xml`、`meta.xml` 一律不读——
样式不影响可索引文本，`manifest` 也不参与。

两个强制闸门由 `services/ingest/guards.py` 提供，顺序不能反：

1. `check_zip_size(zf)` —— 必须在**读 content.xml 之前**，它只看归档目录，
   挡的是「压缩后很小、解压后 200 MiB+」的 ZIP 炸弹；
2. `check_odf_repeated(xml)` —— 必须在 `ElementTree.fromstring` **之前**，
   它只看属性字面量，挡的是 `number-rows-repeated` 这类「一行声明撑出十万行」的炸弹。
   过闸之后重复值保证 ≤ `MAX_ODF_REPEATED`，所以后续展开是安全的。

**序号口径与需求文档的偏差（刻意，已核对 `schemas/locator.py`）**：需求写的是
`ParaLocator.index` / `TableLocator.index`「从 0 开始」，但
`pyknora/schemas/locator.py` 里两个字段都是 `Field(ge=1)`，传 0 会被 Pydantic
当 ValidationError 拒掉。本节点不许改 `locator.py`，所以这里统一**从 1 开始**，
与 `PageLocator.page` / `SlideLocator.slide`（本来就是 `ge=1`、从 1 开始）对齐。

表格块的 `locator.rows` 用 `R1:R<n>`：`TableLocator.rows` 的 pattern 是
`^R[1-9]\d*(:R[1-9]\d*)?$`，`R0` 会直接被拒，所以行号同样从 1 起。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import ParaLocator, SlideLocator, TableLocator
from pyknora.services.ingest.guards import (
    MAX_ODF_REPEATED,
    check_odf_repeated,
    check_zip_size,
)
from pyknora.services.ingest.parser import Block, ParsedText

#: 本解析器认领的后缀。与 `parser.SUPPORTED_SUFFIXES` 分开维护：接线是 Lead 的事。
SUFFIXES: frozenset[str] = frozenset({".odt", ".ods", ".odp"})

_NS_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_NS_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_NS_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
# 演示文稿的 `draw:page` 住在 **drawing** 命名空间，不是 presentation。
# `presentation:1.0` 只承载 `presentation:name`、`presentation:class` 这类属性。
_NS_DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"


def _tag(namespace: str, local: str) -> str:
    """拼 ElementTree 的 `{ns}local` 形式。"""
    return f"{{{namespace}}}{local}"


_OFFICE_BODY = _tag(_NS_OFFICE, "body")
_OFFICE_TEXT = _tag(_NS_OFFICE, "text")
_OFFICE_SPREADSHEET = _tag(_NS_OFFICE, "spreadsheet")
_OFFICE_PRESENTATION = _tag(_NS_OFFICE, "presentation")
_OFFICE_VALUE = _tag(_NS_OFFICE, "value")
_OFFICE_STRING_VALUE = _tag(_NS_OFFICE, "string-value")

_TEXT_P = _tag(_NS_TEXT, "p")
_TEXT_H = _tag(_NS_TEXT, "h")
_TEXT_S = _tag(_NS_TEXT, "s")
_TEXT_TAB = _tag(_NS_TEXT, "tab")
_TEXT_LINE_BREAK = _tag(_NS_TEXT, "line-break")
_TEXT_C = _tag(_NS_TEXT, "c")

_TABLE_TABLE = _tag(_NS_TABLE, "table")
_TABLE_ROW = _tag(_NS_TABLE, "table-row")
_TABLE_CELL = _tag(_NS_TABLE, "table-cell")
_TABLE_COVERED_CELL = _tag(_NS_TABLE, "covered-table-cell")
_TABLE_ROWS_REPEATED = _tag(_NS_TABLE, "number-rows-repeated")
_TABLE_COLS_REPEATED = _tag(_NS_TABLE, "number-columns-repeated")

_DRAW_PAGE = _tag(_NS_DRAW, "page")

#: `text:s` 的 `text:c` 属性本身不在 `check_odf_repeated` 的覆盖面里，
#: 一个 `<text:s text:c="99999999"/>` 能直接撑出百 MB 空白，所以这里另设硬上限。
_MAX_INLINE_SPACES = 256

#: 单张表最多展开的单元格数。`number-rows-repeated` 与 `number-columns-repeated`
#: 各自都合法（≤ 10 000）却相乘出 10^8 个单元格——两个闸门都拦不住这个组合，
#: 所以展开循环里再兜一层：约 5 000 行 × 40 列，超过就停，先保住内存。
_MAX_TABLE_CELLS = 200_000

#: `_repeat` 里 `int()` 前的长度闸：属性值超过 5 位就一定 > 10 000，
#: 而 Python 3.12 对 > 4300 位的数字串 `int()` 会直接抛 ValueError。
_REPEAT_DIGIT_CAP = 5


def _element_text(el: ET.Element) -> str:
    """递归取元素内的全部文本节点，等价于 `itertext()` 的语义。

    不能直接 `"".join(el.itertext())`：ODF 把空格、制表、换行表达成**空元素**
    （`text:s` / `text:tab` / `text:line-break`），它们没有文本子节点，
    `itertext()` 会把 `Hello<text:s/>World` 拼成 `HelloWorld`。所以这里手写递归，
    对这三个 tag 单独出字面量，其余与 `itertext()` 完全一致（text + 子节点 + tail）。
    """
    if el.tag == _TEXT_S:
        raw = el.get(_TEXT_C) or "1"
        count = int(raw) if raw.isdigit() and len(raw) <= _REPEAT_DIGIT_CAP else 1
        return " " * min(max(count, 1), _MAX_INLINE_SPACES)
    if el.tag == _TEXT_TAB:
        return "\t"
    if el.tag == _TEXT_LINE_BREAK:
        return "\n"
    parts = [el.text or ""]
    for child in el:
        parts.append(_element_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _collapse(text: str) -> str:
    """把块内空白压成单空格。

    段/单元格是**独立块**，块内换行没有下游语义（切分层按块取偏移），
    压平后既不会让 `\\t` 混进 TSV 单元格，也不会让引用里出现凭空多出的空行。
    """
    return " ".join(text.split())


def _cell_text(cell: ET.Element) -> str:
    """单元格文本；纯数字/日期单元格的正文是空的，回落到 `office:value`。

    单元格里可以有多个 `text:p`（一个格子里换段）。直接用 `_element_text` 会把
    「甲」「乙」拼成「甲乙」，丢掉段落边界，所以按 `text:p` 分段、段间补一个空格。
    """
    paragraphs = [_collapse(_element_text(p)) for p in cell.iter(_TEXT_P)]
    text = " ".join(t for t in paragraphs if t) if paragraphs else _collapse(_element_text(cell))
    if text:
        return text
    for attr in (_OFFICE_VALUE, _OFFICE_STRING_VALUE):
        raw = cell.get(attr)
        if raw:
            return _collapse(raw)
    return ""


def _repeat(el: ET.Element, attr: str) -> int:
    """读 `number-rows-repeated` / `number-columns-repeated`，缺省为 1。

    `check_odf_repeated` 已经保证过闸的值 ≤ 10 000（> 10 000 直接抛），
    这里的 `min` 与长度闸都是**兜底**：万一将来有人绕过闸门调用本模块，
    也不会因为 `int()` 转换超长数字串而崩，更不会无界展开。
    """
    raw = el.get(attr)
    if raw is None or not raw.isdigit():
        return 1
    # 先去掉前导 0 再判长度：`0000001` 是 1，不是 10 000。
    digits = raw.lstrip("0") or "0"
    if len(digits) > _REPEAT_DIGIT_CAP:
        return MAX_ODF_REPEATED
    return max(1, min(int(digits), MAX_ODF_REPEATED))


def _collect_rows(el: ET.Element, out: list[ET.Element]) -> None:
    """按文档顺序收集 `table:table-row`，能穿过 `table-header-rows` / `row-group`。

    遇到嵌套的 `table:table` 就停：内表的行属于内表，摊平进来会让外表的行区间
    (`locator.rows`) 与真实表格对不上。
    """
    for child in el:
        if child.tag == _TABLE_ROW:
            out.append(child)
        elif child.tag != _TABLE_TABLE:
            _collect_rows(child, out)


def _table_rows(table: ET.Element) -> list[list[str]]:
    """把一张表展开成 `[[cell, ...], ...]`，行列重复声明都按字面量铺开。"""
    row_elements: list[ET.Element] = []
    _collect_rows(table, row_elements)
    rows: list[list[str]] = []
    written = 0
    for row_el in row_elements:
        cells: list[str] = []
        for cell in row_el:
            if cell.tag not in {_TABLE_CELL, _TABLE_COVERED_CELL}:
                continue
            value = _cell_text(cell)
            cells.extend(value for _ in range(_repeat(cell, _TABLE_COLS_REPEATED)))
        for _ in range(_repeat(row_el, _TABLE_ROWS_REPEATED)):
            if written >= _MAX_TABLE_CELLS:
                return rows
            rows.append(list(cells))
            written += len(cells)
    return rows


def _render_table(rows: list[list[str]]) -> str:
    """表格块渲染成 TSV：一行一行、单元格之间一个 `\\t`。"""
    # 只裁两端的换行，**不能**用 `strip()`：它连 `\t` 一起裁，
    # 首列为空的表格（`\tA1`）会被裁成 `A1`，整行左移一列且列数悄悄少一格。
    return "\n".join("\t".join(cells) for cells in rows).strip("\n")


def _parse_odt(root: ET.Element) -> list[Block]:
    """`.odt`：段落/标题按序出块，`table:table` 出表格块。

    标题是**绑定到后续段**的：最近一次 `text:h` 的文本写进后续每段的
    `ParaLocator.heading`（同时冗余进 `Block.heading_path`，与 docx 解析器同口径）。
    表格块不占段落序号，也不产生 `ParaLocator`（ADR 0008 D3：表格是独立单元）。
    """
    body = root.find(f"{_OFFICE_BODY}/{_OFFICE_TEXT}")
    if body is None:
        return []
    blocks: list[Block] = []
    heading: str | None = None
    para_index = 0
    table_index = 0

    def walk(parent: ET.Element) -> None:
        nonlocal heading, para_index, table_index
        for child in parent:
            tag = child.tag
            if tag == _TABLE_TABLE:
                rows = _table_rows(child)
                text = _render_table(rows)
                if not text:
                    continue
                table_index += 1
                blocks.append(
                    Block(
                        text=text,
                        is_tabular=True,
                        locator=TableLocator(index=table_index, rows=f"R1:R{len(rows)}"),
                    )
                )
            elif tag in {_TEXT_P, _TEXT_H}:
                text = _collapse(_element_text(child))
                if not text:
                    continue
                if tag == _TEXT_H:
                    heading = text
                para_index += 1
                blocks.append(
                    Block(
                        text=text,
                        heading_path=heading,
                        locator=ParaLocator(index=para_index, heading=heading),
                    )
                )
            else:
                # `text:section` / `text:list` / `text:list-item` 等容器要继续下钻，
                # 否则列表项里的段落会整段丢掉。
                walk(child)

    walk(body)
    return blocks


def _parse_ods(root: ET.Element) -> list[Block]:
    """`.ods`：一个 `table:table`（工作表）出**一个**表格块。

    ADR 0008 给电子表格留的是 `SheetLocator`（表名 + 单元格区间），但本节点没有
    SheetLocator 的接线位，按约定先用 `TableLocator`：`index` = 工作表序号（从 1 起），
    `rows` = 该表实际展开后的行区间，`sheet` = 真实工作表名（没有名字时才回落 None）。
    """
    body = root.find(f"{_OFFICE_BODY}/{_OFFICE_SPREADSHEET}")
    if body is None:
        return []
    blocks: list[Block] = []
    for sheet_index, sheet in enumerate(body.findall(_TABLE_TABLE), start=1):
        rows = _table_rows(sheet)
        text = _render_table(rows)
        if not text:
            continue
        blocks.append(
            Block(
                text=text,
                is_tabular=True,
                locator=TableLocator(
                    index=sheet_index,
                    rows=f"R1:R{len(rows)}",
                    sheet=sheet.get(f"{{{_NS_TABLE}}}name") or None,
                ),
            )
        )
    return blocks


def _parse_odp(root: ET.Element) -> list[Block]:
    """`.odp`：一页一张幻灯片，页内每个 `text:p` 出一块，`slide` 从 1 起。

    页内的占位符、备注框架都可能带 `text:p`，统一用 `.iter()` 递归取，
    这样「标题 + 正文 + 列表」这种多框架版式不会只剩标题。
    """
    body = root.find(f"{_OFFICE_BODY}/{_OFFICE_PRESENTATION}")
    if body is None:
        return []
    blocks: list[Block] = []
    for slide_no, page in enumerate(body.findall(_DRAW_PAGE), start=1):
        for para in page.iter(_TEXT_P):
            text = _collapse(_element_text(para))
            if text:
                blocks.append(Block(text=text, locator=SlideLocator(slide=slide_no)))
    return blocks


def _sniff(root: ET.Element) -> str:
    """没有可信后缀时按正文结构判定格式；判不出返回空串。"""
    body = root.find(_OFFICE_BODY)
    if body is None:
        return ""
    for child in body:
        if child.tag == _OFFICE_SPREADSHEET:
            return ".ods"
        if child.tag == _OFFICE_PRESENTATION:
            return ".odp"
        if child.tag == _OFFICE_TEXT:
            return ".odt"
    return ""


def _invalid(reason: str) -> AppError:
    """统一的「不是有效 ODF」错误，`details.reason` 只放类型名/条目名，不放字节。"""
    return AppError(
        ErrorCode.PARSE_FAILED,
        "不是有效的 OpenDocument",
        details={"reason": reason},
    )


def parse(data: bytes, filename: str = "") -> ParsedText:
    """解析 odt/ods/odp。任何底层异常都收敛成 `PARSE_FAILED`。

    格式按后缀判定。**后缀存在但不是 odt/ods/odp 时抛 `UNSUPPORTED_FORMAT`**
    （不拿正文去猜：`.pdf` 里恰好长得像 ODF 就会被静默错配）；只有后缀缺失
    （`filename` 的默认值就是空串）才按正文结构兜底判定——否则一个默认可调用的
    签名会必然抛错，等于给自己埋雷。
    """
    suffix = PurePosixPath(filename).suffix.lower()

    # 损坏/非 zip 输入：`BadZipFile` 一旦裸奔到 Worker 会被当成未知错误反复重试，
    # 而坏字节重试一百次也不会变好，所以必须在解析层收敛成不可重试的 PARSE_FAILED。
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — 库内异常类型随版本变化，按类名记录即可
        raise _invalid(type(exc).__name__) from exc

    with archive:
        # 闸门 1：解压前先看归档目录的累计体积（不解压、不读内容）。
        check_zip_size(archive)
        try:
            xml = archive.read("content.xml")
        except Exception as exc:  # noqa: BLE001 — 缺条目/坏压缩流都落在这里
            raise _invalid(type(exc).__name__) from exc

    # 闸门 2：重复声明必须在建树之前拦下，否则 10 000 行声明会在展开时撑爆内存。
    # 这里**不 try**：`ParseGuardError` 本身就是带 limit/actual 的 `PARSE_FAILED`，
    # 再包一层只会把诊断信息埋掉。
    check_odf_repeated(xml)

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise _invalid(type(exc).__name__) from exc

    # 有后缀就以它为准：`.pdf` 这类明确的外来后缀不该因为正文长得像 ODF 就被接管，
    # 否则「上传 .pdf 得到一份 odt 解析结果」会变成静默的错配——下面那条
    # `kind not in SUFFIXES` 会把外来后缀直接拒成 UNSUPPORTED_FORMAT。
    # 只有后缀缺失（`filename` 的默认值就是空串）时才按正文结构兜底判定。
    kind = suffix or _sniff(root)
    if kind not in SUFFIXES:
        raise AppError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"unsupported file type: {suffix or 'unknown'}",
            details={"allowed": sorted(SUFFIXES)},
        )

    if kind == ".ods":
        blocks = _parse_ods(root)
    elif kind == ".odp":
        blocks = _parse_odp(root)
    else:
        blocks = _parse_odt(root)

    if not blocks:
        raise AppError(ErrorCode.PARSE_FAILED, "OpenDocument 未提取到任何文本")
    return ParsedText(blocks=blocks, parser="odf")
