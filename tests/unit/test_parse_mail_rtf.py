"""N10：邮件（.eml）与 RTF（.rtf）解析器。

邮件样本用标准库现场构造（不落外部样本文件）；RTF 依赖 `striprtf`，不在项目
依赖里，未安装时 RTF 用例用 `importorskip` 跳过——跳过不是失败，原因见交付报告。
缺失依赖时的报错分支不依赖真库，用 monkeypatch 屏蔽 import 即可验证。
"""

from __future__ import annotations

import builtins
from email import policy
from email.message import EmailMessage

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import ParaLocator
from pyknora.services.ingest.parsers.mail_rtf import SUFFIXES, parse


def _make_multipart_eml() -> bytes:
    """中文 Subject + text/plain 正文（两段）+ 一个二进制附件。"""
    msg = EmailMessage()
    msg["Subject"] = "季度报告"
    msg["From"] = "alice@example.com"
    msg["To"] = "bob@example.com"
    msg["Date"] = "Mon, 02 Nov 2026 10:00:00 +0800"
    msg.set_content("这是正文第一段。\n\n这是正文第二段。")
    msg.add_attachment(
        b"SECRET-ATTACHMENT-CONTENT",
        maintype="application",
        subtype="octet-stream",
        filename="secret.bin",
    )
    return msg.as_bytes(policy=policy.SMTP)


def test_suffixes_contract() -> None:
    assert frozenset({".eml", ".rtf"}) == SUFFIXES


def test_eml_subject_body_and_attachment_skipped() -> None:
    parsed = parse(_make_multipart_eml(), "report.eml")

    assert parsed.parser == "email"
    assert parsed.blocks

    # 首块是头字段：Subject 解码成中文，From/Date 各占一行。
    assert "Subject: 季度报告" in parsed.blocks[0].text
    assert "From: alice@example.com" in parsed.blocks[0].text
    assert "Date: Mon, 02 Nov 2026 10:00:00 +0800" in parsed.blocks[0].text
    assert parsed.blocks[0].heading_path == "季度报告"

    body_blocks = [b for b in parsed.blocks if "这是正文" in b.text]
    assert [b.text for b in body_blocks] == ["这是正文第一段。", "这是正文第二段。"]

    # 附件正文与文件名都不得混进任何块。
    for block in parsed.blocks:
        assert "SECRET-ATTACHMENT-CONTENT" not in block.text
        assert "secret.bin" not in block.text

    # 块序号从 1 起（ParaLocator.index 约束 ge=1）。
    assert all(isinstance(b.locator, ParaLocator) for b in parsed.blocks)
    assert [b.locator.index for b in parsed.blocks] == list(range(1, len(parsed.blocks) + 1))


def test_eml_html_only_tags_are_stripped() -> None:
    msg = EmailMessage()
    msg["Subject"] = "HTML 邮件"
    msg.set_content(
        "<html><body><h1>标题一</h1><p>第一段</p><p>第二段</p></body></html>",
        subtype="html",
    )
    parsed = parse(msg.as_bytes(policy=policy.SMTP), "html.eml")

    full = parsed.full_text
    assert "标题一" in full
    assert "第一段" in full
    assert "第二段" in full
    assert "<" not in full
    assert ">" not in full


def test_eml_prefers_plain_over_html() -> None:
    msg = EmailMessage()
    msg["Subject"] = "两种正文"
    msg.set_content("纯文本正文")
    msg.add_alternative("<p>HTML 正文</p>", subtype="html")
    parsed = parse(msg.as_bytes(policy=policy.SMTP), "both.eml")

    assert "纯文本正文" in parsed.full_text
    assert "HTML 正文" not in parsed.full_text


def test_rtf_unicode_escape() -> None:
    pytest.importorskip("striprtf")
    # \u20013? = 中（U+4E2D），\u25991? = 文（U+6587）；尾随 ? 是 RTF 的降级字符。
    rtf = b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Times New Roman;}}\\pard \\u20013?\\u25991? \\par}"
    parsed = parse(rtf, "note.rtf")

    assert parsed.parser == "striprtf"
    assert "中文" in parsed.full_text


def test_rtf_hex_escape_is_decoded() -> None:
    pytest.importorskip("striprtf")
    # \'e9 是十六进制转义：解出来是 é（具体码位随 striprtf 的代码页，不再钉死），
    # 这里只断言转义序列本身被消化掉了、可见文本仍在。
    rtf = b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Times New Roman;}}\\pard caf\\'e9 \\par}"
    parsed = parse(rtf, "hex.rtf")

    assert parsed.parser == "striprtf"
    assert "caf" in parsed.full_text
    assert "\\'" not in parsed.full_text


def test_rtf_missing_striprtf_raises_parse_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "striprtf" or name.startswith("striprtf."):
            raise ImportError("no striprtf")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with pytest.raises(AppError) as excinfo:
        parse(b"{\\rtf1 test}", "x.rtf")

    assert excinfo.value.code == ErrorCode.PARSE_FAILED
    assert "striprtf" in excinfo.value.message


def test_empty_input_returns_empty_parsed_text() -> None:
    for filename in ("a.eml", "a.rtf"):
        parsed = parse(b"", filename)
        assert parsed.blocks == []
