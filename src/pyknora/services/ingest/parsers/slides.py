"""PPTX 解析器：ADR 0008 D3 的 `slide` / `table` 两个 locator 分支。

契约与分发层（`services/ingest/parser.py`）一致：

- ``SUFFIXES = frozenset({".pptx"})``；
- ``parse(data, filename="") -> ParsedText``。

产出规则（ADR 0008 D3）：

1. 每张幻灯片内的**文本形状**（title / body / 其他占位符）产出一个
   ``Block(is_tabular=False, locator=SlideLocator(slide=n, shape=<shape.name>))``，
   ``slide`` 从 1 开始；形状内多个段落用 ``\n`` 连接成块文本（段落内的 run 已被
   ``paragraph.text`` 拼好，含字段文本）。
2. 幻灯片里的 ``GraphicFrame`` 表格产出
   ``Block(is_tabular=True, locator=TableLocator(index=<全文第几张表>, rows="R1:R<n>"))``，
   文本是**制表符渲染**：一行对应一条 ``table.rows``，单元格之间用 ``\t``，表头行原样
   保留（I6 对表格块不适用，见 ADR 0008 D3；表格块也不进 ``ParsedText.full_text``）。
3. 图片**不 OCR**（视觉通道是 D9 的另一条链路）：本模块只认文本框与表格，图片
   自然落空，因此不需要枚举 ``MSO_SHAPE_TYPE``。组合形状递归展开，顺序即文档顺序。
4. 空幻灯片（既无文字也无表格）不产出块；整篇无内容 → **空** ``ParsedText``，
   不抛 ``PARSE_FAILED``——一份只有图片的演示文稿不是损坏文件。

两处口径**以既有契约为准**，与任务书措辞不同时按代码走：

- ``TableLocator.index`` 在 ``schemas/locator.py`` 里是 ``Field(ge=1)``（"文档内第几张表，
  从 1 开始"），所以这里**从 1 开始**；写 0 会被 pydantic 直接拒（ValidationError）。
  序号是**全文维度**的，跨幻灯片累加，且只对真正产出块的表格计数（全空表不占号）。
- ``Block.page_no`` 的既有 docstring 写明"只有 PDF 才有意义"，位置由 ``locator`` 承载
  （ADR 0008 D2），所以这里**不填** ``page_no``。已知代价：``TableLocator`` 没有
  ``slide`` 字段，表格块因此只记得"第几张表"，记不住"第几页"——这是契约缺口，
  已写进 N8 交付报告，不在本节点擅自加字段。

错误一律走 ``AppError``（``I8``），码固定 ``PARSE_FAILED``：缺包、坏 zip、坏 OOXML
三种情况都重试无益，不能让库内异常裸奔到 Worker 被当成"未知错误"反复重试。
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from typing import Any

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import SlideLocator, TableLocator
from pyknora.services.ingest.guards import check_document_limits, check_zip_size
from pyknora.services.ingest.parser import Block, ParsedText

#: 本解析器认领的后缀。分发层按这个集合选路（ADR 0008 D4：PPTX 属 P1）。
SUFFIXES: frozenset[str] = frozenset({".pptx"})

#: ``ParsedText.parser`` 的取值，便于作业层区分是谁解出来的。
PARSER_NAME = "python-pptx"

# 单元格内的换行/制表符必须折叠成空格：一条 `table.rows` 必须渲染成 TSV 的**一行**，
# 否则行区间 `R1:R<n>` 与文本行号就对不上，引用无法按行核对。
_CELL_WHITESPACE_RE = re.compile(r"\s+")


def _load_presentation() -> Any:
    """函数内 import：项目依赖里还没有 python-pptx（本节点不改 ``pyproject.toml``）。

    缺包按 ``PARSE_FAILED`` 报，而不是 ``INTERNAL_ERROR``：对调用方来说这就是
    "这份文件现在解析不了"，码与坏文件一致，前端提示与重试语义都不必分叉。
    """
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "缺少 python-pptx，无法解析 .pptx（请安装 python-pptx）",
            details={"reason": "ImportError", "dependency": "python-pptx"},
        ) from exc
    return Presentation


def _check_container(data: bytes) -> None:
    """解析前的第一道闸（ADR 0008 D6）：pptx 是 zip，先量解压后总体积。

    ``check_zip_size`` 只读归档目录、不解压，超 200 MiB（累计）抛 ``ParseGuardError``
    （``PARSE_FAILED`` 子类），必须让它原样穿透，不能被下面的兜底吞掉。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            check_zip_size(archive)
    except zipfile.BadZipFile as exc:
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "PPTX 不是有效的 zip 容器（文件损坏或不是 pptx）",
            details={"reason": type(exc).__name__},
        ) from exc
    except OSError as exc:
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "PPTX 读取失败（容器不可读）",
            details={"reason": type(exc).__name__},
        ) from exc


def _iter_shapes(shapes: Any) -> Iterator[Any]:
    """按文档顺序深度优先展开形状树；组合形状没有自己的文本，递归取其子形状。

    用 ``hasattr(shape, "shapes")`` 判组合而不是 ``MSO_SHAPE_TYPE.GROUP``：
    ``shape_type`` 对少数 graphicFrame 会抛 ``NotImplementedError``，而"有没有子形状"
    是结构事实，不需要枚举版本相关的常量。
    """
    for shape in shapes:
        nested = getattr(shape, "shapes", None)
        if nested is not None:
            yield from _iter_shapes(nested)
            continue
        yield shape


def _shape_text(shape: Any) -> str:
    """文本形状 → 块文本。空段落丢弃，段落之间用 ``\\n``。"""
    if not shape.has_text_frame:
        return ""
    lines = (paragraph.text.strip() for paragraph in shape.text_frame.paragraphs)
    return "\n".join(line for line in lines if line)


def _table_text(table: Any) -> str:
    """表格 → 制表符渲染（一行一条逻辑行，含表头行）。"""
    lines: list[str] = []
    for row in table.rows:
        cells = (_CELL_WHITESPACE_RE.sub(" ", cell.text).strip() for cell in row.cells)
        lines.append("\t".join(cells))
    return "\n".join(lines)


def parse(data: bytes, filename: str = "") -> ParsedText:
    """把 .pptx 字节解析成带 ``SlideLocator`` / ``TableLocator`` 的块序列。

    ``filename`` 只按契约收下、不参与判定：后缀白名单由分发层按 ``SUFFIXES`` 做，
    解析器自己再看一遍文件名只会造出第二处真相。
    """
    _ = filename

    _check_container(data)
    presentation_factory = _load_presentation()

    try:
        presentation = presentation_factory(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — 库内异常类型随版本变化，按类名记录
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "PPTX 解析失败（不是有效的 pptx，或 OOXML 结构损坏）",
            details={"reason": type(exc).__name__},
        ) from exc

    slides = list(presentation.slides)
    # 第二道规模闸：幻灯片数超 2000 直接拒（ADR 0008 D6，边界闭区间）。
    check_document_limits(slides=len(slides))

    blocks: list[Block] = []
    table_index = 0
    try:
        for slide_no, slide in enumerate(slides, start=1):
            for shape in _iter_shapes(slide.shapes):
                if shape.has_table:
                    table = shape.table
                    text = _table_text(table)
                    if not text.strip():
                        # 全空表不产出块、也不占表序号：它检索不到，占号只会让
                        # `table.index` 与实际可引用的表错位。
                        continue
                    table_index += 1
                    blocks.append(
                        Block(
                            text=text,
                            is_tabular=True,
                            locator=TableLocator(
                                index=table_index,
                                rows=f"R1:R{len(table.rows)}",
                                slide=slide_no,
                            ),
                        )
                    )
                    continue
                text = _shape_text(shape)
                if not text:
                    continue
                blocks.append(
                    Block(
                        text=text,
                        locator=SlideLocator(slide=slide_no, shape=shape.name or None),
                    )
                )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 — 同上，按类名记录
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "PPTX 解析失败（幻灯片内容无法读取）",
            details={"reason": type(exc).__name__},
        ) from exc

    return ParsedText(blocks=blocks, parser=PARSER_NAME)
