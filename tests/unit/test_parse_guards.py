"""N3 / D6 第一道闸：解析前的纯逻辑预检。

每个阈值都一正一反：**恰好等于上限放行，多 1 就抛**（闭区间语义，见
`services/ingest/guards.py` 模块 docstring）。

ZIP 体积用桩顶替真实的 200 MiB 归档：`check_zip_size` 只消费 `infolist()`，
而 `ZipInfo.file_size` 是普通属性，所以不必在内存里真写 200 MiB，判定逻辑仍是
被测的那一段；另有一条用真实 `ZipFile` 的用例，防止桩与真实对象行为漂移。
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from typing import cast

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.services.ingest.guards import (
    MAX_ODF_REPEATED,
    MAX_PAGES,
    MAX_ROWS,
    MAX_SLIDES,
    MAX_UNCOMPRESSED_BYTES,
    ParseGuardError,
    check_document_limits,
    check_odf_repeated,
    check_zip_size,
)


class _StubZip:
    """只实现 `infolist()`，用来声明任意解压体积而不真的写那么多字节。"""

    def __init__(self, sizes: list[int]) -> None:
        self._entries: list[zipfile.ZipInfo] = []
        for i, size in enumerate(sizes):
            info = zipfile.ZipInfo(f"part-{i}.bin")
            info.file_size = size
            self._entries.append(info)

    def infolist(self) -> list[zipfile.ZipInfo]:
        return list(self._entries)


def _checked(zip_sizes: list[int]) -> None:
    check_zip_size(cast(zipfile.ZipFile, _StubZip(zip_sizes)))


# --------------------------------------------------------------------------- ZIP


def test_zip_exactly_at_limit_passes() -> None:
    assert _checked([MAX_UNCOMPRESSED_BYTES]) is None


def test_zip_one_byte_over_limit_raises() -> None:
    with pytest.raises(ParseGuardError) as excinfo:
        _checked([MAX_UNCOMPRESSED_BYTES + 1])
    details = excinfo.value.details
    assert details["limit"] == MAX_UNCOMPRESSED_BYTES
    assert details["actual"] == MAX_UNCOMPRESSED_BYTES + 1


def test_zip_many_small_files_accumulate() -> None:
    """每个文件都远小于上限，但累加超限——证明判的是总和而不是单文件。"""
    part = 70 * 1024 * 1024  # 70 MiB，单条远低于 200 MiB
    sizes = [part, part, part]
    with pytest.raises(ParseGuardError) as excinfo:
        _checked(sizes)
    actual = excinfo.value.details["actual"]
    assert actual == sum(sizes)
    assert actual > MAX_UNCOMPRESSED_BYTES


def test_zip_empty_archive_passes() -> None:
    assert _checked([]) is None


def test_zip_small_real_archive_passes() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.xml", "<office:document-content/>" * 100)
        zf.writestr("styles.xml", "<x/>")
    with zipfile.ZipFile(buf) as zf:
        assert check_zip_size(zf) is None


# --------------------------------------------------------------------- ODF 重复声明


def test_odf_rows_exactly_at_limit_passes() -> None:
    xml = f'<table:table-row table:number-rows-repeated="{MAX_ODF_REPEATED}"/>'
    assert check_odf_repeated(xml) is None


def test_odf_rows_over_limit_raises() -> None:
    xml = f'<table:table-row table:number-rows-repeated="{MAX_ODF_REPEATED + 1}"/>'
    with pytest.raises(ParseGuardError) as excinfo:
        check_odf_repeated(xml)
    details = excinfo.value.details
    assert details["metric"] == "number-rows-repeated"
    assert details["limit"] == MAX_ODF_REPEATED
    assert details["actual"] == MAX_ODF_REPEATED + 1


def test_odf_columns_exactly_at_limit_passes() -> None:
    xml = f'<x table:number-columns-repeated="{MAX_ODF_REPEATED}"/>'
    assert check_odf_repeated(xml) is None


def test_odf_columns_over_limit_raises() -> None:
    xml = f'<x table:number-columns-repeated="{MAX_ODF_REPEATED + 1}"/>'
    with pytest.raises(ParseGuardError) as excinfo:
        check_odf_repeated(xml)
    details = excinfo.value.details
    assert details["metric"] == "number-columns-repeated"
    assert details["limit"] == MAX_ODF_REPEATED
    assert details["actual"] == MAX_ODF_REPEATED + 1


def test_odf_without_namespace_prefix_raises() -> None:
    with pytest.raises(ParseGuardError):
        check_odf_repeated(f'<x number-rows-repeated="{MAX_ODF_REPEATED + 1}"/>')


def test_odf_single_quote_and_whitespace_raises() -> None:
    with pytest.raises(ParseGuardError):
        check_odf_repeated(f"<x table:number-rows-repeated = '{MAX_ODF_REPEATED + 1}'/>")


def test_odf_bytes_input_raises() -> None:
    xml = f'<x table:number-rows-repeated="{MAX_ODF_REPEATED + 1}"/>'.encode()
    with pytest.raises(ParseGuardError):
        check_odf_repeated(xml)


def test_odf_leading_zeros_are_compared_numerically() -> None:
    zero_padded_ok = f'<x table:number-rows-repeated="000{MAX_ODF_REPEATED}"/>'
    assert check_odf_repeated(zero_padded_ok) is None
    with pytest.raises(ParseGuardError):
        check_odf_repeated(f'<x table:number-rows-repeated="000{MAX_ODF_REPEATED + 1}"/>')


def test_odf_huge_digit_value_does_not_crash() -> None:
    """超长数字串不得触发 Python 3.12 的 int 转换上限（4300 位）。"""
    huge = "1" + "0" * 5000
    with pytest.raises(ParseGuardError) as excinfo:
        check_odf_repeated(f'<x table:number-rows-repeated="{huge}"/>')
    assert excinfo.value.details["actual"] == huge


def test_odf_normal_content_passes() -> None:
    xml = (
        "<office:document-content>"
        '<table:table table:name="S1">'
        '<table:table-row table:number-rows-repeated="3">'
        '<table:table-cell table:number-columns-repeated="6"/>'
        "</table:table-row>"
        "</table:table>"
        "</office:document-content>"
    )
    assert check_odf_repeated(xml) is None


def test_odf_unrelated_attributes_pass() -> None:
    assert check_odf_repeated('<x table:style-name="ce1" number-rows="99999999"/>') is None


# --------------------------------------------------------------------- 单文档规模


def test_document_limits_all_none_passes() -> None:
    assert check_document_limits() is None


@pytest.mark.parametrize(
    ("kwargs", "metric", "limit"),
    [
        ({"rows": MAX_ROWS}, "rows", MAX_ROWS),
        ({"pages": MAX_PAGES}, "pages", MAX_PAGES),
        ({"slides": MAX_SLIDES}, "slides", MAX_SLIDES),
    ],
)
def test_document_limits_exactly_at_limit_passes(
    kwargs: dict[str, int], metric: str, limit: int
) -> None:
    assert check_document_limits(**kwargs) is None
    assert check_document_limits(rows=1, pages=1, slides=1) is None


@pytest.mark.parametrize(
    ("kwargs", "metric", "limit"),
    [
        ({"rows": MAX_ROWS + 1}, "rows", MAX_ROWS),
        ({"pages": MAX_PAGES + 1}, "pages", MAX_PAGES),
        ({"slides": MAX_SLIDES + 1}, "slides", MAX_SLIDES),
    ],
)
def test_document_limits_over_limit_raises(kwargs: dict[str, int], metric: str, limit: int) -> None:
    with pytest.raises(ParseGuardError) as excinfo:
        check_document_limits(**kwargs)
    details = excinfo.value.details
    assert details["metric"] == metric
    assert details["limit"] == limit
    assert details["actual"] == limit + 1


def test_document_limits_only_checks_given_dimensions() -> None:
    assert check_document_limits(rows=MAX_ROWS) is None
    assert check_document_limits(pages=MAX_PAGES) is None


# --------------------------------------------------------------------- 错误体形状


@pytest.mark.parametrize(
    "trigger",
    [
        lambda: _checked([MAX_UNCOMPRESSED_BYTES + 1]),
        lambda: check_odf_repeated(f'<x number-rows-repeated="{MAX_ODF_REPEATED + 1}"/>'),
        lambda: check_document_limits(rows=MAX_ROWS + 1),
    ],
)
def test_guard_error_is_app_error_with_details(trigger: Callable[[], None]) -> None:
    with pytest.raises(ParseGuardError) as excinfo:
        trigger()
    err = excinfo.value
    assert isinstance(err, AppError)
    assert err.code == ErrorCode.PARSE_FAILED
    assert err.http_status == 422
    assert err.retryable is False
    assert set(err.details) >= {"limit", "actual"}
    assert isinstance(err.details["limit"], int)
