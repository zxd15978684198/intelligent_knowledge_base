"""N13 / ADR 0008 D5 + D5-1：PDF 文本与表格解析器（pdfplumber 后端）。

Lead 按下面两个名字机械接线，本模块不碰 `parser.py`：

    SUFFIXES: frozenset[str]      # frozenset({".pdf"})
    def parse(data: bytes, filename: str = "") -> ParsedText

## 产出规则

- **一份文档只 `pdfplumber.open()` 一次**（D5-1 的第一条硬约束）。文本与表格在**同一个
  `Page` 对象**上取：先 `page.extract_text()`，再 `page.find_tables()`。D5-1 实测：
  把文本与表格拆成两次 open，S2 从 3.630s 涨到 11.267s（12.8×）——每开一次都要重解析
  全部 `Page`，`_layout` 与 `get_textmap` 缓存全部作废。
- **分批**：`> 100` 页按 100 页一批（实测 pdfplumber 约 3.45–3.8 MB/页近线性，200 页
  +690 MB），每批处理完显式 `page.close()` 释放 `_layout`/`_objects`；`>= 150` 页强制
  走分批路径（D5-1 红线，见 :func:`_should_batch`）。`pdf.close()` 自身也会逐页 close，
  重复调用是幂等的。
- **文本块**：`Block(text=<段落>, page_no=<1 基页号>, locator=PageLocator(page=..., heading=...))`。
  段落靠行几何切：页内行距的 20 分位当基线，行间距超过基线 1.6 倍即新段；标题行单独成块。
- **页眉页脚当噪声丢弃**：页顶 8% / 页底 8% 带内、字号小于本页正文的行（每页重复的
  公文头 `Q/XZG·JG·13·11-2020`、页码）不进块。只丢"小字号的固定文字"——S3 的 22.5pt
  大标题同样在页顶，字号大于正文，保留。实测 S2 每页少 1–2 个噪声块。
- **表格块**：`Block(text=<制表符渲染>, is_tabular=True, locator=TableLocator(...))`。
  准入通过的表**只出一次**——不再额外产出一份按行的纯文本块（D3-1：重复计入检索），
  它覆盖的文本行也从文本流里剔除。准入不通过的表**原样留在文本流里**，不丢内容。
- **扫描件**：整篇抽不到任何文本时抛 `PARSE_FAILED`，`details["reason"] == "no_text_layer"`。
  这个字符串是 N14 触发 OCR 的信号，**不许改**。
- 加密 / 坏文件同样是 `PARSE_FAILED`，`details["reason"]` 取 `"encrypted"` / `"broken"`。

## 几处取舍（ADR 没写死的）

1. **标题靠字号 + 字体判**，不靠"目录/章节"这类字面量：页内最多数字号记正文，
   行字号 > 正文 ×1.15 视为标题；字号与正文相同但字体不同（黑体标题 + 宋体正文）也算。
   再加三道闸：≤40 字、不以句末标点结尾、页眉页脚带内的**小于正文**的行不算。
   实测 S3（多表格的成绩分析）标题全覆盖；S2（制度文档）章标题 14.0pt 与正文同字号，
   靠字体差异覆盖。**判不出就是 `heading=None`，绝不猜**——错标题比没标题更伤引用。
2. **表格准入四条**（D5-1）：≥2 行且 ≥2 列、首行每格非空且 ≤20 字、任一格 >200 字整表
   丢弃、列数 2–12。四条是"与"关系，任一条不过就当普通文本。淘汰原因由
   :func:`_table_rejection` 返回字符串，供实测统计（不进 `details`）。
3. `TableLocator` 原先**没有 `page` 字段**（只有 `index`/`rows`/`cells`/`slide`/`sheet`），
   表格块会丢掉"在第几页"。本节点按任务书未改 `schemas/locator.py`，把它当契约缺口上报；
   Lead 随后补了 `page: int | None`（与 `slide` 同型、向后兼容），本模块已传 `page=page_no`。
4. 页内顺序：先按行序出文本块，再出该页的表格块（按 bbox 的 y 序）。表格在版面中间时
   会排到该页文本之后——`is_tabular` 块本来就不进 `full_text`，不影响正文回拼。
5. `(cid:127)` 这类字形是 pdfminer 的原始输出（S3 的列表符号），**不改写**：改写等于编造。
"""

from __future__ import annotations

import io
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import PageLocator, TableLocator
from pyknora.services.ingest.guards import check_document_limits
from pyknora.services.ingest.parser import Block, ParsedText

#: 本模块负责的后缀。Lead 的 `parser.py` 按它分派，别在别处再抄一份。
SUFFIXES: frozenset[str] = frozenset({".pdf"})

#: 写进 `ParsedText.parser`，用于区分 pypdf 基线（`"pypdf"`）与本次后端。
PARSER_NAME = "pdfplumber"

#: 每批页数。内存实测 3.45–3.8 MB/页 → 100 页约 350–380 MB 峰值。
PAGE_BATCH_SIZE = 100
#: 页数到这里**必须**分批（D5-1 红线：150 页以上单路径会把内存顶穿）。
FORCE_BATCH_PAGES = 150

# 表格准入（D5-1）。四个阈值是"与"关系，改这里等于改契约，必须同步 ADR。
MIN_TABLE_ROWS = 2
MIN_TABLE_COLUMNS = 2
MAX_TABLE_COLUMNS = 12
MAX_HEADER_CHARS = 20
MAX_CELL_CHARS = 200

# 段落与标题启发式。数值都来自真实样本实测（见 docs/plans/parsing-bench-pdfplumber.md）。
PARAGRAPH_GAP_RATIO = 1.6
HEADING_SIZE_RATIO = 1.15
HEADING_MAX_CHARS = 40
HEADER_BAND = 0.08
FOOTER_BAND = 0.92
#: 页眉页脚带内，长度不超过它的纯数字行算页码（"12"、"iv" 不算——后者不是数字）。
PAGE_NUMBER_MAX_CHARS = 8
MAX_BLOCK_CHARS = 2_000

# 句末标点：以它结尾的行不是标题（"理论以官方答案为准；" 这种半句）。
_SENTENCE_END = "。！？；：，、,.;:!?"
# 表格 bbox 与文本行的容差（pt）：行只要"基本在框内"就当被表格吃掉。
_BBOX_TOLERANCE = 2.0
# 两张表的 bbox 交集占较小者面积的比例超过它就当重复检测（见 `_overlap_ratio`）。
_DUPLICATE_OVERLAP = 0.8

_WHITESPACE_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- 数据结构


@dataclass(slots=True)
class _Line:
    """一行文本 + 几何。`has_geometry=False` 时几何字段全是 `0.0`（降级路径）。"""

    text: str
    top: float = 0.0
    bottom: float = 0.0
    x0: float = 0.0
    x1: float = 0.0
    size: float = 0.0
    font: str = ""


@dataclass(slots=True)
class _PageText:
    """一页的行流。`has_geometry` 为 False 表示拿不到行坐标，只能整页成块。"""

    lines: list[_Line] = field(default_factory=list)
    has_geometry: bool = False


@dataclass(slots=True)
class _DocState:
    """跨页状态：标题沿用、全文表格序号、是否见过文字。"""

    heading: str | None = None
    table_index: int = 0
    chars: int = 0


# --------------------------------------------------------------------------- 工具


def _percentile(values: list[float], fraction: float) -> float:
    """线性取分位（不引 numpy：这里只要一个基线行距）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[position]


def _cell_text(raw: str | None) -> str:
    """单元格 → 单行文本。单元格内的换行压成空格：一个格就是一个字段，不能带 `\\t`/`\\n`。"""
    if raw is None:
        return ""
    return _WHITESPACE_RE.sub(" ", raw).strip()


def _trim_empty_rows(rows: list[list[str]]) -> list[list[str]]:
    """裁掉尾部全空行：`find_tables` 会把表格下方的空白行也带进来。"""
    trimmed = list(rows)
    while trimmed and not any(trimmed[-1]):
        trimmed.pop()
    return trimmed


def _overlap_ratio(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    """两个 bbox 的交集面积 / 较小者面积。0 表示不沾，1 表示完全重合。"""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    if smaller <= 0:
        return 0.0
    return (width * height) / smaller


# --------------------------------------------------------------------------- 表格准入


def _table_rejection(rows: list[list[str]]) -> str | None:
    """表格准入四条（ADR 0008 D5-1）。`None` = 准入，否则返回淘汰原因。

    四条同时满足才收：**≥2 行且 ≥2 列**、**首行每格非空且 ≤20 字**、
    **任一单元格 >200 字整表丢弃**、**列数 2–12**。不通过的表由调用方当普通文本处理，
    这里只回答"收还是不收、为什么"。
    """
    if len(rows) < MIN_TABLE_ROWS:
        return "too_few_rows"
    columns = max(len(row) for row in rows)
    if columns < MIN_TABLE_COLUMNS:
        return "too_few_columns"
    if columns > MAX_TABLE_COLUMNS:
        return "too_many_columns"
    header = rows[0]
    if len(header) < columns or any(not cell for cell in header[:columns]):
        return "empty_header_cell"
    if any(len(cell) > MAX_HEADER_CHARS for cell in header[:columns]):
        return "header_too_long"
    if any(len(cell) > MAX_CELL_CHARS for row in rows for cell in row):
        return "cell_too_long"
    return None


def _render_table(rows: list[list[str]]) -> list[str]:
    """单元格矩阵 → 逐行 TSV（行尾空单元格截掉、行首行中保留，与 `tabular.py` 一致）。"""
    rendered: list[str] = []
    for row in rows:
        trimmed = list(row)
        while trimmed and trimmed[-1] == "":
            trimmed.pop()
        if not trimmed:  # 全空行：不渲染，否则行号会和真实文件错位
            continue
        rendered.append("\t".join(trimmed))
    return rendered


def _page_tables(
    page: Any,
) -> tuple[list[tuple[tuple[float, float, float, float], list[list[str]]]], list[str]]:
    """取出本页的表格：`(准入的 (bbox, 行矩阵), 淘汰原因列表)`。

    同一页上被重复检出的同一张表（bbox 高度重合）只收一次，否则同一份数据会进检索两次。
    """
    admitted: list[tuple[tuple[float, float, float, float], list[list[str]]]] = []
    rejected: list[str] = []
    for table in page.find_tables():
        bbox = tuple(float(value) for value in table.bbox)
        rows = _trim_empty_rows([[_cell_text(cell) for cell in row] for row in table.extract()])
        reason = _table_rejection(rows)
        if reason is not None:
            rejected.append(reason)
            continue
        if any(_overlap_ratio(bbox, other) > _DUPLICATE_OVERLAP for other, _ in admitted):
            rejected.append("duplicate")
            continue
        admitted.append((bbox, rows))
    return admitted, rejected


# --------------------------------------------------------------------------- 行与标题


def _body_style(page: Any) -> tuple[float, str]:
    """页内"正文"的字号与字体：取出现次数最多的那一档。"""
    sizes = Counter(round(float(char["size"]), 1) for char in page.chars)
    fonts = Counter(str(char["fontname"]) for char in page.chars)
    body_size = sizes.most_common(1)[0][0] if sizes else 0.0
    body_font = fonts.most_common(1)[0][0] if fonts else ""
    return body_size, body_font


def _page_text(page: Any) -> _PageText:
    """`extract_text()` 的行 + `extract_text_lines()` 的几何，两者按行号对齐。

    两份真实样本（S2 40 页 / S3 9 页）实测**逐行完全一致**，所以对齐是安全的；
    万一对不上（不同 pdfplumber 版本的换行策略差异）就降级为"整页一块、无标题"，
    内容一个不丢，只是位置粒度变粗。
    """
    text = page.extract_text() or ""
    plain = text.split("\n") if text else []
    try:
        geometry = page.extract_text_lines()
    except Exception:  # noqa: BLE001 — 内部实现变化时不值得让整篇解析失败
        geometry = []
    aligned = len(geometry) == len(plain) and all(
        str(line["text"]) == raw for line, raw in zip(geometry, plain, strict=True)
    )
    if not aligned:
        return _PageText(lines=[_Line(text=raw) for raw in plain], has_geometry=False)
    lines: list[_Line] = []
    for line in geometry:
        sizes = [float(char["size"]) for char in line["chars"]]
        fonts = Counter(str(char["fontname"]) for char in line["chars"])
        lines.append(
            _Line(
                text=str(line["text"]),
                top=float(line["top"]),
                bottom=float(line["bottom"]),
                x0=float(line["x0"]),
                x1=float(line["x1"]),
                size=median(sizes) if sizes else 0.0,
                font=fonts.most_common(1)[0][0] if fonts else "",
            )
        )
    return _PageText(lines=lines, has_geometry=True)


def _is_heading(line: _Line, body_size: float, body_font: str, page_height: float) -> bool:
    """这一行是不是标题。三条闸：短、不以句末标点结尾、不是页眉页脚。

    "不是页眉页脚"只对**小于正文**的行生效：页眉页脚通常是小一号的固定文字
    （S2 的 `Q/XZG·JG·13·11-2020` 是 10.6pt 黑体、正文 14pt 宋体），而文档大标题
    也在页顶但字号大于正文（S3 的 22.5pt），不能一起误伤。
    """
    text = line.text.strip()
    if not text or len(text) > HEADING_MAX_CHARS:
        return False
    if text[-1] in _SENTENCE_END:
        return False
    in_furniture = line.top < page_height * HEADER_BAND or line.bottom > page_height * FOOTER_BAND
    if in_furniture and line.size < body_size:
        return False
    if body_size and line.size > body_size * HEADING_SIZE_RATIO:
        return True
    # 字号与正文相同、字体不同：中文制度文档的"第一章 总则"就是这种排法。
    return bool(body_font) and line.font != body_font and line.size >= body_size * 0.95


def _is_furniture(line: _Line, body_size: float, page_height: float) -> bool:
    """这一行是不是页眉/页脚噪声：页顶/页底带内**小于正文**的固定文字，或带内的纯页码。

    只看带区会把 S3 的 22.5pt 页顶大标题当噪声丢掉，所以必须叠一条"字号小于正文"。
    页码单独判是因为有的模板页码用正文字号（`:func:`_is_heading` 里同一套带区口径）。
    """
    in_band = line.top < page_height * HEADER_BAND or line.bottom > page_height * FOOTER_BAND
    if not in_band:
        return False
    if body_size and line.size < body_size - 0.5:
        return True
    text = line.text.strip()
    return text.isdigit() and len(text) <= PAGE_NUMBER_MAX_CHARS


def _line_pitch(lines: list[_Line]) -> float:
    """页内基线行距：相邻行空隙的 20 分位（段落间距只会比它大）。"""
    gaps = [
        later.top - earlier.bottom
        for earlier, later in zip(lines, lines[1:], strict=False)
        if later.top - earlier.bottom > 0
    ]
    return _percentile(gaps, 0.2)


def _covered(line: _Line, boxes: list[tuple[float, float, float, float]]) -> bool:
    """这一行是否被某张**准入通过**的表格覆盖（覆盖了就由表格块出，文本流里剔除）。"""
    return any(
        line.top >= top - _BBOX_TOLERANCE
        and line.bottom <= bottom + _BBOX_TOLERANCE
        and line.x1 > x0
        and line.x0 < x1
        for x0, top, x1, bottom in boxes
    )


# --------------------------------------------------------------------------- 块构造


def _text_block(text: str, page_no: int, heading: str | None) -> Block:
    """文本块。`heading_path` 与 `locator.heading` 同时写：

    `Locator` 是 D2 的契约位，`heading_path` 是切分层 `chunk_parsed` 至今在用的字段
    （`splitter.py:146`），两边口径一致才不会出现"引用里没标题、chunk 里有"。
    """
    return Block(
        text=text,
        page_no=page_no,
        heading_path=heading,
        locator=PageLocator(page=page_no, heading=heading),
    )


def _table_block(rows: list[list[str]], page_no: int, state: _DocState) -> Block:
    """表格块。序号是**全文** 1 基序号，`rows` 是表内行区间（含表头）。"""
    rendered = _render_table(rows)
    state.table_index += 1
    return Block(
        text="\n".join(rendered),
        page_no=page_no,
        locator=TableLocator(
            index=state.table_index,
            rows=f"R1:R{len(rendered)}" if rendered else None,
            page=page_no,
        ),
        is_tabular=True,
    )


# --------------------------------------------------------------------------- 单页


def _page_blocks(page: Any, page_no: int, state: _DocState) -> list[Block]:
    """一页 → 若干块。文本块先出，本页的表格块按 y 序追加在后。"""
    page_text = _page_text(page)
    state.chars += sum(len(line.text.strip()) for line in page_text.lines)

    admitted, _ = _page_tables(page)  # 淘汰原因只给实测脚本看，解析结果里不出现
    blocked = [bbox for bbox, _ in admitted]

    blocks = _text_blocks(page, page_text, page_no, blocked, state)
    blocks.extend(_table_block(rows, page_no, state) for _bbox, rows in admitted)
    return blocks


def _text_blocks(
    page: Any,
    page_text: _PageText,
    page_no: int,
    blocked: list[tuple[float, float, float, float]],
    state: _DocState,
) -> list[Block]:
    """行流 → 文本块。标题行单独成块并更新跨页标题；段落按行距切。"""
    lines = [line for line in page_text.lines if line.text.strip()]
    if not lines:
        return []
    if not page_text.has_geometry:
        # 降级路径：拿不到行坐标，整页一块；标题无法归属，保持原样（None）。
        return [_text_block("\n".join(line.text for line in lines), page_no, state.heading)]

    body_size, body_font = _body_style(page)
    page_height = float(page.height) or 1.0
    pitch = _line_pitch(lines)

    blocks: list[Block] = []
    buffer: list[str] = []
    buffer_heading = state.heading
    previous: _Line | None = None

    def flush() -> None:
        nonlocal buffer
        text = "\n".join(buffer)
        if text.strip():
            blocks.append(_text_block(text, page_no, buffer_heading))
        buffer = []

    for line in lines:
        if _covered(line, blocked):
            continue
        if _is_furniture(line, body_size, page_height):
            continue
        if _is_heading(line, body_size, body_font, page_height):
            flush()
            state.heading = line.text.strip()
            blocks.append(_text_block(state.heading, page_no, state.heading))
            previous = line
            continue
        if buffer and previous is not None:
            gap = line.top - previous.bottom
            too_long = sum(len(part) + 1 for part in buffer) + len(line.text) > MAX_BLOCK_CHARS
            if too_long or (pitch > 0 and gap > pitch * PARAGRAPH_GAP_RATIO):
                flush()
        if not buffer:
            buffer_heading = state.heading
        buffer.append(line.text)
        previous = line
    flush()
    return blocks


# --------------------------------------------------------------------------- 分批


def _page_batches(page_count: int) -> Iterator[range]:
    """`range` 批：100 页一批。空文档给不出批（调用方另行判 0 页）。"""
    for start in range(0, page_count, PAGE_BATCH_SIZE):
        yield range(start, min(start + PAGE_BATCH_SIZE, page_count))


def _should_batch(page_count: int) -> bool:
    """是否走分批路径。`>=150` 页是 D5-1 的红线，`>100` 是内存实测的批大小。"""
    return page_count >= FORCE_BATCH_PAGES or page_count > PAGE_BATCH_SIZE


# --------------------------------------------------------------------------- 打开与报错


#: 异常链最多看的层数：pdfplumber → pdfminer → pypdf 一般两三跳就到底了。
_CHAIN_LIMIT = 8


def _error_signature(exc: BaseException) -> tuple[str, list[str]]:
    """异常链的类型名与消息拼成的指纹。

    只看 `str(exc)` 会漏：加密 PDF 上 pdfplumber 抛的 `PdfminerException` **消息是空串**，
    真正的信号在 `__cause__` 的 `PDFPasswordIncorrect` 里（实测）。
    """
    names: list[str] = []
    messages: list[str] = []
    current: BaseException | None = exc
    for _ in range(_CHAIN_LIMIT):
        if current is None:
            break
        names.append(type(current).__name__)
        messages.append(str(current))
        current = current.__cause__ or current.__context__
    return " ".join(names).lower(), [part.lower() for part in messages]


def _open_error(exc: Exception) -> AppError:
    """把 pdfminer / pypdf 的底层异常翻成可判别的 `PARSE_FAILED`。

    加密单独给 `"encrypted"`：这不是坏文件，用户补密码就能救，和"字节坏了"必须分开。
    判据是"类型名 + 消息"的整条异常链，理由见 :func:`_error_signature`。
    """
    names, messages = _error_signature(exc)
    if (
        "password" in names
        or "encrypt" in names
        or any("password" in part or "encrypt" in part for part in messages)
    ):
        return AppError(
            ErrorCode.PARSE_FAILED,
            "PDF 已加密，无法解析（需要密码）",
            details={"reason": "encrypted"},
        )
    return AppError(
        ErrorCode.PARSE_FAILED,
        "PDF 解析失败（文件损坏或不完整）",
        details={"reason": "broken", "error": type(exc).__name__},
    )


def _open_pdf(data: bytes) -> Any:
    """打开 PDF 流。**整篇只调用一次**——调用点只有 :func:`parse`。"""
    import pdfplumber  # 函数内 import：pdfplumber 很重，模块级 import 会拖慢 CLI 冷启

    try:
        return pdfplumber.open(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — pdfminer 的异常类型随版本变化
        raise _open_error(exc) from exc


# --------------------------------------------------------------------------- 入口


def parse(data: bytes, filename: str = "") -> ParsedText:
    """PDF bytes → `ParsedText`。`filename` 只为对齐解析器签名，本模块不用它。

    错误出口只有三个：`reason="empty"`（0 字节）、`"encrypted"` / `"broken"`（打不开）、
    `"no_text_layer"`（能打开但没有文本层，N14 的 OCR 信号）。规模越界由
    `guards.check_document_limits` 抛 `ParseGuardError`（同一错误码，`details` 带 metric）。
    """
    if not data:
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "PDF 内容为空（0 字节）",
            details={"reason": "empty"},
        )

    pdf = _open_pdf(data)
    blocks: list[Block] = []
    state = _DocState()
    try:
        try:
            page_count = len(pdf.pages)
        except Exception as exc:  # noqa: BLE001 — 页树坏掉的 PDF 在这一步才炸
            raise _open_error(exc) from exc
        if page_count == 0:
            raise AppError(
                ErrorCode.PARSE_FAILED,
                "PDF 没有任何页面",
                details={"reason": "broken", "pages": 0},
            )
        check_document_limits(pages=page_count)

        if _should_batch(page_count):
            for batch in _page_batches(page_count):
                for index in batch:
                    page = pdf.pages[index]
                    try:
                        blocks.extend(_page_blocks(page, index + 1, state))
                    finally:
                        # 释放 `_layout` / `_objects`：分批的意义全在这一句上。
                        page.close()
        else:
            for index, page in enumerate(pdf.pages):
                blocks.extend(_page_blocks(page, index + 1, state))
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 — 解析期的库内异常一律转可诊断的错误
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "PDF 解析失败（文件损坏、截断或编码异常）",
            details={"reason": "broken", "error": type(exc).__name__},
        ) from exc
    finally:
        pdf.close()

    if not blocks:
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "PDF 未提取到任何文本（可能是扫描件）",
            details={"reason": "no_text_layer", "pages": page_count},
        )
    return ParsedText(blocks=blocks, parser=PARSER_NAME)
