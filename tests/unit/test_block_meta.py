"""`Block.meta` 的记账契约（ADR 0008 D9-4）。

为什么单独立一个文件：`meta` 是 2026-11-02 给 `parser.Block` 新增的槽位，它不属于
任何一个解析器的语义，而是**跨解析器的可复现性凭证**。放在具体解析器的测试文件里，
换一个解析器的人不会去看它。
"""

from __future__ import annotations

from pyknora.schemas.locator import RegionLocator
from pyknora.services.ingest.parser import Block, ParsedText
from pyknora.services.ingest.parsers.vision import (
    OcrBox,
    OcrResult,
    VlmResult,
    blocks_from_ocr,
    blocks_from_vlm,
)


def test_block_meta_defaults_to_empty_dict() -> None:
    """老代码（不给 meta）必须完全不受影响。"""
    block = Block(text="x")
    assert block.meta == {}


def test_block_meta_not_shared_between_instances() -> None:
    """`default_factory` 而不是 `{}`：否则所有块共用一个 dict。"""
    a = Block(text="a")
    b = Block(text="b")
    a.meta["ocr_score"] = 0.9
    assert b.meta == {}


def test_block_meta_does_not_affect_full_text() -> None:
    """记账字段不参与拼接，`full_text` 的口径不能因为它变化。"""
    parsed = ParsedText(
        blocks=[
            Block(text="甲", meta={"ocr_score": 0.5}),
            Block(text="乙"),
        ]
    )
    assert parsed.full_text == "甲\n\n乙"


def test_blocks_from_ocr_carries_ocr_score() -> None:
    """OCR 块必须带置信度：同页两次识别不一致时，靠它判断是引擎抖还是代码改。"""
    result = OcrResult(
        blocks=[
            OcrBox(text="发票", bbox=(10.0, 20.0, 110.0, 40.0), score=0.93),
            OcrBox(text="合计", bbox=(10.0, 50.0, 110.0, 70.0), score=0.42),
        ]
    )
    blocks = blocks_from_ocr(3, result, dpi=72)
    assert [b.meta["ocr_score"] for b in blocks] == [0.93, 0.42]
    assert all(isinstance(b.locator, RegionLocator) for b in blocks)
    # 视觉块的 char_* 由上层置 None，I6 不适用（D9-5）——这里只确认 locator 是块级位置。
    assert [b.locator.page for b in blocks] == [3, 3]


def test_blocks_from_vlm_carries_model_and_prompt_version() -> None:
    """VLM 块必须带模型名与提示词版本（D9-4 的直接要求）。"""
    result = VlmResult(text="整页表格转写", model="gpt-4o-mini", prompt_version="vlm-ocr/1")
    (block,) = blocks_from_vlm(7, result, page_bbox=(0.0, 0.0, 612.0, 792.0))
    assert block.meta["vision_model"] == "gpt-4o-mini"
    assert block.meta["prompt_version"] == "vlm-ocr/1"
    assert block.meta["degraded"] == ""


def test_blocks_from_vlm_keeps_placeholder_with_degraded_reason() -> None:
    """失败页也留块（D9-6），且降级原因进 meta，而不是静默消失。"""
    result = VlmResult(text="", degraded="no_vlm_config")
    (block,) = blocks_from_vlm(1, result, page_bbox=(0.0, 0.0, 612.0, 792.0))
    assert block.text == ""
    assert block.meta["degraded"] == "no_vlm_config"
