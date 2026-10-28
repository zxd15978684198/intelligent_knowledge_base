"""S09：切片。把 ParsedText 切成 Chunk 列表。

关键约束：
- char_start / char_end 是**整篇正文**内的字符偏移，不是块内偏移，
  否则来源定位（citations）拼不回原文。
- overlap 在切分点回退，保证跨块语义不被硬切。
- page_no 继承自起始段落的页码，用于来源追踪。
"""

from __future__ import annotations

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.chunk import Chunk
from pyknora.services.ingest.parser import Block, ParsedText

# 优先在这些字符之后断开，避免把句子切一半
_BREAK_CHARS = "。！？；.!?;\n"


def _find_break(text: str, start: int, end: int) -> int:
    """在 [start, end) 里找一个语义断点；找不到就返回 end。"""
    for i in range(end, start, -1):
        if text[i - 1] in _BREAK_CHARS:
            return i
    return end


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

    # 拼接时记录每块在全文中的起始偏移，供 page_no 回查
    spans: list[tuple[int, int, Block]] = []
    cursor = 0
    pieces: list[str] = []
    for i, block in enumerate(parsed.blocks):
        if i > 0:
            pieces.append("\n\n")
            cursor += 2
        start = cursor
        pieces.append(block.text)
        cursor += len(block.text)
        spans.append((start, cursor, block))
    full_text = "".join(pieces)

    def page_at(offset: int) -> int | None:
        for start, end, block in spans:
            if start <= offset < end:
                return block.page_no
        return None

    def heading_at(offset: int) -> str | None:
        for start, end, block in spans:
            if start <= offset < end:
                return block.heading_path or block.text[:40]
        return None

    chunks: list[Chunk] = []
    pos = 0
    total = len(full_text)
    ordinal = 0
    while pos < total:
        hard_end = min(pos + chunk_size, total)
        end = hard_end if hard_end == total else _find_break(full_text, pos, hard_end)
        if end <= pos:
            end = hard_end
        raw = full_text[pos:end]
        stripped = raw.strip()
        if stripped:
            leading = len(raw) - len(raw.lstrip())
            # content 必须是 full_text 的原样切片：citations 靠 char_start/char_end
            # 回原文，一旦在这里做内部空白折叠，偏移就拼不回原样了（真实踩过的坑）。
            content = stripped
            start = pos + leading
            chunks.append(
                Chunk(
                    ordinal=ordinal,
                    content=content,
                    page_no=page_at(start),
                    heading_path=heading_at(start),
                    char_start=start,
                    char_end=start + len(stripped),
                    token_count=max(1, len(stripped) // 2),
                    locator_ref=f"offsets:{start}-{start + len(stripped)}",
                )
            )
            ordinal += 1
        if end >= total:
            break
        pos = max(end - chunk_overlap, pos + 1)

    if not chunks:
        raise AppError(ErrorCode.PARSE_INCOMPLETE, "解析成功但切不出任何 chunk")
    return chunks
