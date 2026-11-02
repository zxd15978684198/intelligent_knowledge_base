"""视觉触发接线的验收：信号 → 路由 → 块 → 切分。

覆盖的是 Lead 手写的接线层（`parser._visual_blocks` / `parser._parse_pdf`），
不重复 N13/N14 自己的解析与识别用例。所有视觉调用都用假实现替换：
这里要验的是"什么时候调谁"，不是"模型认得准不准"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.chunk import Chunk
from pyknora.schemas.locator import RegionLocator
from pyknora.services.ingest import parser as ingest_parser
from pyknora.services.ingest.parser import Block, ParsedText
from pyknora.services.ingest.parsers import pdf as pdf_parser
from pyknora.services.ingest.parsers import vision
from pyknora.services.ingest.parsers.vision import (
    OCR_MAX_PAGES,
    OcrBox,
    OcrResult,
    PageSignals,
    VlmResult,
)
from pyknora.services.ingest.splitter import chunk_parsed

#: 一页 A4（pt）。信号里带上它，视觉块才能算出页级 bbox。
A4 = {"page_width": 595.0, "page_height": 842.0}


def _visual_block(page: int = 2, text: str = "扫描出来的第 2 页正文") -> Block:
    return Block(
        text=text,
        page_no=page,
        is_visual=True,
        locator=RegionLocator(page=page, bbox=(0.0, 0.0, 595.0, 842.0)),
        meta={"ocr_score": 0.93},
    )


class _FakeVision:
    """假视觉通道：记录调用，可注入 OCR/VLM 结果与渲染失败。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.render_calls: list[int] = []
        self.ocr_calls: list[int] = []
        self.vlm_calls: list[int] = []
        self.ocr_result = OcrResult(
            blocks=[OcrBox(text="识别出的字", bbox=(10.0, 10.0, 200.0, 40.0), score=0.95)],
            elapsed_s=0.1,
        )
        self.vlm_result = VlmResult(text="模型读出的整页", model="fake-vlm")
        self.render_error: Exception | None = None

        def fake_render(pdf_bytes: bytes, page_no: int, *, dpi: int = vision.RENDER_DPI) -> bytes:
            self.render_calls.append(page_no)
            if self.render_error is not None:
                raise self.render_error
            return b"PNG"

        def fake_ocr(
            png: bytes,
            *,
            timeout_s: float = vision.OCR_PAGE_TIMEOUT_S,
            engine: object | None = None,
        ) -> OcrResult:
            self.ocr_calls.append(len(self.ocr_calls) + 1)
            return self.ocr_result

        def fake_vlm(png: bytes, timeout_s: float) -> VlmResult:
            self.vlm_calls.append(len(self.vlm_calls) + 1)
            return self.vlm_result

        monkeypatch.setattr(vision, "render_page", fake_render)
        monkeypatch.setattr(vision, "ocr_page", fake_ocr)
        monkeypatch.setattr(ingest_parser, "_run_vlm", fake_vlm)


# --------------------------------------------------------------------------- #
# 1. 纯数据层：视觉块不进 full_text，切出来没有偏移
# --------------------------------------------------------------------------- #


def test_full_text_excludes_visual_blocks() -> None:
    parsed = ParsedText(blocks=[Block(text="第一页正文"), _visual_block()])
    assert parsed.full_text == "第一页正文"


def test_splitter_gives_visual_chunk_no_offsets() -> None:
    parsed = ParsedText(blocks=[Block(text="正文" * 40), _visual_block(page=2)])
    chunks = chunk_parsed(parsed, chunk_size=512)

    visual = [c for c in chunks if c.locator_ref == "region:2"]
    assert len(visual) == 1
    chunk = visual[0]
    assert (chunk.char_start, chunk.char_end) == (None, None)
    assert chunk.page_no == 2
    assert chunk.page_end_no == 2
    assert chunk.locator is not None
    assert chunk.locator.kind == "region"

    # 文本块仍带真偏移，且顺序不乱（视觉块排在文档里它自己的位置）。
    offsets = [(c.char_start, c.char_end) for c in chunks if c.char_start is not None]
    assert offsets and all(start < end for start, end in offsets if start is not None)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_visual_block_without_text_produces_no_chunk() -> None:
    """VLM 失败留的空占位块不该变成一个空 chunk（它的存在记在 degraded 里）。"""
    parsed = ParsedText(blocks=[Block(text="正文"), _visual_block(text="   ")])
    chunks = chunk_parsed(parsed)
    assert all(c.char_start is not None for c in chunks)


def test_chunk_allows_null_offsets_only_as_a_pair() -> None:
    visual = Chunk(ordinal=0, content="abc", char_start=None, char_end=None)
    assert visual.length == 3

    with pytest.raises(ValueError):
        Chunk(ordinal=0, content="abc", char_start=0)
    with pytest.raises(ValueError):
        Chunk(ordinal=0, content="abc", char_start=5, char_end=5)


# --------------------------------------------------------------------------- #
# 2. 路由：谁该被调用
# --------------------------------------------------------------------------- #


def test_scan_page_goes_through_ocr_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeVision(monkeypatch)
    signals = [PageSignals(char_count=0, **A4)]

    outcome = ingest_parser._visual_blocks(b"%PDF-1.4", signals)

    assert fake.render_calls == [1]
    assert len(fake.ocr_calls) == 1
    assert fake.vlm_calls == []  # 置信度够高，不升级
    assert [b.is_visual for b in outcome.blocks] == [True]
    assert outcome.blocks[0].page_no == 1
    assert outcome.degraded == []


def test_text_page_never_touches_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeVision(monkeypatch)
    signals = [PageSignals(char_count=900, image_count=0, **A4)]

    outcome = ingest_parser._visual_blocks(b"%PDF-1.4", signals)

    assert (fake.render_calls, fake.ocr_calls, fake.vlm_calls) == ([], [], [])
    assert outcome.blocks == []


def test_structured_page_goes_straight_to_vlm(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeVision(monkeypatch)
    # 有大图 + 表格块 → classify_page 判 structured → 直接 VLM，不浪费一次 OCR。
    signals = [
        PageSignals(char_count=200, image_count=1, image_area_ratio=0.9, table_block_count=1, **A4)
    ]

    outcome = ingest_parser._visual_blocks(b"%PDF-1.4", signals)

    assert fake.ocr_calls == []
    assert len(fake.vlm_calls) == 1
    block = outcome.blocks[0]
    assert block.is_visual
    # VLM 块是页级 bbox：整页那么大（D9-5 不假装精确到框）。
    assert isinstance(block.locator, RegionLocator)
    assert block.locator.bbox == (0.0, 0.0, 595.0, 842.0)


def test_low_confidence_ocr_escalates_to_vlm(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeVision(monkeypatch)
    fake.ocr_result = OcrResult(
        blocks=[OcrBox(text="糊成一团", bbox=(0.0, 0.0, 10.0, 10.0), score=0.20)], elapsed_s=0.1
    )
    signals = [PageSignals(char_count=0, **A4)]

    outcome = ingest_parser._visual_blocks(b"%PDF-1.4", signals)

    assert len(fake.ocr_calls) == 1
    assert len(fake.vlm_calls) == 1
    # OCR 已出的块不撤销：两份文字都留着（丢内容比多一段更糟）。
    assert len(outcome.blocks) == 2
    assert all(b.is_visual for b in outcome.blocks)


def test_render_failure_is_degraded_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeVision(monkeypatch)
    fake.render_error = RuntimeError("pdfium 崩了")
    signals = [PageSignals(char_count=0, **A4)]

    outcome = ingest_parser._visual_blocks(b"%PDF-1.4", signals)

    assert outcome.blocks == []
    assert outcome.degraded == ["render_error@1"]


def test_ocr_budget_caps_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeVision(monkeypatch)
    signals = [PageSignals(char_count=0, **A4) for _ in range(OCR_MAX_PAGES + 1)]

    outcome = ingest_parser._visual_blocks(b"%PDF-1.4", signals)

    assert len(fake.ocr_calls) == OCR_MAX_PAGES
    assert f"ocr_budget@{OCR_MAX_PAGES + 1}" in outcome.degraded


# --------------------------------------------------------------------------- #
# 3. PDF 入口：合并产物、无产物时的报错口径
# --------------------------------------------------------------------------- #


def test_parse_pdf_merges_visual_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeVision(monkeypatch)
    signals = [PageSignals(char_count=0, **A4)]
    monkeypatch.setattr(
        pdf_parser,
        "parse_with_signals",
        lambda data, filename="": (ParsedText(blocks=[], parser="pdfplumber"), signals),
    )

    parsed = ingest_parser._parse_pdf(b"%PDF-1.4", "scan.pdf")

    assert parsed.parser == "pdfplumber"
    assert len(fake.render_calls) == 1
    assert [b.is_visual for b in parsed.blocks] == [True]
    assert parsed.degraded == []


def test_parse_pdf_reports_visual_ledger_when_nothing_produced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeVision(monkeypatch)
    fake.render_error = RuntimeError("boom")
    signals = [PageSignals(char_count=0, **A4), PageSignals(char_count=0, **A4)]
    monkeypatch.setattr(
        pdf_parser,
        "parse_with_signals",
        lambda data, filename="": (ParsedText(blocks=[], parser="pdfplumber"), signals),
    )

    with pytest.raises(AppError) as excinfo:
        ingest_parser._parse_pdf(b"%PDF-1.4", "scan.pdf")

    error = excinfo.value
    assert error.code is ErrorCode.PARSE_FAILED
    assert error.details["reason"] == "no_text_layer"
    assert error.details["pages"] == 2
    # 视觉侧的账必须跟着报出来，否则排障只能看到"扫描件"三个字。
    assert error.details["visual"]["degraded"] == ["render_error@1", "render_error@2"]


def test_dispatch_routes_pdf_to_vision_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = ParsedText(blocks=[Block(text="x")], parser="sentinel")
    monkeypatch.setattr(ingest_parser, "_parse_pdf", lambda data, filename: sentinel)

    assert ingest_parser.parse(b"%PDF-1.4", "a.pdf") is sentinel


# --------------------------------------------------------------------------- #
# 4. 配置样例不许悄悄丢掉多模态字段
# --------------------------------------------------------------------------- #


def test_env_samples_document_vision_keys() -> None:
    root = Path(__file__).resolve().parents[2]
    keys = (
        "PYKNORA_VISION_BASE_URL",
        "PYKNORA_VISION_API_KEY",
        "PYKNORA_VISION_MODEL",
        "PYKNORA_VISION_PAGE_TIMEOUT_S",
    )
    sample = (root / ".env.example").read_text(encoding="utf-8")
    for key in keys:
        assert key in sample, f"{key} 不在 .env.example 里"

    local = root / ".env"
    if local.exists():  # 本地 .env 不入库，缺了不算失败
        text = local.read_text(encoding="utf-8")
        for key in keys:
            assert key in text, f"{key} 不在 .env 里"
