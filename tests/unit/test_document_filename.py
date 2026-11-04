"""`filename_from_object_key` 的纯函数用例（前端计划 F0 / D3 的唯一后端补项）。"""

from __future__ import annotations

import pytest

from pyknora.schemas.document import filename_from_object_key


def test_standard_object_key_yields_last_segment() -> None:
    key = "objects/11111111-1111-1111-1111-111111111111/abc123def456/员工手册v2.docx"
    assert filename_from_object_key(key) == "员工手册v2.docx"


def test_ascii_name_with_spaces_and_dots() -> None:
    key = "objects/ws/hash/2026 Q3 report.v1.final.pdf"
    assert filename_from_object_key(key) == "2026 Q3 report.v1.final.pdf"


def test_missing_key_is_none() -> None:
    assert filename_from_object_key(None) is None
    assert filename_from_object_key("") is None


def test_key_without_directory_part_is_none() -> None:
    # 没有 "/" 的键不是我们写入的格式，宁可返回 None 由前端回落，也不要瞎猜。
    assert filename_from_object_key("bare-name.pdf") is None


def test_trailing_slash_is_none() -> None:
    assert filename_from_object_key("objects/ws/hash/") is None


@pytest.mark.parametrize("dot_segment", [".", ".."])
def test_dot_segments_are_rejected(dot_segment: str) -> None:
    assert filename_from_object_key(f"objects/ws/hash/{dot_segment}") is None


def test_nested_filename_keeps_only_last_segment() -> None:
    """已知边界：上传文件名里带 "/" 时对象键会出现两层目录，这里只能取末段。

    这不是 bug：`document_versions.object_key` 是拼接出来的，没有转义；
    与其返回一个含 "/" 的假名字，不如返回末段并在计划里登记这条限制。
    """
    key = "objects/ws/hash/dir/sub/file.pdf"
    assert filename_from_object_key(key) == "file.pdf"
