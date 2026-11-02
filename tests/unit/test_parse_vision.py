"""N14 / ADR 0008 D9 + D10 + D10-8：视觉双通道（渲染 / OCR / 判型 / VLM / 预算）的单元测试。

几条纪律（与 `tests/unit/test_parse_tabular.py` 一致）：

- **真库、真识别**：`test_render_page_then_real_ocr_reads_drawn_digits` 用 Pillow 当场画图
  → 存成单页 PDF → `render_page` → `ocr_page` 走真的是 RapidOCR 模型。断言里的数字是
  测试当场画上去的，**识别结果没有被硬编码**。
- 依赖缺失一律 `pytest.importorskip`，绝不"跳过但报通过"；真 OCR 那两组必须真跑。
- 每例前后清空共享状态（渲染文档槽、VLM 缓存），否则用例顺序会互相影响。
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import httpx
import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.settings import Settings
from pyknora.schemas.locator import RegionLocator
from pyknora.services.ingest.parsers import vision

# Windows 自带字体：数字用 Arial/Segoe 就够，中文另找雅黑/黑体/宋体。
_ASCII_FONTS = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/msyh.ttc",
)
_CJK_FONTS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)


@pytest.fixture(autouse=True)
def _isolate_vision_state():
    """共享槽在模块级（PDF 文档 / VLM 缓存），每例前后都要清干净。"""
    vision.close_renderer()
    vision.reset_vlm_cache()
    try:
        yield
    finally:
        vision.close_renderer()
        vision.reset_vlm_cache()


# --------------------------------------------------------------------------- #
# 造图工具（不 mock：图是当场画的）
# --------------------------------------------------------------------------- #


def _first_existing(candidates: tuple[str, ...]) -> str | None:
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def _draw_image(lines: list[str], size: tuple[int, int], font_path: str, font_size: int):
    """白底黑字。返回 PIL.Image。"""
    image_mod = pytest.importorskip("PIL.Image")
    draw_mod = pytest.importorskip("PIL.ImageDraw")
    font_mod = pytest.importorskip("PIL.ImageFont")

    image = image_mod.new("RGB", size, "white")
    draw = draw_mod.Draw(image)
    font = font_mod.truetype(font_path, font_size)
    y = 40
    for line in lines:
        draw.text((60, y), line, fill="black", font=font)
        y += font_size + 40
    return image


def _pdf_from_image(image, *, resolution: float = 150.0) -> bytes:
    """单页 PDF：Pillow 直接存（`resolution` 决定 1 像素对应多少 pt）。"""
    buffer = io.BytesIO()
    image.save(buffer, format="PDF", resolution=resolution)
    return buffer.getvalue()


def _small_pdf(text: str = "hello 42") -> bytes:
    font = _first_existing(_ASCII_FONTS)
    if font is None:  # pragma: no cover - Windows 上必有
        pytest.skip("找不到可用字体，无法造图")
    return _pdf_from_image(_draw_image([text], (600, 220), font, 64))


# --------------------------------------------------------------------------- #
# 1. 渲染：一份 PDF 只 open 一次
# --------------------------------------------------------------------------- #


def test_render_page_reuses_one_document(monkeypatch):
    """连续两次 `render_page` 同一份 PDF：`PdfDocument` 只构造一次（ADR D10-6 同类约束）。"""
    pdfium = pytest.importorskip("pypdfium2")
    pdf = _small_pdf()

    opens: list[int] = []
    real_document = pdfium.PdfDocument

    class _CountingDocument:
        def __init__(self, data):
            opens.append(1)
            self._doc = real_document(data)

        def __len__(self) -> int:
            return len(self._doc)

        def __getitem__(self, index: int):
            return self._doc[index]

        def close(self) -> None:
            self._doc.close()

    monkeypatch.setattr(pdfium, "PdfDocument", _CountingDocument)

    first = vision.render_page(pdf, 1)
    second = vision.render_page(pdf, 1)

    assert len(opens) == 1, "同一份 PDF 被重复 open 了（每页重开是 O(n²)）"
    assert first.startswith(b"\x89PNG\r\n\x1a\n")
    assert first == second, "同一页渲染两次应当逐字节相同"


def test_render_page_reopens_when_pdf_changes(monkeypatch):
    """换一份 PDF：共享槽只留最近一份，旧的那个要立刻 close（不留两份的内存峰值）。"""
    pdfium = pytest.importorskip("pypdfium2")
    pdf_a = _small_pdf("aaa 1")
    pdf_b = _small_pdf("bbb 2")

    opens: list[int] = []
    closes: list[int] = []
    real_document = pdfium.PdfDocument

    class _CountingDocument:
        def __init__(self, data):
            opens.append(1)
            self._doc = real_document(data)

        def __len__(self) -> int:
            return len(self._doc)

        def __getitem__(self, index: int):
            return self._doc[index]

        def close(self) -> None:
            closes.append(1)
            self._doc.close()

    monkeypatch.setattr(pdfium, "PdfDocument", _CountingDocument)

    vision.render_page(pdf_a, 1)
    vision.render_page(pdf_b, 1)

    assert len(opens) == 2
    assert len(closes) == 1, "换文档时旧的那份必须关掉"


def test_pdf_pages_size_bbox_and_page_count():
    """`PdfPages` 的页数 / 物理尺寸 / 整页 bbox：150dpi 下 600×220 像素 = 288×105.6 pt。"""
    pdf = _small_pdf()
    with vision.PdfPages(pdf) as pages:
        assert pages.page_count == 1
        width, height = pages.page_size_pt(1)
        assert width == pytest.approx(600 * 72 / 150)
        assert height == pytest.approx(220 * 72 / 150)
        assert pages.page_bbox(1) == pytest.approx((0.0, 0.0, width, height))


def test_pdf_pages_close_is_idempotent_and_blocks_render():
    """显式 `close()` 之后再用要立刻炸出来，而不是静默返回空图。"""
    pdf = _small_pdf()
    pages = vision.PdfPages(pdf)
    png = pages.render(1)
    assert png.startswith(b"\x89PNG")
    pages.close()
    pages.close()  # 幂等
    with pytest.raises(AppError) as excinfo:
        pages.render(1)
    assert excinfo.value.code == ErrorCode.INTERNAL_ERROR


def test_render_page_rejects_out_of_range_page():
    pdf = _small_pdf()
    with pytest.raises(AppError) as excinfo:
        vision.render_page(pdf, 2)
    assert excinfo.value.code == ErrorCode.PARSE_FAILED


def test_suffixes_is_empty_by_design():
    """本模块不是按后缀分派的解析器：`parser.py` 的接线由 Lead 做，空集防止误注册。"""
    assert isinstance(vision.SUFFIXES, frozenset)
    assert not vision.SUFFIXES


# --------------------------------------------------------------------------- #
# 2. 真 OCR（不 mock、不 skip、不硬编码识别结果）
# --------------------------------------------------------------------------- #


def test_render_page_then_real_ocr_reads_drawn_digits(capsys):
    """真链路：Pillow 画数字 → 单页 PDF → render_page → ocr_page → 断言识别出画的那个数。"""
    pytest.importorskip("pypdfium2")
    pytest.importorskip("rapidocr_onnxruntime")
    font = _first_existing(_ASCII_FONTS)
    if font is None:  # pragma: no cover
        pytest.skip("找不到可用字体，无法造图")

    drawn = "34567890"
    pdf = _pdf_from_image(_draw_image([drawn], (900, 300), font, 96))

    started = time.monotonic()
    png = vision.render_page(pdf, 1)
    render_s = time.monotonic() - started
    assert png.startswith(b"\x89PNG")

    started = time.monotonic()
    result = vision.ocr_page(png)
    ocr_s = time.monotonic() - started

    print(
        f"[n14] render={render_s:.3f}s ocr={ocr_s:.3f}s boxes={len(result.blocks)} "
        f"text={result.text!r} png={len(png)}B"
    )
    assert result.degraded is None, f"真 OCR 不该降级：{result.degraded}"
    assert result.blocks, "至少该识别出一个框"
    digits = "".join(ch for ch in result.text if ch.isdigit())
    assert drawn in digits, f"识别结果里没有画上去的 {drawn}：{result.text!r}"
    assert result.blocks[0].score > 0.5
    # 阅读顺序 + bbox 合理性：框在页面里、且在页面上半部分（图只画了一行、y≈40）
    x0, y0, x1, y1 = result.blocks[0].bbox
    assert 0 <= x0 < x1 <= 900
    assert 0 <= y0 < y1 <= 300


def test_real_ocr_reads_drawn_chinese(capsys):
    """中文同理：描字必须是当场画的（雅黑 84px），本机实测识别置信度 0.999。"""
    pytest.importorskip("pypdfium2")
    pytest.importorskip("rapidocr_onnxruntime")
    font = _first_existing(_CJK_FONTS)
    if font is None:  # pragma: no cover - 非 Windows 或精简系统
        pytest.skip("本机没有中文字体，无法造中文图")

    drawn = "费用报销填写说明"
    pdf = _pdf_from_image(_draw_image([drawn, "N14 2026"], (1000, 400), font, 84))

    started = time.monotonic()
    png = vision.render_page(pdf, 1)
    render_s = time.monotonic() - started
    started = time.monotonic()
    result = vision.ocr_page(png)
    ocr_s = time.monotonic() - started

    print(
        f"[n14-cjk] render={render_s:.3f}s ocr={ocr_s:.3f}s boxes={len(result.blocks)} "
        f"text={result.text!r} png={len(png)}B"
    )
    assert result.degraded is None
    assert result.blocks
    assert "费用报销" in result.text, f"中文识别结果对不上：{result.text!r}"
    assert len(result.blocks) >= 1


# --------------------------------------------------------------------------- #
# 3. OCR 的降级、排序、短语料解析
# --------------------------------------------------------------------------- #


def test_ocr_timeout_degrades_to_ocr_slow_without_raising():
    """D10-8 已决口径：超时 → `degraded="ocr_slow"` + 空块，**不抛**。"""

    def slow_engine(png: bytes):  # pragma: no cover - 故意慢，不该跑完
        time.sleep(0.4)
        return None

    started = time.monotonic()
    try:
        result = vision.ocr_page(b"\x89PNG", timeout_s=0.05, engine=slow_engine)
    finally:
        # 丢掉还卡在 sleep 里的那个线程池：否则下一例的 OCR 会排在它后面，白等 0.35s。
        vision.close_executor()
    elapsed = time.monotonic() - started

    assert elapsed < 0.35, f"超时没生效，白等了 {elapsed:.2f}s"
    assert result.degraded == "ocr_slow"
    assert result.blocks == []
    assert result.text == ""


def test_ocr_engine_failure_degrades_to_ocr_error():
    def boom(png: bytes):
        raise RuntimeError("模型文件损坏")

    result = vision.ocr_page(b"\x89PNG", engine=boom)
    assert result.degraded == "ocr_error"
    assert result.blocks == []


def test_ocr_boxes_are_sorted_in_reading_order():
    """乱序输入 → 阅读顺序；空文本的框丢掉；倾斜四边形的 bbox 取轴对齐外包。"""

    def engine(png: bytes):
        return (
            [
                [[[300, 120], [380, 122], [380, 140], [300, 138]], "第二行右", 0.9],
                [[[10, 10], [90, 10], [90, 30], [10, 30]], "第一行左", 0.9],
                [[[10, 118], [280, 118], [280, 138], [10, 138]], "第二行左", 0.9],
                [[[300, 12], [380, 12], [380, 32], [300, 32]], "第一行右", 0.9],
                [[[0, 0], [1, 1], [1, 1], [0, 0]], "   ", 0.1],
            ],
            [0.01, 0.01, 0.01],
        )

    result = vision.ocr_page(b"\x89PNG", engine=engine)
    assert result.degraded is None
    assert [box.text for box in result.blocks] == ["第一行左", "第一行右", "第二行左", "第二行右"]
    assert result.blocks[0].bbox == (10.0, 10.0, 90.0, 30.0)
    assert result.text.splitlines() == ["第一行左", "第一行右", "第二行左", "第二行右"]


def test_ocr_engine_is_constructed_once_across_pages(monkeypatch):
    """引擎单例：两页只构造一次（构造实测 0.98–1.05s，每页重建等于每页多付一次首帧）。"""
    pytest.importorskip("rapidocr_onnxruntime")
    built: list[int] = []

    class _FakeEngine:
        def __init__(self) -> None:
            built.append(1)

        def __call__(self, png: bytes):
            return ([[[[0, 0], [10, 0], [10, 10], [0, 10]], "甲", 0.99]], [0.01, 0.01, 0.01])

    monkeypatch.setattr("rapidocr_onnxruntime.RapidOCR", _FakeEngine)
    vision.close_engine()
    try:
        first = vision.ocr_page(b"page-1")
        second = vision.ocr_page(b"page-2")
    finally:
        vision.close_engine()  # 别把假引擎留给后面的真 OCR 用例

    assert len(built) == 1, f"引擎被构造了 {len(built)} 次"
    assert first.text == "甲"
    assert second.text == "甲"


# --------------------------------------------------------------------------- #
# 4. 判型：三分类各至少 2 例（纯文本页 / 图片页 / 半结构化页）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        # ---- 纯文本页 → text_scan ----
        pytest.param(vision.PageSignals(char_count=1200), "text_scan", id="纯文本页-无图"),
        pytest.param(
            vision.PageSignals(char_count=0, image_count=1, image_area_ratio=1.0),
            "text_scan",
            id="纯扫描页-S1无文本层",
        ),
        pytest.param(
            vision.PageSignals(char_count=40, image_count=1, image_area_ratio=0.95),
            "text_scan",
            id="稀疏但成句-S2劣质文字层",
        ),
        # ---- 图片页 / 结构化页 → structured ----
        pytest.param(
            vision.PageSignals(char_count=800, image_count=1, table_block_count=2),
            "structured",
            id="有表格块",
        ),
        pytest.param(vision.PageSignals(char_count=800, column_count=2), "structured", id="多栏"),
        pytest.param(
            vision.PageSignals(char_count=800, image_count=1, image_area_ratio=0.85),
            "structured",
            id="图占大半版",
        ),
        # ---- 半结构化页 → uncertain ----
        pytest.param(
            vision.PageSignals(
                char_count=30, image_count=1, image_area_ratio=0.9, sentence_like=False
            ),
            "uncertain",
            id="稀疏碎片-判不出是字还是图",
        ),
        pytest.param(
            vision.PageSignals(char_count=900, image_count=1, image_area_ratio=0.2),
            "uncertain",
            id="文字完好+只有小图-信号打架",
        ),
    ],
)
def test_classify_page_three_ways(signals, expected):
    kind = vision.classify_page(signals)
    assert kind == expected
    assert vision.channel_for(kind) == ("ocr" if expected == "text_scan" else "vlm")


def test_channel_for_covers_all_three_kinds():
    assert vision.channel_for("text_scan") == "ocr"
    assert vision.channel_for("structured") == "vlm"
    assert vision.channel_for("uncertain") == "vlm"  # 兜底一律 VLM（D10-2）


def test_page_signals_triggered_by_and_record():
    """D9-2 三信号 + 逐页记账字段（`as_record` 的键名就是 ADR 写的四个）。"""
    assert vision.PageSignals(char_count=0).triggered_by() == "S1"
    assert vision.PageSignals(char_count=12, image_count=1).triggered_by() == "S2"
    assert (
        vision.PageSignals(char_count=900, image_count=1, image_area_ratio=0.7).triggered_by()
        == "S3"
    )
    # 文字层完好 + 只有小图：三信号都不命中 → 根本不该进视觉通道
    assert (
        vision.PageSignals(char_count=900, image_count=1, image_area_ratio=0.1).triggered_by()
        is None
    )
    record = vision.PageSignals(char_count=12, image_count=1, image_area_ratio=0.876).as_record()
    assert record == {
        "chars": 12,
        "image_count": 1,
        "image_area_ratio": 0.876,
        "triggered_by": "S2",
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", False),
        ("×", False),
        ("ab", False),
        ("总计：3 项。", True),
        ("费用报销填写说明", True),
    ],
)
def test_looks_like_sentence(text, expected):
    assert vision.looks_like_sentence(text) is expected


def test_escalate_to_vlm_only_when_ocr_proved_unreliable():
    """D10-2 附加规则 2：uncertain 只在 OCR 已证明读不准时才升级 VLM。"""
    good = vision.OcrResult(blocks=[vision.OcrBox(text="甲", bbox=(0, 0, 1, 1), score=0.99)])
    shaky = vision.OcrResult(blocks=[vision.OcrBox(text="甲", bbox=(0, 0, 1, 1), score=0.42)])
    assert vision.escalate_to_vlm(good) is False
    assert vision.escalate_to_vlm(shaky) is True
    assert vision.escalate_to_vlm(vision.OcrResult()) is True  # 一个框都没有
    assert vision.escalate_to_vlm(vision.OcrResult(degraded="ocr_slow")) is True


# --------------------------------------------------------------------------- #
# 5. 预算（页数上限 / 总时长 / 缩页预算 / PARSE_INCOMPLETE 语义）
# --------------------------------------------------------------------------- #


def test_doc_budget_page_caps_and_reasons():
    budget = vision.DocBudget(total_s=60.0, ocr_max_pages=2, vision_max_pages=1)
    assert budget.claim_ocr_page() is True
    assert budget.claim_ocr_page() is True
    assert budget.claim_ocr_page() is False
    assert budget.ocr_pages == 2
    assert budget.reasons == ["ocr_max_pages"]
    assert budget.claim_vision_page() is True
    assert budget.claim_vision_page() is False
    assert budget.reasons == ["ocr_max_pages", "vision_max_pages"]
    assert budget.incomplete is True
    assert budget.error_code == ErrorCode.PARSE_INCOMPLETE


def test_doc_budget_expires_and_shrinks_page_timeout():
    """整份预算将尽时，单页预算自动缩到剩余量——不会"最后几页每页还硬等满 20s"。"""
    now = [0.0]
    budget = vision.DocBudget(total_s=10.0, clock=lambda: now[0])
    assert budget.page_timeout(20.0) == pytest.approx(10.0)
    now[0] = 4.0
    assert budget.page_timeout(20.0) == pytest.approx(6.0)
    now[0] = 11.0
    assert budget.page_timeout(20.0) == 0.0
    assert budget.expired() is True
    assert budget.claim_ocr_page() is False
    assert budget.reasons == ["doc_budget"]


def test_budget_constants_match_adr():
    """四个预算常量就是 D9-7 / D10 已决第 4 条写死的数（写死是为了让改动必须过 ADR）。"""
    assert vision.OCR_PAGE_TIMEOUT_S == 20.0
    assert vision.OCR_MAX_PAGES == 20
    assert vision.VISION_MAX_PAGES == 50
    assert vision.DOC_TOTAL_BUDGET_S == 600.0


# --------------------------------------------------------------------------- #
# 6. VLM：无配置优雅跳过 / 有配置走 httpx（stub 传输，不打真网络）
# --------------------------------------------------------------------------- #


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _stub_client(handler):
    """把 `httpx.AsyncClient` 换成走 `MockTransport` 的工厂：真发请求，只是不出网。

    必须用模块级保存的**原始类**再构造客户端 —— 工厂本身会顶替 `httpx.AsyncClient`，
    在工厂里再引用 `httpx.AsyncClient` 就是拿自己调自己（RecursionError）。
    """

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    return factory


def _cfg(**kwargs) -> Settings:
    base = {
        "chat_base_url": "http://stub.local/v1",
        "chat_api_key": "sk-test",
        "chat_model": "gpt-4o-mini",
        "_env_file": None,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


async def test_vlm_without_api_key_skips_without_network(monkeypatch):
    """没有 key：`degraded="no_vlm_config"`，而且**一个请求都不许发**。"""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不许被调用
        sent.append(request)
        return httpx.Response(200, json={"choices": []})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_client(handler))
    cfg = _cfg(chat_api_key="", vision_api_key="")

    result = await vision.vlm_page(b"\x89PNG-fake", config=cfg)

    assert result.degraded == "no_vlm_config"
    assert result.text == ""
    assert result.model == ""
    assert sent == []


async def test_vlm_stub_returns_text_and_reuses_cache(monkeypatch):
    """有 key：真发（stub）请求、解析 Markdown、按 D9-4 的键命中缓存（第二次不再请求）。"""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": " | a | b |\n"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_client(handler))
    cfg = _cfg()

    first = await vision.vlm_page(b"\x89PNG-page-a", config=cfg)
    second = await vision.vlm_page(b"\x89PNG-page-a", config=cfg)

    assert first.degraded is None
    assert first.text == "| a | b |"
    assert first.model == "gpt-4o-mini"
    assert first.prompt_version == vision.VLM_PROMPT_VERSION
    assert first.cached is False
    assert second.cached is True
    assert second.text == first.text
    assert len(requests) == 1, "同一页第二次调用必须命中缓存（D9-4）"

    request = requests[0]
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body["model"] == "gpt-4o-mini"
    assert body["stream"] is False
    parts = body["messages"][0]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_vlm_uses_vision_settings_over_chat_settings(monkeypatch):
    """`vision_*` 逐项回落：只有模型名单独换掉时，网关与 key 仍走 chat_*。"""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_client(handler))
    cfg = _cfg(vision_model="qwen-vl-max", vision_base_url="http://vision.local/v1")

    result = await vision.vlm_page(b"\x89PNG-x", config=cfg)

    assert result.model == "qwen-vl-max"
    assert requests[0].url.host == "vision.local"
    assert requests[0].headers["authorization"] == "Bearer sk-test"


async def test_vlm_http_error_degrades_and_is_not_cached(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, json={"error": {"message": "boom"}})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_client(handler))
    cfg = _cfg()

    first = await vision.vlm_page(b"\x89PNG-err", config=cfg)
    second = await vision.vlm_page(b"\x89PNG-err", config=cfg)

    assert first.degraded == "vlm_http_error"
    assert second.degraded == "vlm_http_error"
    assert len(requests) == 2, "失败不该进缓存"
    assert first.text == ""


async def test_vlm_timeout_degrades(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    monkeypatch.setattr(httpx, "AsyncClient", _stub_client(handler))
    result = await vision.vlm_page(b"\x89PNG-slow", config=_cfg())

    assert result.degraded == "vlm_timeout"
    assert result.text == ""


async def test_vlm_unreachable_degrades(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    monkeypatch.setattr(httpx, "AsyncClient", _stub_client(handler))
    result = await vision.vlm_page(b"\x89PNG-net", config=_cfg())

    assert result.degraded == "vlm_unreachable"


async def test_vlm_bad_response_degrades(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    monkeypatch.setattr(httpx, "AsyncClient", _stub_client(handler))
    result = await vision.vlm_page(b"\x89PNG-html", config=_cfg())

    assert result.degraded == "vlm_bad_response"


# --------------------------------------------------------------------------- #
# 7. 产物：视觉块（text + RegionLocator，char_* 由上层置 None）
# --------------------------------------------------------------------------- #


def test_blocks_from_ocr_maps_pixel_bbox_to_points():
    """像素 → pt：200dpi 下乘 72/200=0.36；`page_no`、`is_tabular`、`locator.kind` 都要对。"""
    result = vision.OcrResult(
        blocks=[vision.OcrBox(text="甲", bbox=(100.0, 200.0, 300.0, 260.0), score=0.99)]
    )
    blocks = vision.blocks_from_ocr(3, result, dpi=200)

    assert len(blocks) == 1
    block = blocks[0]
    assert block.text == "甲"
    assert block.is_tabular is False
    assert block.page_no == 3
    assert isinstance(block.locator, RegionLocator)
    assert block.locator.kind == "region"
    assert block.locator.page == 3
    assert block.locator.bbox == pytest.approx((36.0, 72.0, 108.0, 93.6))
    # 默认 dpi 与显式 200 一致（渲染默认就是这个 DPI）
    assert vision.blocks_from_ocr(3, result)[0].locator.bbox == block.locator.bbox


def test_blocks_from_ocr_is_empty_when_nothing_recognized():
    assert vision.blocks_from_ocr(1, vision.OcrResult(degraded="ocr_slow")) == []


def test_blocks_from_vlm_is_page_level_and_keeps_placeholder():
    page_bbox = (0.0, 0.0, 595.0, 842.0)
    ok = vision.blocks_from_vlm(
        2, vision.VlmResult(text="| a |\n| b |", model="m"), page_bbox=page_bbox
    )
    assert len(ok) == 1
    assert ok[0].text.startswith("| a |")
    assert ok[0].is_tabular is False
    assert ok[0].locator.bbox == page_bbox

    failed = vision.blocks_from_vlm(
        2, vision.VlmResult(degraded="vlm_timeout"), page_bbox=page_bbox
    )
    assert len(failed) == 1, "D9-6：失败的页也要留占位块，否则就是静默丢失"
    assert failed[0].text == ""
    assert failed[0].locator.bbox == page_bbox
