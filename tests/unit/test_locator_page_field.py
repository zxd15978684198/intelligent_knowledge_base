"""`TableLocator.page`：PDF 表格块回指页码（2026-11-02 补的契约缺口）。

原先 `TableLocator` 只有 `index`/`rows`/`cells`/`slide`/`sheet`，PDF 表格块因此丢掉
「在第几页」——`Block.page_no` 虽然对，但 `splitter.compact_locator()` 只读 locator，
落库后只剩「第几张表」。这里钉住三件事：字段可空、1 基、`locator_ref` 的紧凑串不变。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pyknora.schemas.locator import TableLocator
from pyknora.services.ingest.splitter import compact_locator


def test_page_is_optional() -> None:
    """DOCX 的表格在段落流里，没有页码也合法（与 `slide`/`sheet` 同口径）。"""
    locator = TableLocator(index=1, rows="R1:R3")
    assert locator.page is None


def test_page_must_be_one_based() -> None:
    with pytest.raises(ValidationError):
        TableLocator(index=1, page=0)


def test_page_coexists_with_other_containers() -> None:
    """三个容器字段互不排斥，各自表达「挂在哪个容器里」。"""
    locator = TableLocator(index=2, rows="R1:R9", page=5, slide=3, sheet="S1")
    assert (locator.page, locator.slide, locator.sheet) == (5, 3, "S1")


def test_compact_locator_stays_stable() -> None:
    """`locator_ref` 的格式**不因新字段改变**：页号活在 `locator` 的 JSONB 里。

    改串会让已落库的 `chunks.locator_ref` 与新区块两种形态并存，前端按串解析的地方
    要同时兼容两版——收益（页号）已经由 `locator` 拿到了，不值这个代价。
    """
    assert compact_locator(TableLocator(index=1, rows="R1:R3", page=7)) == "table:R1:R3"
