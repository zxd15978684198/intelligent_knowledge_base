"""S09：切片。把 ParsedText 切成 Chunk 列表。

关键约束：
- 普通块（`is_tabular=False`）：char_start / char_end 是**整篇正文**内的字符偏移，
  不是块内偏移，否则来源定位（citations）拼不回原文。
- 表格块（`is_tabular=True`）：ADR 0008 D3 的受控例外——不进 `full_text` 拼接，
  `char_start`/`char_end` 是**块内**偏移（不可回拼原文），`page_at`/`heading_at`
  对它不求值（返回 None，交给 `locator` 兜底），`locator_ref` 写 `locator` 的
  紧凑串（如 `sheet:Sheet1!B2:C4`）而不是 `offsets:`。表格的可核对性由
  `locator`（sheet/cells/rows）承担，见 AGENTS.md §4 I6 的适用范围限定。
- overlap 在切分点回退，保证跨块语义不被硬切。
- page_no / page_end_no 是**起止页**：一块横跨两页时，只记起始页会让引用
  "看起来对、翻过去找不到后半段"（P2-D2 实测：4 页 PDF 有一块跨 3–4 页）。
  这里不做"按页切断"——那会把跨页的句子劈成两块，用引用精度换召回质量。
"""

from __future__ import annotations

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.chunk import Chunk
from pyknora.schemas.locator import (
    Locator,
    PageLocator,
    ParaLocator,
    RegionLocator,
    SheetLocator,
    SlideLocator,
    TableLocator,
)
from pyknora.services.ingest.parser import Block, ParsedText

# 优先在这些字符之后断开，避免把句子切一半
_BREAK_CHARS = "。！？；.!?;\n"


def compact_locator(locator: Locator) -> str:
    """把 `locator` 压成 `locator_ref` 用的紧凑串（ADR 0008 D3-4 / D2a）。

    `locator_ref` 原本只写 `offsets:{start}-{end}`，对表格块不成立（偏移不可回拼），
    这里改用结构化位置的紧凑序列化，**格式随 kind 不同**：

    | kind | 形态 |
    | --- | --- |
    | sheet | `sheet:Sheet1!B2:C4`（无 `cells` 时 `sheet:Sheet1`） |
    | table | `table:R1200:R1350`（无 `rows` 退 `cells`，再退表序号） |
    | slide | `slide:3` |
    | page | `page:12` |
    | para | `para:7`（存量回填的 `index=None` 时只写 `para`） |
    | region | `region:1`（页内坐标不进串，前端按 `bbox` 画框） |

    只表达"在哪"，不表达"多大"：区间信息已经在 `locator` 的 `cells`/`rows` 里。
    """
    if isinstance(locator, SheetLocator):
        if locator.cells:
            return f"sheet:{locator.sheet}!{locator.cells}"
        return f"sheet:{locator.sheet}"
    if isinstance(locator, TableLocator):
        if locator.rows:
            return f"table:{locator.rows}"
        if locator.cells:
            return f"table:{locator.cells}"
        return f"table:{locator.index}"
    if isinstance(locator, SlideLocator):
        return f"slide:{locator.slide}"
    if isinstance(locator, PageLocator):
        return f"page:{locator.page}"
    if isinstance(locator, ParaLocator):
        return f"para:{locator.index}" if locator.index is not None else "para"
    if isinstance(locator, RegionLocator):
        return f"region:{locator.page}"
    return f"locator:{locator.kind}"  # pragma: no cover — 6 种 kind 上面已穷举


def _find_break(text: str, start: int, end: int) -> int:
    """在 [start, end) 里找一个语义断点；找不到就返回 end。"""
    for i in range(end, start, -1):
        if text[i - 1] in _BREAK_CHARS:
            return i
    return end


def _chunk_from_span(text: str, start: int, end: int) -> tuple[str, int, int]:
    """把 `[start, end)` 收成一个 chunk 的 (content, char_start, char_end)。

    只 strip 首尾：`content` 必须是 `full_text` 的原样切片，citations 靠
    char_start/char_end 回原文，一旦在这里做内部空白折叠，偏移就拼不回原样了
    （真实踩过的坑，AGENTS §4 I6）。
    """
    raw = text[start:end]
    stripped = raw.strip()
    leading = len(raw) - len(raw.lstrip())
    char_start = start + leading
    return stripped, char_start, char_start + len(stripped)


def chunk_parsed(
    parsed: ParsedText,
    *,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> list[Chunk]:
    """把解析结果切成带偏移的块。

    chunk_size / chunk_overlap 以字符计。校验顺序很重要：先判参数，
    再拼正文——参数非法时不应该先做一遍无用的字符串拼接。
    """
    if chunk_size <= 0:
        raise AppError(ErrorCode.INVALID_REQUEST, "chunk_size must be > 0")
    if chunk_overlap < 0:
        raise AppError(ErrorCode.INVALID_REQUEST, "chunk_overlap must be >= 0")
    if chunk_overlap >= chunk_size:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"chunk_overlap({chunk_overlap}) must be < chunk_size({chunk_size})",
        )

    # 拼接时记录每块在全文中的起始偏移，供 page_no 回查。
    # ADR 0008 D3-1：表格块**不进拼接**——它是独立单元，不为它算全文偏移。
    # spans 的第四项是块在 parsed.blocks 里的序号，用来把表格块与文本块按文档顺序合并。
    spans: list[tuple[int, int, Block, int]] = []
    cursor = 0
    pieces: list[str] = []
    for index, block in enumerate(parsed.blocks):
        if block.is_tabular or block.is_visual:
            # 表格块（D3-1）与视觉块（D9-5）都不参与全文拼接：前者是块内偏移，
            # 后者根本没有字符流。它们各自在下面独立成块。
            continue
        # 分隔符只插在两个参与拼接的块之间；全文本流时等价于原来的 `i > 0`。
        if pieces:
            pieces.append("\n\n")
            cursor += 2
        start = cursor
        pieces.append(block.text)
        cursor += len(block.text)
        spans.append((start, cursor, block, index))
    full_text = "".join(pieces)

    def page_at(offset: int) -> int | None:
        for start, end, block, _index in spans:
            if start <= offset < end:
                # 表格块对 page_at 让路（D3-2）：页码由 `locator` 兜底。
                return None if block.is_tabular else block.page_no
        return None

    def heading_at(offset: int) -> str | None:
        for start, end, block, _index in spans:
            if start <= offset < end:
                return None if block.is_tabular else block.heading_path or block.text[:40]
        return None

    def block_index_at(offset: int) -> int:
        """偏移落在哪一块——只用于按文档顺序排列 chunk，不参与任何取值。"""
        for start, end, _block, index in spans:
            if start <= offset < end:
                return index
        return len(parsed.blocks)  # pragma: no cover — spans 已覆盖整篇 full_text

    # (块序号, 块内起始偏移, chunk)：先收集，最后按文档顺序排一次再编号 ordinal。
    collected: list[tuple[int, int, Chunk]] = []

    pos = 0
    total = len(full_text)
    while pos < total:
        hard_end = min(pos + chunk_size, total)
        end = hard_end if hard_end == total else _find_break(full_text, pos, hard_end)
        if end <= pos:
            end = hard_end
        content, start, end_offset = _chunk_from_span(full_text, pos, end)
        if content:
            collected.append(
                (
                    block_index_at(start),
                    start,
                    Chunk(
                        ordinal=0,  # 真正的 ordinal 在排序后统一给
                        content=content,
                        page_no=page_at(start),
                        # 末字符所在页：用 `end - 1` 而不是 `end`，因为 char_end 是
                        # 开区间端点，恰好落在页首时 `page_at(end)` 会指向下一页。
                        page_end_no=page_at(end_offset - 1),
                        heading_path=heading_at(start),
                        char_start=start,
                        char_end=end_offset,
                        token_count=max(1, len(content) // 2),
                        locator_ref=f"offsets:{start}-{end_offset}",
                    ),
                )
            )
        if end >= total:
            break
        pos = max(end - chunk_overlap, pos + 1)

    # ADR 0008 D3：表格块独立成块。char_* 是**块内**偏移（不叠加全文 cursor），
    # page/heading 一律 None，locator_ref 走 locator 的紧凑串。
    # 超长表格的行级切分（D3-4 的 `[表头]` 注入）不在本节点，整块原子输出。
    for index, block in enumerate(parsed.blocks):
        if not block.is_tabular:
            continue
        content, start, end_offset = _chunk_from_span(block.text, 0, len(block.text))
        if not content:
            continue
        collected.append(
            (
                index,
                start,
                Chunk(
                    ordinal=0,  # 同上
                    content=content,
                    # D3-2：page_at()/heading_at() 不对表格块求值，这里直接置空。
                    page_no=None,
                    page_end_no=None,
                    heading_path=None,
                    char_start=start,
                    char_end=end_offset,
                    token_count=max(1, len(content) // 2),
                    # D3-4：有 locator 就写紧凑串；没有 locator 时保持旧的 offsets 形态。
                    locator_ref=(
                        compact_locator(block.locator)
                        if block.locator is not None
                        else f"offsets:{start}-{end_offset}"
                    ),
                ),
            )
        )

    # ADR 0008 D9-5 / D10-5：视觉块独立成块，`char_start`/`char_end` 一律 None。
    # 理由不是"懒得算"——位图里没有"原文第几个字符"，硬填一个偏移等于给引用系统
    # 一个假坐标；能核对的只有 `locator` 的 bbox。页码与标题取块自带的（OCR 块有
    # page_no，VLM 块同样），不像表格块那样置空。
    for index, block in enumerate(parsed.blocks):
        if not block.is_visual:
            continue
        content = block.text.strip()
        if not content:
            # VLM 失败时 `blocks_from_vlm` 会留一个空占位块（D9-6：不许静默丢页），
            # 它的"存在"已经记在 `ParsedText.degraded` 里，不该再产出一个空 chunk。
            continue
        collected.append(
            (
                index,
                0,
                Chunk(
                    ordinal=0,  # 同上
                    content=content,
                    page_no=block.page_no,
                    page_end_no=block.page_no,
                    heading_path=block.heading_path,
                    char_start=None,
                    char_end=None,
                    token_count=max(1, len(content) // 2),
                    locator_ref=(
                        compact_locator(block.locator) if block.locator is not None else None
                    ),
                    locator=block.locator,
                ),
            )
        )

    collected.sort(key=lambda item: (item[0], item[1]))
    chunks = [
        chunk.model_copy(update={"ordinal": ordinal})
        for ordinal, (_index, _start, chunk) in enumerate(collected)
    ]

    if not chunks:
        raise AppError(ErrorCode.PARSE_INCOMPLETE, "解析成功但切不出任何 chunk")
    return chunks
