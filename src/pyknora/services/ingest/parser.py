"""S09：解析。把 bytes 变成带位置映射的 Markdown 文本块。

白名单格式见 `SUPPORTED_SUFFIXES`（ADR 0008 D4：5 类 → 14 类）。
每种解析器的输出统一为 `ParsedText`：整篇正文 + 每段的位置信息。
表格类格式（xlsx/csv/pptx/ods…）的实现在 `parsers/` 子包里，这里只做后缀路由。

**位置映射是坑**：PDF 的页码、DOCX 的段落序号、HTML 的元素顺序，
来源不同，所以这里把它们统一成 `blocks` 列表，每块带 page_no 与字节内偏移。
"""

from __future__ import annotations

import asyncio
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import Locator

if TYPE_CHECKING:  # pragma: no cover — 视觉信号只在 PDF 入口用到，运行时延迟引入
    from pyknora.services.ingest.parsers.vision import PageSignals

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
    #: 视觉块（OCR/VLM 认出来的）。切分层靠它把 `char_start`/`char_end` 置 `None`
    #: ——位图里没有「第几个字符」这回事（ADR 0008 D9-5 / D10-5）。
    is_visual: bool = False
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
    #: 视觉通道的降级记录（`no_vlm_config`、`ocr_slow@3`、`vision_budget@7` 之类）。
    #: 这些**不是错误**（D9-6：单页失败不整份失败），但必须能回答"这份文档为什么少了
    #: 某几页"，所以随产物一起往上走，由作业层决定要不要标 `PARSE_INCOMPLETE`。
    degraded: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        """纯文本流的拼接结果。**表格块与视觉块不参与拼接**。

        表格块的 `char_*` 是块内偏移（D3-2），拼进全文会让所有偏移反算错位；
        视觉块连原始字符流都不存在（D9-5）。所以这里和 `splitter.chunk_parsed`
        用同一条排除规则——两处必须一致，否则偏移在两处之间就对不上。
        """
        return "\n\n".join(b.text for b in self.blocks if not b.is_tabular and not b.is_visual)


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


# PDF 不再走本模块：ADR 0008 D5 把主路径换成 `pdfplumber`（字符级 bbox + 表格检测），
# 实现在 `parsers/pdf.py`（一次 open、>100 页分批、表格准入四条）。原先这里的 pypdf
# 版本已删除——留着两条路径只会让「同一份 PDF 两次解析出不同文本」多一个来源。
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
    ".pdf": "pdf",
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


@dataclass(slots=True)
class _VisualOutcome:
    """视觉通道的产物与降级记录。降级**永不抛错**（ADR 0008 D9-6）。"""

    blocks: list[Block] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {"blocks": len(self.blocks), "degraded": sorted(set(self.degraded))}


def _run_vlm(png: bytes, timeout_s: float) -> Any:
    """同步地在当前线程里跑一次 `vision.vlm_page`（它本身是协程）。

    worker 里 `parser.parse` 跑在 `asyncio.to_thread` 的线程上，那里**没有**运行中的
    事件循环，`asyncio.run` 是安全的；反过来，如果有人在协程里直接调 `parse`，
    这里会明确报错而不是偷偷起一个嵌套循环（那会得到
    `RuntimeError: asyncio.run() cannot be called from a running event loop`，
    信息量远不如这句话）。要接协程调用方，正确做法是传一个 async 编排函数，
    而不是让解析器猜。
    """
    from pyknora.services.ingest.parsers import vision

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(vision.vlm_page(png, timeout_s=timeout_s))
    raise AppError(
        ErrorCode.INTERNAL_ERROR,
        "视觉通道不能在已有事件循环的线程里同步调用（改为在 to_thread 里解析）",
    )


def _visual_blocks(pdf_bytes: bytes, signals: Sequence[PageSignals]) -> _VisualOutcome:
    """逐页决定要不要进视觉通道，并把认出来的文字变成块。

    两问的顺序是硬的（ADR 0008 D10-2）：先 `triggered_by()` 问「这页到底该不该进通道」，
    命中才 `classify_page()` 问「进去以后走 OCR 还是 VLM」。反过来先判型的话，
    纯文字页也会被判成 `text_scan`，然后白跑一遍 OCR。

    OCR 跑完还要再问一次 `escalate_to_vlm()`（D10-2 附加规则 2）：同一个框都没识别出来
    或者平均置信度太低时升级到 VLM。OCR 已经出的块**不撤销**——两份文字都留着，
    由切分层按块顺序排列，丢内容比多一段文字更糟。
    """
    from pyknora.services.ingest.parsers import vision

    outcome = _VisualOutcome()
    targets = [
        (page_no, signal)
        for page_no, signal in enumerate(signals, start=1)
        if signal.triggered_by() is not None
    ]
    if not targets:
        return outcome

    budget = vision.DocBudget()
    for page_no, signal in targets:
        kind = vision.classify_page(signal)
        try:
            png = vision.render_page(pdf_bytes, page_no)
        except Exception:  # noqa: BLE001 — pypdfium2 的异常类型随版本变，一律降级
            outcome.degraded.append(f"render_error@{page_no}")
            continue

        if vision.channel_for(kind) == "ocr":
            if not budget.claim_ocr_page():
                outcome.degraded.append(f"ocr_budget@{page_no}")
                continue
            result = vision.ocr_page(png, timeout_s=budget.page_timeout(vision.OCR_PAGE_TIMEOUT_S))
            if result.degraded:
                outcome.degraded.append(f"{result.degraded}@{page_no}")
            outcome.blocks.extend(vision.blocks_from_ocr(page_no, result))
            if not vision.escalate_to_vlm(result):
                continue

        if not budget.claim_vision_page():
            outcome.degraded.append(f"vision_budget@{page_no}")
            continue
        vlm = _run_vlm(png, budget.page_timeout(vision.OCR_PAGE_TIMEOUT_S))
        if vlm.degraded:
            outcome.degraded.append(f"{vlm.degraded}@{page_no}")
        page_bbox = (0.0, 0.0, signal.page_width or 1.0, signal.page_height or 1.0)
        outcome.blocks.extend(vision.blocks_from_vlm(page_no, vlm, page_bbox=page_bbox))
    return outcome


def _parse_pdf(data: bytes, filename: str) -> ParsedText:
    """PDF 入口：pdfplumber 抽文本层 + 视觉通道补扫描页。

    与其它格式的区别只有一处：它先拿**逐页信号**，再决定要不要动视觉通道
    （`vision.SUFFIXES` 是空集，它永远不进上面那张后缀派发表）。
    """
    from pyknora.services.ingest.parsers import pdf as pdf_parser

    parsed, signals = pdf_parser.parse_with_signals(data, filename)
    visual = _visual_blocks(data, signals)
    if visual.blocks:
        parsed.blocks.extend(visual.blocks)
    parsed.degraded.extend(visual.degraded)

    if not parsed.blocks:
        # 到这里说明：没有文本层，且视觉通道也没给出任何块（没配 key / 全超时 /
        # 全是无内容的占位块）。仍然按"没有文本层"报，但把视觉侧的账一起带上，
        # 否则排障只能看到"扫描件"三个字，看不出到底是没配还是跑挂了。
        raise AppError(
            ErrorCode.PARSE_FAILED,
            "PDF 未提取到任何文本（可能是扫描件，且视觉通道未产出内容）",
            details={
                "reason": "no_text_layer",
                "pages": len(signals),
                "visual": visual.summary(),
            },
        )
    return parsed


def parse(data: bytes, filename: str, content_type: str | None = None) -> ParsedText:
    """按后缀分派解析器。格式不在白名单时抛 UNSUPPORTED_FORMAT。"""
    suffix = _check_allowed(filename, content_type)
    if suffix == ".pdf":
        return _parse_pdf(data, filename)
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
