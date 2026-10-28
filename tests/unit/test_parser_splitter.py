"""S09 / A3：解析与切片。

A3 断言：3 页 PDF 切出的 chunk 数 ≥ 3，每块 page_no 非空、char_start < char_end。
PDF 用手工构造的最小内容，不依赖外部样本文件。
"""

from __future__ import annotations

import io

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.services.ingest.parser import Block, ParsedText, parse
from pyknora.services.ingest.splitter import chunk_parsed


def _make_text_payload(paras: int) -> bytes:
    return "\n\n".join(
        f"第 {i} 段：PyKnora 增量索引测试语料，用于验证切片边界与位置映射。" * 3
        for i in range(paras)
    ).encode()


def _make_pdf(pages: int) -> bytes:
    """构造一个多页 PDF，每页一段文本。手写最小 PDF 结构，不引第三方生成器。"""
    objects: list[bytes] = []
    page_ids: list[int] = []

    # 1: Catalog, 2: Pages, 然后每页 2 个对象（Page + Contents）
    n_pages = pages
    first_page_id = 3
    for i in range(n_pages):
        page_ids.append(first_page_id + i * 2)

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids).encode()
    objects.append(
        b"<< /Type /Pages /Count " + str(n_pages).encode() + b" /Kids [" + kids + b"] >>"
    )

    for i in range(n_pages):
        # 必须是合法的内容流：文本要包在 BT/ET 里并显式设置字体，
        # 裸字符串 pypdf 提取不出来（首版就是这样，3 页全返回空）。
        # 每页写足 ~300 字符，让 chunk_size=200 必然切出多块，page_no 才能被真正验证。
        body = f"Page {i + 1} about PyKnora incremental indexing and permission isolation. " * 5
        content = f"BT /F1 12 Tf 72 720 Td ({body}) Tj ET".encode()
        page_obj = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Contents " + str(first_page_id + i * 2 + 1).encode() + b" 0 R "
            b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
            b"/BaseFont /Helvetica >> >> >> >>"
        )
        stream = (
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
        objects.append(page_obj)
        objects.append(stream)

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for idx, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(str(idx).encode() + b" 0 obj\n" + body + b"\nendobj\n")
    xref_pos = out.tell()
    count = len(objects) + 1
    out.write(b"xref\n0 " + str(count).encode() + b"\n")
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        b"trailer\n<< /Size "
        + str(count).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_pos).encode()
        + b"\n%%EOF\n"
    )
    return out.getvalue()


def test_parse_plain_text() -> None:
    parsed = parse(_make_text_payload(2), "a.txt")
    assert parsed.parser == "plain"
    assert len(parsed.blocks) == 2


def test_parse_markdown_keeps_heading_path() -> None:
    md = b"# Title\n\ncontent one\n\n## Sub\n\ncontent two\n"
    parsed = parse(md, "a.md")
    assert parsed.parser == "markdown"
    paths = [b.heading_path for b in parsed.blocks if b.heading_path]
    assert any("Title / Sub" in p for p in paths)


def test_parse_html_strips_tags() -> None:
    html = b"<html><body><p>alpha</p><script>bad()</script><p>beta</p></body></html>"
    parsed = parse(html, "a.html")
    assert parsed.parser == "regex-html"
    joined = " ".join(b.text for b in parsed.blocks)
    assert "alpha" in joined and "beta" in joined
    assert "bad()" not in joined


def test_unsupported_format_raises() -> None:
    with pytest.raises(AppError) as exc:
        parse(b"data", "a.exe")
    assert exc.value.code == ErrorCode.UNSUPPORTED_FORMAT


def test_pdf_three_pages_yields_page_numbers() -> None:
    """A3 前半：3 页 PDF 切出的 chunk 数 ≥ 3，每块 page_no 非空。"""
    parsed = parse(_make_pdf(3), "doc.pdf")
    chunks = chunk_parsed(parsed, chunk_size=200, chunk_overlap=20)
    assert len(chunks) >= 3
    assert all(c.page_no is not None for c in chunks)
    assert {c.page_no for c in chunks} == {1, 2, 3}


def test_a3_all_chunks_have_ordered_offsets() -> None:
    """A3 后半：每块 char_start < char_end 全部成立。"""
    parsed = parse(_make_pdf(3), "doc.pdf")
    chunks = chunk_parsed(parsed, chunk_size=200, chunk_overlap=20)
    assert all(c.char_start < c.char_end for c in chunks)


def test_ordinals_are_contiguous_from_zero() -> None:
    parsed = parse(_make_text_payload(3), "a.txt")
    chunks = chunk_parsed(parsed, chunk_size=120, chunk_overlap=10)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_offsets_are_global_not_block_local() -> None:
    """偏移必须是全文坐标，否则 citations 拼不回原文。"""
    parsed = ParsedText(blocks=[Block(text="AAAA"), Block(text="BBBB")], parser="test")
    chunks = chunk_parsed(parsed, chunk_size=10, chunk_overlap=0)
    full = parsed.full_text
    for c in chunks:
        assert full[c.char_start : c.char_end] == c.content


def test_overlap_must_be_less_than_chunk_size() -> None:
    parsed = ParsedText(blocks=[Block(text="x" * 100)], parser="test")
    with pytest.raises(AppError) as exc:
        chunk_parsed(parsed, chunk_size=50, chunk_overlap=50)
    assert exc.value.code == ErrorCode.INVALID_REQUEST


def test_zero_chunk_size_rejected() -> None:
    parsed = ParsedText(blocks=[Block(text="x")], parser="test")
    with pytest.raises(AppError):
        chunk_parsed(parsed, chunk_size=0)


def test_empty_parse_result_raises_parse_incomplete() -> None:
    with pytest.raises(AppError) as exc:
        parse(b"   \n\n   ", "a.txt")
    assert exc.value.code == ErrorCode.PARSE_FAILED


def test_chunk_rejects_inverted_offsets() -> None:
    from pyknora.schemas.chunk import Chunk

    with pytest.raises(ValueError):
        Chunk(ordinal=0, content="x", char_start=10, char_end=5)
