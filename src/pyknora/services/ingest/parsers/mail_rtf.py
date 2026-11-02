"""邮件（.eml）与 RTF（.rtf）解析器（N10）。

与 `parser.py` 的五个白名单解析器并列，但**不接入** `parser.parse` 的分派：
`SUFFIXES` 与 `parse(data, filename="")` 是本模块对外的唯一契约，由上层按后缀
分派进来（`parser.py` 一行不改）。

两类格式的位置映射档次不同（ADR 0008 D2）：

- `.eml`：A 档，每个块带 `ParaLocator(index=...)`。序号从 **1** 起——`ParaLocator`
  的 `index` 约束是 `ge=1`，0 会被 pydantic 直接拒绝，不是可选值。
- `.rtf`：B 档，允许 `locator=None`；本实现按 striprtf 输出中的换行（对应 RTF 的
  `\\par`）切块，每块 `locator=None`。

`striprtf` 是可选依赖：模块导入时不 import，只有真正解析 `.rtf` 时才在函数内
import；缺失时抛 `AppError(PARSE_FAILED, ...)`，而不是在 import 期就炸掉整个包
（否则没装 striprtf 的环境连 `.eml` 都用不了）。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from email import message_from_bytes, policy
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import PurePosixPath

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import ParaLocator
from pyknora.services.ingest.parser import Block, ParsedText

SUFFIXES: frozenset[str] = frozenset({".eml", ".rtf"})

_PLAIN = "text/plain"
_HTML = "text/html"

# 块级标签：在开/闭标签处断行，其余标签只剥掉。`br` 单独放在 startendtag 里。
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "section",
        "article",
        "blockquote",
        "pre",
        "hr",
    }
)


class _HTMLTextExtractor(HTMLParser):
    """最小 HTML→纯文本：剥标签、丢 script/style、块级标签处断行。

    不用 lxml（AGENTS.md §2 口径），也不追求完整 HTML 语义——邮件正文里的
    HTML 通常结构简单，剥掉标签后能读即可。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `<br/>`、`<hr/>` 走这里；不补这一支，自闭合标签会被整段丢掉。
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            if self._skip_depth:
                self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks).replace("\xa0", " ")
        lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def _html_to_text(html: str) -> str:
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor.text()


def _header_str(msg: EmailMessage, name: str) -> str:
    """取一个头字段的字符串形态。`policy.default` 下 Date 会被解析成 datetime。"""
    value = msg.get(name)
    if value is None:
        return ""
    return str(value).strip()


def _decode_part(part: EmailMessage) -> str:
    """按声明字符集解码正文 part，声明缺失/错误时回退 utf-8、gb18030。

    不用 `part.get_content()`：`policy.default` 对没声明 charset 的 text/* 默认按
    ascii 解码，中文邮件会变成一串替换符；这里拿原始字节自己决定回退顺序。
    """
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charsets: list[str] = []
    declared = part.get_content_charset()
    if declared:
        charsets.append(declared)
    charsets.extend(["utf-8", "gb18030"])
    for charset in charsets:
        try:
            return payload.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _iter_text_parts(part: EmailMessage) -> Iterator[tuple[str, str]]:
    """递归收集正文 part，跳过 attachment 与所有二进制 part。

    先判 `Content-Disposition: attachment` 再递归：`message/rfc822`（转发的邮件）
    也是 multipart 形态，先递归会把附件里的邮件正文吸进正文。
    """
    if (part.get_content_disposition() or "").lower() == "attachment":
        return
    if part.is_multipart():
        for child in part.iter_parts():
            yield from _iter_text_parts(child)
        return
    content_type = part.get_content_type()
    if content_type not in {_PLAIN, _HTML}:
        return
    text = _decode_part(part)
    if text.strip():
        yield content_type, text


def _split_paragraphs(text: str) -> list[str]:
    """纯文本按空行切段（单换行是折行，不切）。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [para.strip() for para in re.split(r"\n\s*\n", normalized) if para.strip()]


def _split_lines(text: str) -> list[str]:
    """HTML 转出的文本 / RTF 的 `\\par` 已按行断开，逐行成块。"""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_eml(data: bytes) -> ParsedText:
    if not data:
        return ParsedText(blocks=[], parser="email")

    try:
        msg = message_from_bytes(data, policy=policy.default)
    except Exception as exc:  # noqa: BLE001 — 库内异常类型随版本变化，按类名记录
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "EML 解析失败（邮件格式损坏）",
            details={"reason": type(exc).__name__},
        ) from exc

    subject = _header_str(msg, "Subject")
    try:
        blocks: list[Block] = []
        header_lines = [
            f"{label}: {value}"
            for label, value in (
                ("Subject", subject),
                ("From", _header_str(msg, "From")),
                ("Date", _header_str(msg, "Date")),
            )
            if value
        ]
        if header_lines:
            blocks.append(Block(text="\n".join(header_lines), heading_path=subject or None))

        plain_parts: list[str] = []
        html_parts: list[str] = []
        for content_type, text in _iter_text_parts(msg):
            if content_type == _PLAIN:
                plain_parts.append(text)
            else:
                html_parts.append(text)

        body_texts: list[str] = []
        if plain_parts:
            for text in plain_parts:
                body_texts.extend(_split_paragraphs(text))
        else:
            for html in html_parts:
                body_texts.extend(_split_lines(_html_to_text(html)))
    except Exception as exc:  # noqa: BLE001 — 同上
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "EML 正文解析失败（MIME 结构损坏）",
            details={"reason": type(exc).__name__},
        ) from exc

    for text in body_texts:
        blocks.append(Block(text=text, heading_path=subject or None))

    # 序号从 1 起：`ParaLocator.index` 是 `ge=1`，0 不合法（见模块 docstring）。
    for index, block in enumerate(blocks, start=1):
        block.locator = ParaLocator(index=index)

    return ParsedText(blocks=blocks, parser="email")


def _parse_rtf(data: bytes) -> ParsedText:
    if not data:
        return ParsedText(blocks=[], parser="striprtf")

    # 函数内 import：striprtf 不在项目依赖里，import 期失败会连累 .eml。
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError as exc:
        raise AppError(ErrorCode.PARSE_FAILED, "解析 RTF 需要 striprtf") from exc

    try:
        # RTF 是 7-bit + 转义；latin-1 对任意字节都无损，交给 striprtf 解 \'hh / \uNNNN。
        text = rtf_to_text(data.decode("latin-1"))
    except Exception as exc:  # noqa: BLE001 — 同上
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "RTF 解析失败（文件损坏或不是有效的 RTF）",
            details={"reason": type(exc).__name__},
        ) from exc

    # B 档：按 `\par` 切块，locator 留 None（ADR 0008 D2 允许）。
    blocks = [Block(text=line) for line in _split_lines(text)]
    return ParsedText(blocks=blocks, parser="striprtf")


def parse(data: bytes, filename: str = "") -> ParsedText:
    """按后缀解析；没给后缀时按魔数嗅探（RTF 必以 `{\\rtf` 开头，其余当邮件）。"""
    suffix = PurePosixPath(filename).suffix.lower() if filename else ""
    if suffix == ".rtf":
        return _parse_rtf(data)
    if suffix == ".eml":
        return _parse_eml(data)
    if data.lstrip().startswith(b"{\\rtf"):
        return _parse_rtf(data)
    return _parse_eml(data)
