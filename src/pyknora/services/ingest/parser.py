"""S09：解析。把 bytes 变成带位置映射的 Markdown 文本块。

白名单格式见 `SUPPORTED_SUFFIXES`（ADR 0008 D4：5 类 → 14 类）。
每种解析器的输出统一为 `ParsedText`：整篇正文 + 每段的位置信息。
表格类格式（xlsx/csv/pptx/ods…）的实现在 `parsers/` 子包里，这里只做后缀路由。

**位置映射是坑**：PDF 的页码、DOCX 的段落序号、HTML 的元素顺序，
来源不同，所以这里把它们统一成 `blocks` 列表，每块带 page_no 与字节内偏移。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import PurePosixPath
from typing import Any

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import Locator

# 新增的后缀与 `_PARSUB_MODULES` 必须同步：`tests/unit/test_parser_dispatch.py`
# 会把两张表对账，漏一处就红（曾经就是靠人眼核对，漏了 .markdown）。
SUPPORTED_SUFFIXES = frozenset(
    {
        ".pdf",
        ".docx",
        ".txt",
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".xlsx",
        ".xls",
        ".csv",
        ".pptx",
        ".odt",
        ".ods",
        ".odp",
        ".eml",
        ".rtf",
    }
)
SUPPORTED_MIME = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "message/rfc822",
        "application/rtf",
        "text/rtf",
        "text/csv",
        "text/plain",
        "text/markdown",
        "text/html",
    }
)


@dataclass(slots=True)
class Block:
    """一个逻辑段落。page_no 只有 PDF 才有意义。

    `locator` / `is_tabular` 是 ADR 0008 D2/D3 的两个接线位，本节点只**承载**它们：
    四个解析器暂时都不填 `locator`（留 `None`），真正回填是后续 S2/S4 节点的事；
    `is_tabular` 只是让切分层知道「这块是表格，别按纯文本流处理」。
    """

    text: str
    page_no: int | None = None
    heading_path: str | None = None
    locator: Locator | None = None
    is_tabular: bool = False
    # 解析侧的「可复现性凭证」（ADR 0008 D9-4）：视觉块必须能回答"这段文字是哪个
    # 模型、哪版提示词、以什么置信度认出来的"，否则同一份文件两次解析出不同文本时
    # 无法判断是模型漂移还是代码回归。放 dict 而不是逐字段加，是因为承载方从 OCR
    # （`ocr_score`）到 VLM（`vision_model`/`prompt_version`）再到各格式解析器
    # （`sheet_name`/`slide_no` 之类）都不一样——固定字段会逼所有解析器背一份空壳。
    # 它**不参与**任何检索或切分逻辑，纯粹记账。
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedText:
    blocks: list[Block] = field(default_factory=list)
    parser: str = ""

    @property
    def full_text(self) -> str:
        """纯文本流的拼接结果。**表格块不参与拼接**（ADR 0008 D3-1）。

        表格块是独立单元，它的 `char_*` 是块内偏移（D3-2），拼进全文会让所有
        偏移反算错位；所以这里和 `splitter.chunk_parsed` 用同一条排除规则。
        """
        return "\n\n".join(b.text for b in self.blocks if not b.is_tabular)


def _suffix_of(filename: str, content_type: str | None) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix:
        return suffix
    if content_type:
        for s in SUPPORTED_SUFFIXES:
            if content_type == s:
                return s
    return ""


def _check_allowed(filename: str, content_type: str | None) -> str:
    suffix = _suffix_of(filename, content_type)
    if suffix not in SUPPORTED_SUFFIXES:
        raise AppError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"unsupported file type: {suffix or 'unknown'}",
            details={"allowed": sorted(SUPPORTED_SUFFIXES)},
        )
    return suffix


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _parse_pdf(data: bytes) -> ParsedText:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise AppError(ErrorCode.INTERNAL_ERROR, "pypdf 未安装") from exc

    # **底层异常必须包成 AppError**：`PdfReader` / `extract_text` 遇到截断、损坏、
    # 加密的文件会抛 `PdfStreamError` / `FileNotDecryptedError` 之类的库内异常。
    # 这些异常一旦裸奔到 Worker，只会被当成"未知错误"反复重试，而且用户拿不到
    # `PARSE_FAILED` 这个可诊断的码——文件本身坏了，重试一百次也不会变好。
    # 异常消息（可能夹着二进制碎片）不进 details，只留类型名。
    try:
        reader = PdfReader(io.BytesIO(data))
        blocks: list[Block] = []
        for page_index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            for para in re.split(r"\n\s*\n", text):
                cleaned = para.strip()
                if cleaned:
                    blocks.append(Block(text=cleaned, page_no=page_index + 1))
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 — 库内异常类型随版本变化，按类名记录即可
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "PDF 解析失败（文件损坏、截断或加密）",
            details={"reason": type(exc).__name__},
        ) from exc
    if not blocks:
        raise AppError(ErrorCode.PARSE_FAILED, "PDF 未提取到任何文本（可能是扫描件）")
    return ParsedText(blocks=blocks, parser="pypdf")


def _parse_docx(data: bytes) -> ParsedText:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover
        raise AppError(ErrorCode.INTERNAL_ERROR, "python-docx 未安装") from exc

    # 同 PDF：损坏的 zip（.docx 本质是 zip）会抛 `BadZipFile`/`KeyError`，
    # 必须转成 `PARSE_FAILED` 才能在作业层终止而不是无限重试。
    try:
        document = docx.Document(io.BytesIO(data))
        blocks: list[Block] = []
        heading_stack: list[str] = []
        for para in document.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name or "").lower()
            if style.startswith("heading"):
                level = int(style.replace("heading", "").strip() or 1)
                heading_stack = heading_stack[: level - 1] + [text]
                blocks.append(Block(text=text, heading_path=" / ".join(heading_stack)))
            else:
                blocks.append(Block(text=text, heading_path=" / ".join(heading_stack) or None))
    except Exception as exc:  # noqa: BLE001 — 同上，按类名记录
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "DOCX 解析失败（文件损坏或不是有效的 docx）",
            details={"reason": type(exc).__name__},
        ) from exc
    if not blocks:
        raise AppError(ErrorCode.PARSE_FAILED, "DOCX 未提取到任何段落")
    return ParsedText(blocks=blocks, parser="python-docx")


def _parse_text(data: bytes, *, markdown: bool) -> ParsedText:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("gb18030", errors="replace")
    blocks: list[Block] = []
    heading_stack: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            joined = "\n".join(buffer).strip()
            if joined:
                blocks.append(Block(text=joined, heading_path=" / ".join(heading_stack) or None))
            buffer.clear()

    for line in text.splitlines():
        match = _HEADING_RE.match(line) if markdown else None
        if match:
            flush()
            level = len(match.group(1))
            heading_stack = heading_stack[: level - 1] + [match.group(2).strip()]
            blocks.append(Block(text=line.strip(), heading_path=" / ".join(heading_stack)))
        elif not line.strip():
            flush()
        else:
            buffer.append(line)
    flush()
    if not blocks:
        raise AppError(ErrorCode.PARSE_FAILED, "文本文件为空或没有可索引内容")
    return ParsedText(blocks=blocks, parser="markdown" if markdown else "plain")


def _parse_html(data: bytes) -> ParsedText:
    text = data.decode("utf-8", errors="replace")
    # 去掉 script/style，再按块级标签切段；不引入 lxml 依赖
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    parts = re.split(r"(?i)</(?:p|div|li|h[1-6]|tr|section|article)>", text)
    blocks: list[Block] = []
    for part in parts:
        cleaned = re.sub(r"(?s)<[^>]+>", " ", part)
        cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
        if cleaned:
            blocks.append(Block(text=cleaned))
    if not blocks:
        raise AppError(ErrorCode.PARSE_FAILED, "HTML 未提取到任何文本")
    return ParsedText(blocks=blocks, parser="regex-html")


#: 表格/幻灯片/ODF/邮件类后缀 → `parsers/` 子包里的模块名。
#: 这些模块各自导出 `SUFFIXES` 与 `parse(data, filename="")`，签名刻意不带
#: `content_type`（分派已经按后缀做完，再传一遍只会出现两个真相）。
_PARSUB_MODULES: dict[str, str] = {
    ".xlsx": "tabular",
    ".xls": "tabular",
    ".csv": "tabular",
    ".pptx": "slides",
    ".odt": "odf",
    ".ods": "odf",
    ".odp": "odf",
    ".eml": "mail_rtf",
    ".rtf": "mail_rtf",
}


def _parse_via_submodule(module_name: str, data: bytes, filename: str) -> ParsedText:
    """延迟导入 `parsers.<module>` 并委托。

    必须延迟：`parsers/*.py` 反过来 import 本模块的 `Block`/`ParsedText`，
    在模块顶层 import 会形成循环（`parser` → `parsers.tabular` → `parser`），
    表现为首次上传 `.xlsx` 时 `ImportError: partially initialized module`。
    """
    module = import_module(f"{__package__}.parsers.{module_name}")
    return module.parse(data, filename)  # type: ignore[no-any-return]


def parse(data: bytes, filename: str, content_type: str | None = None) -> ParsedText:
    """按后缀分派解析器。格式不在白名单时抛 UNSUPPORTED_FORMAT。"""
    suffix = _check_allowed(filename, content_type)
    if suffix == ".pdf":
        return _parse_pdf(data)
    if suffix == ".docx":
        return _parse_docx(data)
    if suffix in {".md", ".markdown"}:
        return _parse_text(data, markdown=True)
    if suffix in {".html", ".htm"}:
        return _parse_html(data)
    submodule = _PARSUB_MODULES.get(suffix)
    if submodule is not None:
        return _parse_via_submodule(submodule, data, filename)
    return _parse_text(data, markdown=False)
