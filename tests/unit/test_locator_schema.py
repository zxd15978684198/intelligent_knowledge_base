"""N1 / ADR 0008 D2：`locator` 契约（discriminated union）。

只验类型本身：6 种 kind 的往返、非法输入的拒绝、`inferred` 缺省与保真。
不验 parser / splitter 的接线（那是后续节点的事），也不碰数据库。
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from pyknora.schemas.locator import (
    LOCATOR_KINDS,
    Locator,
    PageLocator,
    ParaLocator,
    RegionLocator,
    SheetLocator,
    SlideLocator,
    TableLocator,
)

_LOCATOR = TypeAdapter(Locator)

# kind → 一个合法载荷 + 期望的具体模型类。
_VALID_CASES = [
    (
        {"kind": "page", "page": 3, "heading": "第 3 章 检索"},
        PageLocator,
    ),
    (
        {"kind": "para", "index": 12, "heading": "2.1 增量索引"},
        ParaLocator,
    ),
    (
        {"kind": "sheet", "sheet": "Sheet1", "cells": "B2:C4"},
        SheetLocator,
    ),
    (
        {"kind": "slide", "slide": 5, "shape": "Title 1"},
        SlideLocator,
    ),
    (
        {"kind": "table", "index": 2, "rows": "R1200:R1350", "cells": "A1:D10"},
        TableLocator,
    ),
    (
        {"kind": "region", "page": 1, "bbox": (0.0, 0.0, 595.0, 842.0)},
        RegionLocator,
    ),
]


@pytest.mark.parametrize(("payload", "expected"), _VALID_CASES, ids=LOCATOR_KINDS)
def test_roundtrip_python(payload: dict[str, object], expected: type) -> None:
    """6 种 kind 各一次：dict → 模型 → dict 再解析，形态与值都不变。"""
    first = _LOCATOR.validate_python(payload)
    assert isinstance(first, expected)
    assert first.kind == expected.model_fields["kind"].default

    dumped = first.model_dump()
    second = _LOCATOR.validate_python(dumped)
    assert second == first
    assert second.model_dump() == dumped


@pytest.mark.parametrize(("payload", "expected"), _VALID_CASES, ids=LOCATOR_KINDS)
def test_roundtrip_json(payload: dict[str, object], expected: type) -> None:
    """JSON 往返：`model_dump_json()` 出去的，`validate_json()` 得能收回来。"""
    first = _LOCATOR.validate_python(payload)
    assert isinstance(first, expected)

    again = _LOCATOR.validate_json(first.model_dump_json())
    assert again == first
    # region 的 bbox 在 JSON 里是数组，回到 python 必须还原成 4 元 tuple。
    if first.kind == "region":
        assert isinstance(again.bbox, tuple)
        assert again.bbox == first.bbox


def test_locator_kinds_are_the_six_literals() -> None:
    assert LOCATOR_KINDS == ("page", "para", "sheet", "slide", "table", "region")


def test_inferred_defaults_to_false() -> None:
    """真定位不带标记；只有回填/推断出来的位置才置 true。"""
    assert PageLocator(page=1).inferred is False
    assert _LOCATOR.validate_python({"kind": "page", "page": 1}).inferred is False


def test_inferred_true_survives_roundtrip() -> None:
    """迁移 0014 的回填形态：`{"kind": "para", "index": null, "inferred": true}`。"""
    payload = {"kind": "para", "index": None, "inferred": True}
    first = _LOCATOR.validate_python(payload)
    assert isinstance(first, ParaLocator)
    assert first.inferred is True
    assert _LOCATOR.validate_python(first.model_dump()) == first
    assert _LOCATOR.validate_json(first.model_dump_json()) == first


def test_para_index_is_optional() -> None:
    assert ParaLocator().index is None
    assert ParaLocator(index=1).index == 1


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        _LOCATOR.validate_python({"kind": "pdf", "page": 1})


def test_missing_kind_rejected() -> None:
    """没有 kind 就分派不了，不能默认落成 page。"""
    with pytest.raises(ValidationError):
        _LOCATOR.validate_python({"page": 1})


def test_page_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        _LOCATOR.validate_python({"kind": "page", "page": 0})


def test_region_page_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        _LOCATOR.validate_python({"kind": "region", "page": 0, "bbox": (0, 0, 1, 1)})


def test_slide_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        _LOCATOR.validate_python({"kind": "slide", "slide": 0})


def test_para_index_zero_rejected() -> None:
    """null 合法（存量回填），0 不合法（序号从 1 起）。"""
    with pytest.raises(ValidationError):
        _LOCATOR.validate_python({"kind": "para", "index": 0})


def test_table_index_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        _LOCATOR.validate_python({"kind": "table", "index": 0})


def test_sheet_empty_name_rejected() -> None:
    with pytest.raises(ValidationError):
        _LOCATOR.validate_python({"kind": "sheet", "sheet": ""})


@pytest.mark.parametrize("cells", ["B2:C", "A", "1:2", "b2:c4", "B02"])
def test_sheet_cells_invalid_rejected(cells: str) -> None:
    with pytest.raises(ValidationError):
        _LOCATOR.validate_python({"kind": "sheet", "sheet": "Sheet1", "cells": cells})


@pytest.mark.parametrize("cells", ["A1", "B2:C4", "AAA100"])
def test_sheet_cells_valid_accepted(cells: str) -> None:
    loc = _LOCATOR.validate_python({"kind": "sheet", "sheet": "Sheet1", "cells": cells})
    assert loc.cells == cells


@pytest.mark.parametrize("rows", ["R0", "R1:R", "r1:r2", "R01"])
def test_table_rows_invalid_rejected(rows: str) -> None:
    with pytest.raises(ValidationError):
        _LOCATOR.validate_python({"kind": "table", "index": 1, "rows": rows})


def test_table_rows_range_accepted() -> None:
    loc = _LOCATOR.validate_python({"kind": "table", "index": 3, "rows": "R1200:R1350"})
    assert isinstance(loc, TableLocator)
    assert loc.rows == "R1200:R1350"


def test_bbox_wrong_length_rejected() -> None:
    """给 3 个值必须报错，而不是补一个默认 0 悄悄放过。"""
    with pytest.raises(ValidationError):
        _LOCATOR.validate_python({"kind": "region", "page": 1, "bbox": (0.0, 0.0, 10.0)})


def test_bbox_x0_gt_x1_rejected() -> None:
    with pytest.raises(ValidationError, match="bbox x0"):
        _LOCATOR.validate_python({"kind": "region", "page": 1, "bbox": (10.0, 0.0, 5.0, 20.0)})


def test_bbox_y0_gt_y1_rejected() -> None:
    with pytest.raises(ValidationError, match="bbox y0"):
        _LOCATOR.validate_python({"kind": "region", "page": 1, "bbox": (0.0, 20.0, 5.0, 10.0)})


def test_bbox_accepts_list_and_normalizes_to_tuple() -> None:
    """JSONB 里读回来的必然是数组；进模型后统一成 tuple。"""
    loc = _LOCATOR.validate_python({"kind": "region", "page": 1, "bbox": [0, 0, 595, 842]})
    assert isinstance(loc, RegionLocator)
    assert loc.bbox == (0.0, 0.0, 595.0, 842.0)
    assert isinstance(loc.bbox, tuple)


def test_bbox_zero_area_allowed() -> None:
    """退化框（x0 == x1）不算错：OCR 的单行文本块可能只有一条线高。"""
    loc = _LOCATOR.validate_python({"kind": "region", "page": 1, "bbox": (5.0, 5.0, 5.0, 5.0)})
    assert loc.bbox == (5.0, 5.0, 5.0, 5.0)


def test_unknown_field_rejected() -> None:
    """契约是封闭的：拼错字段名要立刻报错，而不是静默丢弃。"""
    with pytest.raises(ValidationError):
        _LOCATOR.validate_python({"kind": "page", "page": 1, "pag": 2})


def test_locator_is_frozen() -> None:
    loc = PageLocator(page=1)
    with pytest.raises(ValidationError):
        loc.page = 2  # type: ignore[misc]
