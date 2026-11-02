"""文档解析的视觉双通道：页面光栅化 → 扫描件 OCR / 结构化 VLM。

对应 ADR 0008 的 **D9（触发判定与三信号）**、**D10（双通道与预算）**，以及已决的
**D10-8（保持 20s 页预算、超时降级 `ocr_slow`，不整单失败）**。

## 这个模块负责什么

1. **渲染**：`render_page()` / `PdfPages` —— 用 `pypdfium2` 把 PDF 单页转 PNG，
   一份 PDF 只 `open()` 一次（多页循环请用 `PdfPages`，别逐页调 `render_page`）。
2. **OCR**：`ocr_page()` —— `rapidocr-onnxruntime`，引擎懒加载 + 进程内复用，
   返回每个识别框的 bbox / 文本 / 置信度，按阅读顺序排好。
3. **判型**：`classify_page()` + `PageSignals` —— D9-2 三信号 + 抽到的块类型，纯函数。
4. **路由**：`channel_for()` —— `text_scan`→OCR，`structured`/`uncertain`→VLM。
5. **VLM**：`vlm_page()` —— 走项目现有 `chat_*` 配置（`vision_*` 逐项回落，口径在
   `Settings.resolve_vision()`），用仓库已有的 `httpx`，不引 SDK。没配 key →
   返回 `degraded="no_vlm_config"`。
6. **预算**：`DocBudget` —— 四个模块常量 + 整份文档的页数/时长记账。
7. **产物**：`blocks_from_ocr()` / `blocks_from_vlm()` —— 转成 `Block` +
   `RegionLocator`，供上层 splitter 合并。

## 这个模块**不**负责什么

- **不做接线**：不 `import` 别的 `parsers/*`，不改 `parser.py` / `splitter.py` /
  `workers/`，也不产生 `ParsedText`。谁在 `_parse_pdf` 里调它、视觉块怎么切，是 Lead 的事。
- **不产出 `char_start`/`char_end`**：视觉块由位图而来，原始字符流根本不存在，
  所以 `char_*` 由上层置 `None`（ADR 0008 D9-5 补充 / D10-5：**I6 不适用于视觉块**）。
- **不判"这一页要不要进通道"**：那是调用方看 `PageSignals.triggered_by()` 的事。

## 坐标系约定（本节点确定，需 ADR 追认）

`RegionLocator.bbox` 在本模块一律是 **PDF 点（1/72 英寸）、原点在页面左上、y 轴向下**：

- OCR 引擎吐的是**输入位图的像素坐标**（原点左上、y 向下），所以 `OcrBox.bbox` 保留像素；
  `blocks_from_ocr()` 用 `72/dpi` 换算成 pt —— 这就是"渲染用的 DPI"必须一起传的原因。
- 为什么不用 PDF 原生原点（左下）：`pdfplumber` 的 `x0/top/x1/bottom` 也是左上原点，
  同一个 `kind="region"` 的块在文档里只有一种单位、一种方向，才不会一半能对上一半对不上。
- **坐标原点方向目前没有 ADR 明文**（`schemas/locator.py` 只写了"单位随解析器（PDF 为 pt）"，
  且它不在本节点可写范围）。这是一条要 Lead 拍板的契约缺口，已写进回报。

## 降级口径（一律返回值，不抛）

| 值 | 出在哪 | 含义 |
| --- | --- | --- |
| `ocr_slow` | `OcrResult.degraded` | 页预算 20s 超时（D10-8 已决：不整单失败） |
| `ocr_error` | `OcrResult.degraded` | 引擎在识别这一页时抛了异常 |
| `no_vlm_config` | `VlmResult.degraded` | 视觉与 chat 都没有 key，**不发任何网络请求** |
| `vlm_timeout` / `vlm_unreachable` | `VlmResult.degraded` | 读超时 / 连不上 |
| `vlm_http_error` / `vlm_bad_response` | `VlmResult.degraded` | 4xx–5xx / 响应体不是 JSON |

失败要标 `PARSE_INCOMPLETE` 时用 `DocBudget.error_code`：**`ErrorCode.PARSE_INCOMPLETE`
已经存在**（`core/errors.py:26`，映射 422），本节点没有新增也没有改动任何错误码。
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

import httpx

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.locator import RegionLocator
from pyknora.services.ingest.parser import Block

if TYPE_CHECKING:
    from pyknora.core.settings import Settings

__all__ = [
    "DOC_TOTAL_BUDGET_S",
    "OCR_MAX_PAGES",
    "OCR_MIN_SCORE",
    "OCR_PAGE_TIMEOUT_S",
    "RENDER_DPI",
    "SUFFIXES",
    "VISION_IMAGE_AREA_RATIO",
    "VISION_MAX_PAGES",
    "VISION_MIN_CHARS",
    "VLM_PROMPT_VERSION",
    "Channel",
    "DocBudget",
    "OcrBox",
    "OcrResult",
    "PageKind",
    "PageSignals",
    "PdfPages",
    "VlmResult",
    "blocks_from_ocr",
    "blocks_from_vlm",
    "channel_for",
    "classify_page",
    "close_engine",
    "close_executor",
    "close_renderer",
    "escalate_to_vlm",
    "looks_like_sentence",
    "ocr_page",
    "render_page",
    "reset_vlm_cache",
    "vlm_page",
]

# --------------------------------------------------------------------------- #
# 格式声明
# --------------------------------------------------------------------------- #

#: 本模块负责的后缀：**空集**。
#: 视觉双通道不是"按后缀分派"的格式解析器——页循环在 `parser.py` 的 PDF 分支里，
#: 由 Lead 接线（ADR 0008 D10-7 影响面表第 1 行）。保留这个名字只是让 `parsers/`
#: 的模块形状一致（`tabular`/`slides`/`odf`/`mail_rtf` 都导出 `SUFFIXES`），
#: 任何机械接线读到空集都不会把本模块误注册成 `.pdf` 的解析器。
SUFFIXES: frozenset[str] = frozenset()

# --------------------------------------------------------------------------- #
# 预算与阈值（每个值都要能说出出处，别在这里发明参数）
# --------------------------------------------------------------------------- #

#: 页面光栅化 DPI。ADR 0008 D10-4 实测 200dpi：A4 → 1654×2339，0.02–0.10 s/页，
#: 且"DPI 缩放几乎无收益"（OCR 耗时几乎全在识别阶段），所以不在这里省。
RENDER_DPI = 200

#: 单页 OCR 预算。**ADR 0008 D10-8 已决：保持 20s**，超时降级成 `ocr_slow`，
#: 不许整份文档失败。这个数字低于实测分布（`docs/plans/parsing-bench-ocr.md`：
#: 16 次单页识别 min 7.32 / median 13.62 / mean 16.30 / max 35.03 s，>20s 占 18.75%），
#: 所以"超时"不是异常而是常态——**降级路径必须比成功路径更可靠**。
OCR_PAGE_TIMEOUT_S = 20.0

#: 单份文档最多 OCR 多少页（ADR 0008 D9-7 / D10 已决第 4 条）。
#: 超出的页不静默跳过：`DocBudget` 记一条 `ocr_max_pages`，调用方据此标 `PARSE_INCOMPLETE`。
OCR_MAX_PAGES = 20

#: 单份文档最多送多少页给 VLM（同上）。50 是 D9-7 的默认值，不是成本模型算出来的。
VISION_MAX_PAGES = 50

#: 整份文档的视觉通道总预算（ADR 0008 D9-7）。与 90s 租约解耦：租约靠 D6 心跳续，
#: 这里只兜住"40 页密集扫描件串行 9–10 分钟"这种病态输入。
DOC_TOTAL_BUDGET_S = 600.0

#: D9-2 信号 S2 的"文本稀疏"阈值（字符数）。
#: ADR D9-2 要求"阈值全部进配置"，但 `core/settings.py` 不在本节点的可写范围，
#: 所以先落成模块常量，取值是**保守猜测**——仓库里 0 份真实扫描件，任何具体值都无样本
#: 可验（ADR 未验证项原文）。接线时请把它提到 `Settings.vision_min_chars`。
VISION_MIN_CHARS = 50

#: D9-2 信号 S3 的"图片面积占比"阈值。同上：无样本可验，取保守值（宁可误触发）。
VISION_IMAGE_AREA_RATIO = 0.6

#: D10-2"OCR 置信度 < `ocr_min_score` 时升级 VLM"的默认阈值。
#: 实测扫描件整页均值 0.997、最低单框 0.979（D10-4），所以 0.85 只拦"明显读不准"的框，
#: 不会把正常页推给 VLM —— D10-2 附加规则 2 说的正是"别不分青红皂白升级"。
OCR_MIN_SCORE = 0.85

#: 提示词版本号。D9-4 的缓存键与块元数据都要带上它：换了提示词必须让旧缓存失效，
#: 否则"这段字是哪版提示词识别的"永远说不清。**改提示词必须同时改这个号。**
VLM_PROMPT_VERSION = "v1"

_VLM_PROMPT = (
    "你在做文档页面的结构化转写。请把这张页面图片里的内容转成 Markdown："
    "表格用 Markdown 表格并保持行列对应；多栏按阅读顺序合并成一栏；"
    "公式能转就转成行内 LaTeX。"
    "不要补写图片里没有的字，不要总结、不要解释、不要加代码围栏。"
    "确实认不出的字用 ▯ 代替。只输出转写结果本身。"
)

#: 建连单独压上限（读超时用页级预算）。口径与 `services/rag/answer.py:252-262` 一致。
_CONNECT_TIMEOUT_S = 10.0

#: 行聚类容差 = 行高的这个倍数。经验值，**没有在真实扫描件上调过**（见回报的未验证项）。
_LINE_TOLERANCE_RATIO = 0.6

PageKind = Literal["text_scan", "structured", "uncertain"]
Channel = Literal["ocr", "vlm"]


# --------------------------------------------------------------------------- #
# 结果类型
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class OcrBox:
    """一个识别框。

    `bbox` 是**输入位图的像素坐标**（原点左上、y 向下），不是 PDF pt ——
    换算由 `blocks_from_ocr(..., dpi=...)` 做，因为只有调用方知道自己渲染时用了多少 DPI。
    """

    text: str
    bbox: tuple[float, float, float, float]
    score: float


@dataclass(slots=True)
class OcrResult:
    """一页的 OCR 结果。

    `degraded` 非空表示**这一页没读到东西**，值即原因（见模块 docstring 的降级口径表）。
    这时 `blocks` 一定是空的，而且**不抛异常**——D9-6 / D10-8：单页失败不整份失败。
    """

    blocks: list[OcrBox] = field(default_factory=list)
    degraded: str | None = None
    elapsed_s: float = 0.0

    @property
    def text(self) -> str:
        """整页文本（按阅读顺序，行间换行）。便于日志与自测，不参与 `Block` 产物。"""
        return "\n".join(box.text for box in self.blocks)


@dataclass(slots=True)
class VlmResult:
    """一页的 VLM 转写结果。

    `model` 与 `prompt_version` 是 D9-4 要求随块落地的两个字段：`blocks_from_vlm()`
    会把它们写进 `Block.meta`，所以即便上层只保留块、丢掉本结果对象，归因信息
    也不会丢。
    """

    text: str = ""
    degraded: str | None = None
    model: str = ""
    prompt_version: str = VLM_PROMPT_VERSION
    cached: bool = False


# --------------------------------------------------------------------------- #
# 渲染：一份 PDF 只 open 一次
# --------------------------------------------------------------------------- #


def _pdfium() -> Any:
    """延迟 import `pypdfium2`。

    为什么延迟：ADR 0008 D6 要求子进程 import 链 lazy（worker 子进程 1.7s 的 import 开销
    就是这么省下来的），而且 `pypdfium2` 目前在 `pyproject.toml` 里**不是直接依赖**
    （它是 `pdfplumber` 的传递依赖，见回报的契约缺口），直接写在模块顶部会让没装它的
    环境连 import 本模块都失败。
    """
    try:
        import pypdfium2
    except ImportError as exc:  # pragma: no cover - 依赖已装，只在裸环境触发
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "视觉通道需要 pypdfium2 包（当前环境未安装）",
            details={"package": "pypdfium2"},
        ) from exc
    return pypdfium2


class PdfPages:
    """一份 PDF 的**唯一**打开点：多次渲染复用同一个 `PdfDocument`。

    为什么必须有这个类：`PdfDocument` 的构造要解析交叉引用表，逐页 `open()` 是最容易
    写出来的 O(n²)。同族的证据在 ADR 0008 D5-1 —— pdfplumber 一份文档只 open 一次时
    S2 是 3.63s，分两次 open 变 11.27s（12.8×）。`render_page()` 内部也共享同一份文档。

    用法二选一：

    ```python
    with PdfPages(pdf_bytes) as pages:      # 或 pages = PdfPages(...); try/finally: pages.close()
        for page_no in range(1, pages.page_count + 1):
            png = pages.render(page_no, dpi=200)
    ```
    """

    __slots__ = ("_digest", "_doc", "_pdfium")

    def __init__(self, pdf_bytes: bytes) -> None:
        pdfium = _pdfium()
        self._pdfium = pdfium
        self._digest = hashlib.sha256(pdf_bytes).digest()
        try:
            self._doc = pdfium.PdfDocument(pdf_bytes)
        except Exception as exc:  # noqa: BLE001 — pdfium 的异常类型随版本变，按类名记即可
            raise AppError(
                ErrorCode.PARSE_FAILED,
                f"PDF 打开失败（{type(exc).__name__}），无法渲染页面",
                details={"type": type(exc).__name__},
            ) from exc

    def __enter__(self) -> PdfPages:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def digest(self) -> bytes:
        """`sha256(pdf_bytes)`：共享缓存槽用它判断"还是不是同一份 PDF"。"""
        return self._digest

    @property
    def page_count(self) -> int:
        return len(self._doc)

    def close(self) -> None:
        """幂等关闭。关闭后任何 `render` 都会抛 `INTERNAL_ERROR`（用错顺序要立刻看得见）。"""
        doc = getattr(self, "_doc", None)
        if doc is not None:
            self._doc = None
            doc.close()

    def _page(self, page_no: int) -> Any:
        doc = getattr(self, "_doc", None)
        if doc is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "PdfPages 已关闭，不能再渲染")
        if not 1 <= page_no <= len(doc):
            raise AppError(
                ErrorCode.PARSE_FAILED,
                f"页码越界：{page_no}（本 PDF 共 {len(doc)} 页）",
                details={"page_no": page_no, "page_count": len(doc)},
            )
        return doc[page_no - 1]

    def page_size_pt(self, page_no: int) -> tuple[float, float]:
        """页面的物理尺寸（pt）。"""
        page = self._page(page_no)
        try:
            width, height = page.get_size()
        finally:
            page.close()
        return float(width), float(height)

    def page_bbox(self, page_no: int) -> tuple[float, float, float, float]:
        """整页 bbox（pt，左上原点）。VLM 只到页级，`blocks_from_vlm` 要的就是这个。"""
        width, height = self.page_size_pt(page_no)
        return (0.0, 0.0, width, height)

    def render(self, page_no: int, *, dpi: int = RENDER_DPI) -> bytes:
        """把第 `page_no` 页（1-based）渲染成 PNG bytes。"""
        page = self._page(page_no)
        try:
            bitmap = page.render(scale=dpi / 72)
            try:
                image = bitmap.to_pil()
            finally:
                bitmap.close()
        finally:
            page.close()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


_shared_lock = threading.Lock()
_shared_pages: PdfPages | None = None


def render_page(pdf_bytes: bytes, page_no: int, *, dpi: int = RENDER_DPI) -> bytes:
    """渲染单页为 PNG，**同一份 PDF 复用同一个 document 对象**。

    共享槽只记住"最近一份 PDF"（按 `sha256(pdf_bytes)` 判同）：单份文档解析进程里够用，
    也把常驻内存钉在一份；换文档时旧的那份立刻 `close()`，不留两份的峰值。
    整份解析请用 `PdfPages`，别在循环里反复传 `pdf_bytes` —— 那样每轮都要重算摘要。

    渲染期间持锁：`PdfDocument` 不是线程安全的，而 200dpi 单页只要 0.02–0.10s
    （ADR 0008 D10-4），串行的代价远小于并发踩崩的代价。
    """
    global _shared_pages
    digest = hashlib.sha256(pdf_bytes).digest()
    with _shared_lock:
        if _shared_pages is not None and _shared_pages.digest == digest:
            return _shared_pages.render(page_no, dpi=dpi)
        if _shared_pages is not None:
            _shared_pages.close()
            _shared_pages = None
        pages = PdfPages(pdf_bytes)
        try:
            png = pages.render(page_no, dpi=dpi)
        except BaseException:
            pages.close()
            raise
        _shared_pages = pages
        return png


def close_renderer() -> None:
    """释放共享的 PDF document（Worker 收尾 / 测试隔离用）。幂等。"""
    global _shared_pages
    with _shared_lock:
        if _shared_pages is not None:
            _shared_pages.close()
            _shared_pages = None


# --------------------------------------------------------------------------- #
# OCR：引擎懒加载 + 进程内复用 + 页级超时降级
# --------------------------------------------------------------------------- #

_engine_lock = threading.Lock()
_engine: Any | None = None
_ocr_executor: ThreadPoolExecutor | None = None


def _get_engine() -> Any:
    """懒加载并全局复用 OCR 引擎（ADR 0008 D10-6）。

    构造实测 0.98–1.05 s（稳态）/ 7.55 s（新 spawn 的子进程，含模型加载），
    每页重建等于每页多付一次首帧 —— 而 D10-8 的 20s 页预算本来就已经很紧。
    """
    global _engine
    with _engine_lock:
        if _engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:  # pragma: no cover - 依赖已装
                raise AppError(
                    ErrorCode.INTERNAL_ERROR,
                    "视觉通道需要 rapidocr-onnxruntime 包（当前环境未安装）",
                    details={"package": "rapidocr-onnxruntime"},
                ) from exc
            _engine = RapidOCR()
        return _engine


def close_engine() -> None:
    """丢弃引擎单例（Worker 收尾 / 测试隔离用）。下一次 `ocr_page` 会重新构造（约 1s）。"""
    global _engine
    with _engine_lock:
        _engine = None


def close_executor() -> None:
    """关掉 OCR 线程池。**只在进程退出/测试隔离时用**：正在跑的那一页会被等完。"""
    global _ocr_executor
    with _engine_lock:
        executor = _ocr_executor
        _ocr_executor = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def _executor() -> ThreadPoolExecutor:
    """单线程池：既用来给单页调用套超时，也顺手保证 OCR 调用串行。"""
    global _ocr_executor
    with _engine_lock:
        if _ocr_executor is None:
            # max_workers=1 是**有意**的：D10-8 实测 2 个 OCR worker 峰值会叠到 0.8–1.1 GB，
            # 8 GB 机器上不建议并发 >1；而且超时的调用还在跑，串行才不会越积越多。
            _ocr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pyknora-ocr")
        return _ocr_executor


def _center_y(box: OcrBox) -> float:
    return (box.bbox[1] + box.bbox[3]) / 2.0


def _box_height(box: OcrBox) -> float:
    return box.bbox[3] - box.bbox[1]


def _quad_to_bbox(quad: Iterable[Sequence[float]]) -> tuple[float, float, float, float]:
    """四点框（可能是倾斜的四边形）→ 轴对齐 bbox。"""
    xs: list[float] = []
    ys: list[float] = []
    for point in quad:
        xs.append(float(point[0]))
        ys.append(float(point[1]))
    return (min(xs), min(ys), max(xs), max(ys))


def _in_reading_order(boxes: list[OcrBox]) -> list[OcrBox]:
    """阅读顺序：先 y 后 x，但**同一视觉行内严格按 x 排**。

    只按 y0 排会把倾斜扫描的同一行拆开（同一行几个框的 y 差几像素就够了），
    所以先按 y 聚类成行、再行内按 x 排。容差 = 行高的 `_LINE_TOLERANCE_RATIO` 倍。
    """
    lines: list[list[OcrBox]] = []
    for box in sorted(boxes, key=lambda item: (_center_y(item), item.bbox[0])):
        if lines:
            line = lines[-1]
            mean_y = sum(_center_y(prev) for prev in line) / len(line)
            tolerance = _LINE_TOLERANCE_RATIO * max(
                _box_height(box), *(_box_height(p) for p in line)
            )
            if abs(_center_y(box) - mean_y) <= tolerance:
                line.append(box)
                continue
        lines.append([box])
    return [box for line in lines for box in sorted(line, key=lambda item: item.bbox[0])]


def _boxes_from_rapidocr(raw: Any) -> list[OcrBox]:
    """把 `RapidOCR(...)` 的输出规整成 `OcrBox` 列表。

    输出形状（实测 1.4.4）：`None`（没检出）或 `[[quad, text, score], ...]`，
    外层被包成 `(结果, 各阶段耗时)` 由 `_run_ocr_call` 拆掉。
    """
    boxes: list[OcrBox] = []
    for item in raw or []:
        if len(item) < 3:
            continue
        text = str(item[1]).strip()
        if not text:
            continue
        boxes.append(OcrBox(text=text, bbox=_quad_to_bbox(item[0]), score=float(item[2])))
    return _in_reading_order(boxes)


def _run_ocr_call(engine: Callable[[bytes], Any], png: bytes) -> list[OcrBox]:
    raw = engine(png)
    if isinstance(raw, tuple) and len(raw) == 2:
        raw = raw[0]  # rapidocr 返回 (结果, 各阶段耗时)
    return _boxes_from_rapidocr(raw)


def ocr_page(
    png: bytes,
    *,
    timeout_s: float = OCR_PAGE_TIMEOUT_S,
    engine: Callable[[bytes], Any] | None = None,
) -> OcrResult:
    """识别一页位图（PNG bytes）。

    超时 → `OcrResult(blocks=[], degraded="ocr_slow")`，**不抛**（D10-8 已决口径）。
    引擎抛异常 → `degraded="ocr_error"`，同样不抛（D9-6：单页失败不整份失败）。

    `engine` 只为测试注入（默认走全局单例）。注入的引擎必须同步返回 ——
    引擎本身是 CPU 密集的 C 扩展，超时靠"把调用丢进线程、主线程等 future"实现。
    """
    call = engine if engine is not None else _get_engine()
    started = time.monotonic()
    future = _executor().submit(_run_ocr_call, call, png)
    try:
        boxes = future.result(timeout=max(float(timeout_s), 0.0))
    except FutureTimeoutError:
        # 关键：**不 cancel**。future 已经在跑，`cancel()` 只会返回 False；线程会自然跑完、
        # 结果丢弃。下一个 ocr_page 排在它后面（max_workers=1），不会叠并发。
        return OcrResult(blocks=[], degraded="ocr_slow", elapsed_s=time.monotonic() - started)
    except Exception:  # noqa: BLE001 — 单页失败不整份失败（D9-6），原因用 degraded 表达
        return OcrResult(blocks=[], degraded="ocr_error", elapsed_s=time.monotonic() - started)
    return OcrResult(blocks=boxes, elapsed_s=time.monotonic() - started)


# --------------------------------------------------------------------------- #
# 触发判定与路由（纯函数，不做 I/O）
# --------------------------------------------------------------------------- #


def looks_like_sentence(text: str, *, min_chars: int = 8) -> bool:
    """文本层是否"成句"（D9-2 信号 S2 的落地判据）。

    S2 要抓的是**劣质文字层**：扫描件自带的乱码、单字、符号碎片。判据就三条，
    宁可宽松（宽松只会把页推给 OCR，那是便宜且确定的那条通道）：

    - 去掉空白后不足 2 个字符 → 碎片，不成句；
    - 去掉空白后够长（≥ `min_chars`）→ 当成有内容；
    - 短但带句读标点 → 仍是句子（"总计：3 项。"这种）。
    """
    compact = "".join(text.split())
    if len(compact) < 2:
        return False
    if len(compact) >= min_chars:
        return True
    return bool(_SENTENCE_PUNCT.search(text))


_SENTENCE_PUNCT = re.compile(r"[。．.!?！？；;，,、:：]")


@dataclass(frozen=True, slots=True)
class PageSignals:
    """一页的判定输入：D9-2 的三信号 + 抽到的块类型。

    字段全部由调用方从"已抽好的内容"里数出来（本模块不碰 PDF），所以 `classify_page`
    是纯函数、可单测。
    """

    #: 文本层字符数（S1/S2 的直接依据）。
    char_count: int
    #: 页面上的图片对象个数。
    image_count: int = 0
    #: 图片覆盖面积占页面面积的比例（S3 的直接依据，0–1）。
    image_area_ratio: float = 0.0
    #: 抽到的表格块个数（表格线框是 VLM 的强信号，D10-2）。
    table_block_count: int = 0
    #: 检出的分栏数（多栏排版是 VLM 的强信号，D10-2）；单栏填 1。
    column_count: int = 1
    #: 文本层是否成句（用 `looks_like_sentence()` 算）。
    sentence_like: bool = True
    #: 页面尺寸（pt）。视觉块的页级 bbox（D9-5）要落在页面里，所以尺寸得从 PDF 那侧
    #: 数过来——本模块不碰 PDF，只能由调用方填。
    page_width: float = 0.0
    page_height: float = 0.0

    def triggered_by(
        self,
        *,
        min_chars: int = VISION_MIN_CHARS,
        image_ratio_threshold: float = VISION_IMAGE_AREA_RATIO,
    ) -> str | None:
        """返回命中的信号名（`"S1"`/`"S2"`/`"S3"`），没命中返回 `None`。

        `None` = 这一页**根本不该进视觉通道**（文字层完好、没有大图）：纯文本页 OCR 是
        白花钱、VLM 还会漏行补字（D10-1）。"要不要进通道"由调用方判，
        `classify_page` 只回答"进去以后走哪条"。
        """
        if self.char_count == 0:
            return "S1"
        if self.char_count < min_chars and (self.image_count > 0 or self.image_area_ratio > 0.0):
            return "S2"
        if self.image_area_ratio >= image_ratio_threshold:
            return "S3"
        return None

    def as_record(
        self,
        *,
        min_chars: int = VISION_MIN_CHARS,
        image_ratio_threshold: float = VISION_IMAGE_AREA_RATIO,
    ) -> dict[str, Any]:
        """D9-2 要求逐页留下的记账字段（可直接进日志/作业 details）。"""
        return {
            "chars": self.char_count,
            "image_count": self.image_count,
            "image_area_ratio": round(self.image_area_ratio, 4),
            "triggered_by": self.triggered_by(
                min_chars=min_chars, image_ratio_threshold=image_ratio_threshold
            ),
        }


def classify_page(
    signals: PageSignals,
    *,
    min_chars: int = VISION_MIN_CHARS,
    image_ratio_threshold: float = VISION_IMAGE_AREA_RATIO,
) -> PageKind:
    """D10-2 的判型：`text_scan` / `structured` / `uncertain`。

    规则按"信号强度"排序，命中即返回（每条都能指回 ADR，偏离的地方都写了理由）：

    1. **有表格块 → `structured`**。表格是 OCR 的灾难场景（D10-1：丢列对齐、合并单元格丢失），
       只有 VLM 能保结构。
    2. **多栏（≥2）→ `structured`**。OCR 按行切会把左右栏串成一行。
    3. **文字层够多、但图占了大半个版面 → `structured`**。这是图文混排页：图里的字会被
       OCR 再读一遍、和文字层重复（D10-2 附加规则 1 的反向场景），VLM 有版面感。
    4. **完全没文本层（S1）→ `text_scan`**。经典扫描页，OCR 的主场。
    5. **文字层稀疏但成句（S2）→ `text_scan`**。先丢劣质文字层、用 OCR 重认
       （D10-2 附加规则 1 明文）。
    6. **有图且文字层稀疏、还不成句 → `uncertain`**。全是碎片，判不出是字还是图。
    7. **纯文本页（无图）→ `text_scan`**。见下方"补充规则"。
    8. **其余（文字层完好 + 只有小图）→ `uncertain`**。D10-2 的兜底：信号打架就交给 VLM。

    ## 补充规则 7（D10-2 没覆盖的一格）

    D10-2 只定义了"进入通道之后"怎么判型，**没定义纯文本页误触发时判成哪一类**。
    这里判 `text_scan`，理由：OCR 是确定性的、能给出 bbox 与置信度、且在长文字页
    不会漏行补字；VLM 恰恰相反（D10-1）。正常情况下 `triggered_by()` 返回 `None`
    的页根本不会走到这里，所以这条只是防误触发。
    """
    has_image = signals.image_count > 0 or signals.image_area_ratio > 0.0
    sparse = signals.char_count < min_chars

    if signals.table_block_count > 0:
        return "structured"
    if signals.column_count >= 2:
        return "structured"
    if has_image and signals.image_area_ratio >= image_ratio_threshold and not sparse:
        return "structured"
    if has_image and signals.char_count == 0:
        return "text_scan"
    if has_image and sparse and signals.sentence_like:
        return "text_scan"
    if has_image and sparse:
        return "uncertain"
    if not has_image:
        return "text_scan"
    return "uncertain"


def channel_for(kind: PageKind) -> Channel:
    """D10-2 的路由表：`text_scan`→`ocr`，`structured`/`uncertain`→`vlm`（兜底）。"""
    return "ocr" if kind == "text_scan" else "vlm"


def escalate_to_vlm(result: OcrResult, *, min_score: float = OCR_MIN_SCORE) -> bool:
    """OCR 已经跑过、该不该升级到 VLM（D10-2 附加规则 2："读不准"才升级）。

    三种"读不准"：一个框都没有、超时/出错（`degraded` 非空）、置信度均值低于阈值。
    """
    if result.degraded is not None or not result.blocks:
        return True
    mean_score = sum(box.score for box in result.blocks) / len(result.blocks)
    return mean_score < min_score


# --------------------------------------------------------------------------- #
# 预算记账
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class DocBudget:
    """整份文档的视觉通道预算（ADR 0008 D9-7 / D10 已决第 4 条）。

    只管两件事：**页数上限**（OCR 20 / VLM 50）与**总时长**（600s）。
    单页超时仍然由 `ocr_page(timeout_s=...)` 自己兜，这里通过 `page_timeout()` 把
    "名义页预算"和"还剩多少总预算"取小后传下去 —— 总预算将尽时页预算自动缩短，
    不会出现"最后几页每页还是硬等满 20s"。

    超预算不抛异常：`claim_*` 返回 `False` 并把原因写进 `reasons`，由调用方决定
    怎么标 `PARSE_INCOMPLETE`（`error_code` 就是那个码）。
    """

    total_s: float = DOC_TOTAL_BUDGET_S
    ocr_max_pages: int = OCR_MAX_PAGES
    vision_max_pages: int = VISION_MAX_PAGES
    clock: Callable[[], float] = time.monotonic
    started_at: float = field(init=False)
    ocr_pages: int = 0
    vision_pages: int = 0
    reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.started_at = self.clock()

    def elapsed(self) -> float:
        return self.clock() - self.started_at

    def remaining(self) -> float:
        return max(0.0, self.total_s - self.elapsed())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def page_timeout(self, nominal: float) -> float:
        """单页该给多少秒：名义页预算与剩余总预算取小；已耗尽返回 0。"""
        return max(0.0, min(float(nominal), self.remaining()))

    def claim_ocr_page(self) -> bool:
        """占一页 OCR 配额。返回 `False` 时原因已记进 `reasons`，调用方别再识别这一页。"""
        if self.expired():
            self.note("doc_budget")
            return False
        if self.ocr_pages >= self.ocr_max_pages:
            self.note("ocr_max_pages")
            return False
        self.ocr_pages += 1
        return True

    def claim_vision_page(self) -> bool:
        """占一页 VLM 配额（语义同 `claim_ocr_page`）。"""
        if self.expired():
            self.note("doc_budget")
            return False
        if self.vision_pages >= self.vision_max_pages:
            self.note("vision_max_pages")
            return False
        self.vision_pages += 1
        return True

    def note(self, reason: str) -> None:
        """记一条降级/超预算原因（去重，顺序即发生顺序）。"""
        if reason not in self.reasons:
            self.reasons.append(reason)

    @property
    def incomplete(self) -> bool:
        """有任一原因 = 这份文档的视觉通道没跑完（应标 `PARSE_INCOMPLETE`，语义是 422）。"""
        return bool(self.reasons)

    @property
    def error_code(self) -> str:
        """超预算时要用的错误码。`PARSE_INCOMPLETE` **已存在**，本节点没有新增错误码。"""
        return ErrorCode.PARSE_INCOMPLETE


# --------------------------------------------------------------------------- #
# VLM：走项目现有 chat_* 配置，httpx，无 key 优雅跳过
# --------------------------------------------------------------------------- #

_VLM_CACHE_MAX = 32
_vlm_cache: OrderedDict[str, VlmResult] = OrderedDict()
_vlm_cache_lock = threading.Lock()


def _cache_key(png: bytes, model: str, prompt_version: str) -> str:
    """D9-4：`sha256(页面位图) + vision_model + prompt_version`。"""
    return "|".join((hashlib.sha256(png).hexdigest(), model, prompt_version))


def _cache_get(key: str) -> VlmResult | None:
    with _vlm_cache_lock:
        hit = _vlm_cache.get(key)
        if hit is not None:
            _vlm_cache.move_to_end(key)
        return hit


def _cache_put(key: str, result: VlmResult) -> None:
    with _vlm_cache_lock:
        _vlm_cache[key] = result
        _vlm_cache.move_to_end(key)
        while len(_vlm_cache) > _VLM_CACHE_MAX:
            _vlm_cache.popitem(last=False)


def reset_vlm_cache() -> None:
    """清空页级缓存（测试隔离 / Worker 收尾用）。

    介质未定（D10 已决第 6 条把它推到实施期），所以这里先是**进程内** LRU ——
    够用来避免同一页在同一次解析里被识别两遍，也让"重跑一次同样的文档"省掉 VLM 调用。
    """
    with _vlm_cache_lock:
        _vlm_cache.clear()


@dataclass(frozen=True, slots=True)
class _VisionEndpoint:
    """视觉通道的端点三元组 + 单页超时（只为把配置解析从 `vlm_page` 里拆出来可测）。"""

    base_url: str
    api_key: str
    model: str
    timeout_s: float


def _vision_endpoint(config: Settings, timeout_s: float | None) -> _VisionEndpoint:
    """解析视觉通道的端点与超时。

    端点优先走 `Settings.resolve_vision()`：`vision_*` 逐项回落 `chat_*` 的口径
    （ADR 0008 D9-3）只该有**一处**实现，别在这里抄第二份。那份配置若还没落地，
    就直接读 `chat_*`（任务书口径：`chat_base_url`/`chat_api_key`/`chat_model`）。

    超时同理：`vision_page_timeout_s`（默认 20s，与 OCR 页预算对齐）优先于
    `chat_timeout_s`（60s）——一页吃满 60s 就把整份文档的预算耗光了。
    """
    resolver = getattr(config, "resolve_vision", None)
    if callable(resolver):
        base_url, api_key, model = resolver()
    else:  # pragma: no cover - 当前 settings 已提供 resolve_vision
        base_url, api_key, model = config.chat_base_url, config.chat_api_key, config.chat_model
    page_timeout = (
        float(timeout_s)
        if timeout_s is not None
        else float(getattr(config, "vision_page_timeout_s", 0.0) or config.chat_timeout_s)
    )
    return _VisionEndpoint(
        base_url=str(base_url).rstrip("/"),
        api_key=str(api_key),
        model=str(model),
        timeout_s=page_timeout,
    )


def _current_settings() -> Settings:
    from pyknora.core.settings import settings  # 延迟 import：只在实际要走 VLM 时才碰配置

    return settings


def _http_timeout(read_s: float) -> httpx.Timeout:
    """建连单独压上限。口径同 `services/rag/answer.py:252-262`（那里是流式，这里是页级）。"""
    read = max(float(read_s), 1.0)
    return httpx.Timeout(read, connect=min(_CONNECT_TIMEOUT_S, read))


def _content_text(data: Any) -> str:
    """从 `chat/completions` 的响应里取文本。`content` 可能是字符串，也可能是分段数组。"""
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        ]
        return "".join(parts).strip()
    return ""


async def vlm_page(
    png: bytes,
    *,
    timeout_s: float | None = None,
    config: Settings | None = None,
) -> VlmResult:
    """把一页位图送多模态模型，取回结构化文本（D10-2 的 `structured`/`uncertain` 通道）。

    降级口径：**任何失败都是返回值，不是异常**（D9-6：单页失败不整份失败）。
    视觉 key 与 chat key 都为空时直接返回 `degraded="no_vlm_config"`，**一个网络请求都不发**——
    "没配 key"是运维状态，不该让整份文档解析失败，也不该发一个空令牌上去换 401
    （那种失败看起来像网络故障，会把排障引向错误方向，`Settings.vision_configured` 同理）。

    请求形状与 `services/rag/answer.py` 保持一致（不引 SDK、不设 temperature：
    少一个网关不支持的参数就少一种 400；可复现性靠 D9-4 的缓存键钉住）。
    """
    cfg = config if config is not None else _current_settings()
    endpoint = _vision_endpoint(cfg, timeout_s)
    if not endpoint.api_key:
        return VlmResult(degraded="no_vlm_config")

    model = endpoint.model
    base_url = endpoint.base_url
    read_timeout = endpoint.timeout_s

    key = _cache_key(png, model, VLM_PROMPT_VERSION)
    cached = _cache_get(key)
    if cached is not None:
        return replace(cached, cached=True)

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VLM_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")
                        },
                    },
                ],
            }
        ],
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {endpoint.api_key}"}
    session_id = str(getattr(cfg, "chat_session_id", "") or "")
    if session_id:
        headers["x-opencode-session"] = session_id

    try:
        async with httpx.AsyncClient(timeout=_http_timeout(read_timeout)) as client:
            response = await client.post(
                f"{base_url}/chat/completions", headers=headers, json=payload
            )
    except httpx.TimeoutException:
        return VlmResult(degraded="vlm_timeout", model=model)
    except httpx.HTTPError:
        return VlmResult(degraded="vlm_unreachable", model=model)

    if response.status_code >= 400:
        return VlmResult(degraded="vlm_http_error", model=model)
    try:
        data = response.json()
    except ValueError:
        return VlmResult(degraded="vlm_bad_response", model=model)

    result = VlmResult(text=_content_text(data), model=model)
    _cache_put(key, result)
    return result


# --------------------------------------------------------------------------- #
# 产物：视觉块（text + locator，char_* 交给上层置 None）
# --------------------------------------------------------------------------- #


def blocks_from_ocr(page_no: int, result: OcrResult, *, dpi: int = RENDER_DPI) -> list[Block]:
    """OCR 结果 → 视觉块。**一个识别框一个块**，bbox 从像素换算成 PDF pt。

    粒度取舍：不在这里合并成"段落块"。合并要版面分析（行间距、跨栏、缩进），而仓库里
    0 份真实扫描件可验；而 bbox 一旦合并就永远拿不回精度。ADR 0008 D10-5 说的
    "该文本块的 bbox"，在本实现里退化成"该识别框的 bbox"——更细不是更差。
    合并成段是上层 splitter 的事。

    `char_start`/`char_end` 由上层置 `None`：**视觉块的 I6 不适用**（D9-5 / D10-5），
    原始字符流不存在，能核对的是 `locator` 而不是偏移。
    """
    scale = 72.0 / float(dpi)
    blocks: list[Block] = []
    for box in result.blocks:
        x0, y0, x1, y1 = box.bbox
        bbox = (
            round(x0 * scale, 2),
            round(y0 * scale, 2),
            round(x1 * scale, 2),
            round(y1 * scale, 2),
        )
        blocks.append(
            Block(
                text=box.text,
                page_no=page_no,
                is_tabular=False,
                # 视觉标记：切分层靠它把这一块的 char_* 置 None（D9-5 / D10-5）。
                is_visual=True,
                locator=RegionLocator(page=page_no, bbox=bbox),
                # D10-5 / D9-4 的记账位：同一页两次 OCR 出不同文本时，靠这个判断是
                # 引擎输出变了还是代码变了；不参与检索。
                meta={"ocr_score": box.score},
            )
        )
    return blocks


def blocks_from_vlm(
    page_no: int,
    result: VlmResult,
    *,
    page_bbox: tuple[float, float, float, float],
) -> list[Block]:
    """VLM 结果 → **页级**视觉块（D9-5：VLM 只到页级，不假装精确到框）。

    `text` 为空也照样返回一个块，这是 D9-6 的"占位块"：这一页必须在产物里有位置，
    否则就是静默丢失（一张整页表格的扫描件会凭空消失）。失败原因由调用方从
    `result.degraded` 记到作业/日志上，同时**也写进块的 `meta`**——`parser.Block`
    新增的 `meta` 槽位就是为 D9-4 开的，模型与提示词版本必须跟块一起走，否则
    "同一页两次解析出不同文字"永远无法归因。
    """
    return [
        Block(
            text=result.text,
            page_no=page_no,
            is_tabular=False,
            # 同 `blocks_from_ocr`：视觉块没有字符流偏移。
            is_visual=True,
            locator=RegionLocator(page=page_no, bbox=page_bbox),
            meta={
                "vision_model": result.model,
                "prompt_version": result.prompt_version,
                "degraded": result.degraded or "",
            },
        )
    ]
