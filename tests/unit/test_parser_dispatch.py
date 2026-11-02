"""接线对账：后缀白名单 ⇄ `parsers/` 子包 ⇄ 派发表，三者不许漂移。

ADR 0008 D4 把格式从 5 类扩到 14 类，但真正容易出错的地方不是解析函数本身，
而是**三张表各写一份**：`parser.SUPPORTED_SUFFIXES`（上传入口用它判白名单）、
`parser._PARSUB_MODULES`（分派用）、以及每个 `parsers/*.py` 里的 `SUFFIXES`
（模块自己认领什么）。漏一处不会报错，只会表现为「上传成功、解析走错分支」或
「模块写好了但永远进不去」，所以这里逐项对账。
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.services.ingest.parser import (
    _PARSUB_MODULES,
    SUPPORTED_MIME,
    SUPPORTED_SUFFIXES,
    ParsedText,
    parse,
)


def _module(name: str) -> object:
    return importlib.import_module(f"pyknora.services.ingest.parsers.{name}")


def test_every_dispatched_suffix_is_allowed() -> None:
    """派发表里的后缀必须都在白名单里，否则永远到不了。"""
    missing = set(_PARSUB_MODULES) - SUPPORTED_SUFFIXES
    assert not missing, f"派发表了但白名单没有（上传即被拒）：{sorted(missing)}"


def test_every_allowed_suffix_has_a_parser() -> None:
    """白名单里的后缀必须有下落：要么在老解析器分支里，要么在派发表里。"""
    legacy = {".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm"}
    orphan = SUPPORTED_SUFFIXES - legacy - set(_PARSUB_MODULES)
    assert not orphan, f"白名单收录但没有任何解析器：{sorted(orphan)}"


def test_submodules_claim_the_suffixes_dispatched_to_them() -> None:
    """`parsers.X.SUFFIXES` 必须包含派发给 X 的每个后缀。"""
    for suffix, module_name in _PARSUB_MODULES.items():
        module = _module(module_name)
        assert suffix in module.SUFFIXES, (  # type: ignore[attr-defined]
            f"{module_name}.SUFFIXES 没认领 {suffix}"
        )


def test_submodules_expose_a_parse_entry_point() -> None:
    """签名统一为 `parse(data, filename="")`：不带 content_type，分派已经判过了。"""
    for module_name in set(_PARSUB_MODULES.values()):
        module = _module(module_name)
        entry = getattr(module, "parse", None)
        assert callable(entry), f"{module_name}.parse 不可调用"
        assert isinstance(entry, Callable)


@pytest.mark.parametrize(
    ("suffix", "module_name"),
    sorted(_PARSUB_MODULES.items()),
)
def test_dispatch_reaches_the_expected_submodule(
    suffix: str,
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真的走一遍 `parse()`：用桩替换模块入口，确认后缀路由对了、参数传对了。"""
    import pyknora.services.ingest.parser as parser_module

    importlib.import_module(f"pyknora.services.ingest.parsers.{module_name}")
    seen: dict[str, object] = {}

    def fake_parse(data: bytes, filename: str = "") -> ParsedText:
        seen["data"] = data
        seen["filename"] = filename
        return ParsedText(blocks=[], parser=f"stub:{module_name}")

    real_module = importlib.import_module(f"pyknora.services.ingest.parsers.{module_name}")
    monkeypatch.setattr(real_module, "parse", fake_parse)

    filename = f"report{suffix}"
    result = parser_module.parse(b"payload", filename)

    assert result.parser == f"stub:{module_name}"
    assert seen == {"data": b"payload", "filename": filename}


@pytest.mark.parametrize("suffix", [".doc", ".ppt", ".epub", ".zip", ".json"])
def test_unsupported_formats_are_refused_with_the_full_allowlist(suffix: str) -> None:
    """ADR 0008 D4-P3：旧二进制 Office 与压缩包一律拒收，并回可用的完整清单。"""
    with pytest.raises(AppError) as excinfo:
        parse(b"whatever", f"file{suffix}")
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_FORMAT
    allowed = excinfo.value.details["allowed"]
    assert allowed == sorted(SUPPORTED_SUFFIXES)


def test_mime_allowlist_covers_every_suffix_family() -> None:
    """MIME 白名单是给「没有后缀、只有 Content-Type」的调用方兜底的，别漏族。"""
    required = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "message/rfc822",
        "text/csv",
    }
    assert required <= SUPPORTED_MIME
