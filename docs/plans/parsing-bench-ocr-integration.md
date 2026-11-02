# 视觉双通道接入实测（N14）

- **采集时间**：2026-11-02 20:19–20:45（Asia/Shanghai）
- **机器**：同 `docs/plans/parsing-bench-ocr.md`（OMEN 15-dc1xxx · i5-9300H · 7.81 GiB · Windows 10 19045）
- **解释器**：`E:\projects\pyknora\.venv\Scripts\python.exe`（Python 3.12）
- **依赖**（均已在 `uv.lock` 里，本轮**未改** `pyproject.toml` / `uv.lock`）：
  `pypdfium2==5.14.0`（**`pdfplumber==0.11.10` 的传递依赖，不是直接依赖**，见 §7 缺口 1）·
  `rapidocr-onnxruntime==1.4.4` · `onnxruntime==1.31.1`（`CPUExecutionProvider`）·
  `Pillow==13.0.0` · `httpx==0.28.1` · `pypdf==6.21.3` · `numpy==2.5.4` · `opencv-python==5.0.0.93`
- **本轮产物**：`src/pyknora/services/ingest/parsers/vision.py`（1014 行）、
  `tests/unit/test_parse_vision.py`（697 行，41 例，真跑 41 passed / 0 skipped）
- **不是设计文档**：口径在 `docs/adr/0008_Document_Parsing_Contract.md` 的 D9 / D10 / D10-8；
  本文只记"这台机器上真跑出来多少、哪些没跑"。

## 0. 结论

| 项 | 实测 | 判定 |
| --- | --- | --- |
| 200dpi 渲染单页 | 0.156–0.172 s（Letter 扫描页）· 0.011–0.047 s（合成小图） | **不是瓶颈**，可视为常数项 |
| `RapidOCR()` 构造 | **0.999 s**（稳态；D10-8 记 0.98–1.05 s） | 必须懒加载 + 进程内复用，逐页重建不可接受 |
| OCR 真扫描页（Letter 1700×2200） | **10.563 s / 7.281 s**（含 1.09 s 一次性的引擎构造） | 与 D10-8 的 median 13.62 s 同量级，20 s 页预算**能覆盖典型页、覆盖不了尾部** |
| 超时降级 | 0.05 s 预算 + 0.4 s 慢引擎 → **0.06 s 返回 `ocr_slow`、`blocks=[]`、不抛** | D10-8 口径成立 |
| 渲染复用 | 两次 `render_page` → `PdfDocument` 只 open **1** 次，字节完全一致 | 成立 |
| 引擎复用 | 两页 OCR → `RapidOCR` 只构造 **1** 次 | 成立 |
| VLM 通道 | 无 `chat_api_key` → `degraded="no_vlm_config"` 且**零请求**；stub 语义 6 例全绿 | 语义成立，**真调用 0 次**（§6） |
| 判型 | 真实扫描件 → `S1` → `text_scan` → OCR；A4 文本页 → `None`（不进通道） | 与 D10-2 一致 |

## 1. 真实扫描件端到端（本轮最有价值的一条）

样本：`C:\Users\Administrator\Desktop\课程\agent教程\周十五\day06_multi_agent\knowledge\public\费用报销填写说明（扫描件）.pdf`
（2 页，Letter `612×792 pt`，文本层 0 字符 —— 就是 `parsing-bench-ocr.md` 里那份主样本，此前只跑过裸引擎）。

链路：`PdfPages(data)` → `render_page(dpi=200)` → `ocr_page()` → `blocks_from_ocr()`。

| 页 | 渲染 | OCR（含引擎构造） | PNG | 框数 | 置信度 ≥0.85 | `degraded` |
| --- | --- | --- | --- | --- | --- | --- |
| p1 | **0.172 s** | **10.563 s**（内部 `elapsed_s=9.469`） | 714 970 B | **24** | **24/24** | `None` |
| p2 | **0.156 s** | **7.281 s**（`elapsed_s=7.281`，引擎已热） | 798 568 B | **20** | **20/20** | `None` |

- p1 的 `10.563 − 9.469 = 1.094 s` 就是引擎构造 —— 首次调用付一次，之后不再付。
- 识别文本（p1 前 160 字）：
  `星桥科技费用报销填写说明 / 文档编号KB-PUB-006扫描页1/2公司内公开 / 一申请前准备 / 确认费用用于公司业务，并能够说明对应的项目、客户或内部事项。`
  —— 标题、编号、"一、"小节名都对。
- 定位落点：p1 首块 `RegionLocator(page=1, bbox=(52.2, 43.11, 283.29, 61.88))`（pt，左上原点），
  末块 `bbox=(455.81, 691.4, 492.47, 702.58)`（页脚区），与版面吻合。
- **同时暴露了 OCR 的结构缺陷**（正是 D10-1 说的那种）：列表序号 `2.` / `3.` / `4.` 被切成独立框，
  和它所引导的那句话不在同一行簇里 —— 于是 `text` 里出现"正文一句 → 下一行的 `3.`"这种错位。
  这不是排序 bug 的偶发，而是**倾斜扫描 + 缩进序号**导致的基线漂移，OCR 拿不到"这一条属于哪一段"。
  这正是把带线框/多栏的页判成 `structured` 交给 VLM 的实证理由。

## 2. 合成图真 OCR（pytest 内，非桩）

测试自己用 Pillow 画图 → 存单页 PDF → 走 `render_page` → `ocr_page`，**不 mock 引擎、不硬编码结果**：

| 用例 | 画的内容 | 字体 | 识别结果 | 框数 | 渲染 | 识别 | PNG |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `test_render_page_then_real_ocr_reads_drawn_digits` | `34567890` | Arial 96px（900×300） | `'34567890'`（另一次实测 `'34 567 890'`，故断言前先剥非数字字符） | 1 | 0.031 s | 3.375 s（含 1.0 s 引擎构造） | 49 965 B |
| `test_real_ocr_reads_drawn_chinese` | `费用报销填写说明` + `N14 2026` | 雅黑 84px（1000×400） | `'费用报销填写说明\nN14 2026'` | 2 | 0.047 s | 1.813 s | 100 633 B |

独立探针同图连跑三次（引擎已构造、图已渲染），看抖动：

| 轮次 | 总耗时 | `elapse`(det/cls/rec) | 文本 | 框 |
| --- | --- | --- | --- | --- |
| 1 | 1.557 s | `[1.468, 0.002, 0.077]` | `'34567890'` | 1 |
| 2 | 0.847 s | `[0.806, 0.002, 0.031]` | `'34567890'` | 1 |
| 3 | 0.942 s | `[0.856, 0.003, 0.071]` | `'34567890'` | 1 |

同图重复的 1.8× 抖动与 `parsing-bench-ocr.md` 记的 3.8× 同源（未定位，见 §8）。耗时几乎全在 `det`，
`cls`/`rec` 都是毫秒级 —— 与 ADR D10-4 一致。PNG 编码 **0.008 s**，`PdfDocument(bytes)` open **0.000 s**。

## 3. 渲染复用（D10-6 的"实例复用"落在渲染侧）

`PdfPages` 持有一个 `PdfDocument`；`render_page()` 用模块级单槽 `_shared_pages` + `_shared_lock`，
按 `sha256(pdf_bytes)` 判同：同一份 PDF 连续渲染复用，换 PDF 则先 `close()` 旧的。

| 断言 | 实测 |
| --- | --- |
| 同 PDF 两次 `render_page` | `PdfDocument` 构造 **1** 次；两张 PNG **逐字节相同** |
| 换成另一份 PDF 再渲染 | 构造 **2** 次、旧 doc `close()` **1** 次 |
| `PdfPages.close()` 幂等 | 连调两次不抛；`close()` 后再 `render()` 抛 `AppError` |
| 1 页 PDF 请求第 2 页 | 抛 `AppError(ErrorCode.PARSE_FAILED)`（页号越界，不静默返回空图） |

## 4. 超时降级（D10-8 已决口径）

注入一个 `time.sleep(0.4)` 的慢引擎 + `timeout_s=0.05`：

- 返回耗时 **0.06 s**，`degraded == "ocr_slow"`，`blocks == []`，**不抛**；
- 实现在单线程 `ThreadPoolExecutor` 上跑 `future.result(timeout=…)`：超时抛 `FutureTimeoutError` 被就地吞成降级。
  **超时后不 `cancel()`** —— 已在跑的任务 `cancel()` 只会返回 `False`，骗自己；
- 该慢线程会占住单线程池，所以用例 `finally` 里调 `close_executor()` 换一个新的（真实接线时 worker 常驻、
  不需要这样收拾；这只是测试卫生）。
- 其他失败口径：引擎抛异常 → `degraded="ocr_error"`；空图 → `blocks=[]`、`degraded=None`（不是错误）。

## 5. VLM 通道

用 `httpx.MockTransport`（真构造 `httpx.AsyncClient`、真序列化请求、只是不出网）覆盖 6 例：

| 场景 | 断言 |
| --- | --- |
| 无 `chat_api_key`（且无 `vision_api_key`） | `degraded="no_vlm_config"`、`blocks=[]`、**`requests == []`（一个字节都不发）** |
| 正常返回 | path `/v1/chat/completions`；`authorization: Bearer sk-test`；`body["messages"][0]["content"][1]["image_url"]["url"]` 以 `data:image/png;base64,` 开头（先文字提示、后图）；`model == chat_model` |
| 再请求同一页 | `cached is True`，且 handler 只被调 **1** 次（缓存键 = `sha256(png) + model + prompt_version`） |
| 500 | `degraded="vlm_http_error"`；**不缓存**（第二次会再发，请求数 2） |
| `ReadTimeout` | `degraded="vlm_timeout"` |
| `ConnectError` | `degraded="vlm_unreachable"` |
| 响应不是 JSON / 没有 `choices` | `degraded="vlm_bad_response"` |
| `vision_model` 单独配置 | host 走 `vision_base_url`，**key 仍回落到 `chat_api_key`**（`Settings.resolve_vision()` 的逐项语义） |

缓存是进程内 `OrderedDict` LRU（32 条）+ 锁，键含 `prompt_version`（`VLM_PROMPT_VERSION="v1"`）——
D9-4 要求的"介质未定、留实施期"我给的是**最容易替换的进程内实现**，没有落盘。

## 6. 判型与路由（真实信号，非构造）

用 pdfplumber 从两份真 PDF 数出 `PageSignals`，再走 `triggered_by()` → `classify_page()` → `channel_for()`：

| 样本 | chars | images | 图面积比 | tables | 成句 | `triggered_by` | 判型 | 通道 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 扫描件 p1 | **0** | 1 | **1.000** | 0 | False | **S1** | `text_scan` | **ocr** |
| 扫描件 p2 | **0** | 1 | **1.000** | 0 | False | **S1** | `text_scan` | **ocr** |
| `基本财务管理制度.pdf` p1 | 1166 | 1 | 0.003 | 0 | True | **None** | `uncertain`† | — |
| `基本财务管理制度.pdf` p2 | 386 | 1 | 0.003 | 0 | True | **None** | `uncertain`† | — |

† 这两行的 `kind` 无意义：**先看 `triggered_by()`**，它返回 `None` 就"不进视觉通道"（文字层直接抽取，
OCR 是白花钱、VLM 还会漏行补字）。`classify_page` 只回答"进去以后走哪条"。这条两段式契约写在
`PageSignals.triggered_by()` 和 `classify_page()` 的 docstring 里，**接线时必须按这个顺序调**。

顺带一个结论：A4 文本页也有 1 个图片对象，但图面积比只有 **0.31%** —— 只看 `image_count` 会误触发，
所以 S3 用的是面积比（`VISION_IMAGE_AREA_RATIO = 0.6`）。D9-2 的三信号在这里都是必要的。

`classify_page` 规则顺序（命中即返回，每条在 docstring 里对回 D10-2）：
表格块>0 → 多栏≥2 → 大图混排 → `chars==0` → 稀疏成句 → 稀疏不成句 → 无图 → 其余兜底。
其中"**无图 → `text_scan`**"是 D10-2 没覆盖的一格（补充规则 7），理由：OCR 确定性、带 bbox、
长文字页不漏行补字，VLM 恰好相反。

## 7. 预算常量

| 常量 | 值 | 来源 |
| --- | --- | --- |
| `OCR_PAGE_TIMEOUT_S` | 20.0 | ADR-0008 **D10-8 已决**：20 s 保留、超时降级 `ocr_slow` |
| `OCR_MAX_PAGES` | 20 | ADR-0008 D9-7 / D10 已决 4 |
| `VISION_MAX_PAGES` | 50 | 同上 |
| `DOC_TOTAL_BUDGET_S` | 600.0 | 同上（整份 600 s，与 90 s 租约解耦） |
| `RENDER_DPI` | 200 | D10-3；DPI 缩放几乎无收益（D10-4） |
| `VISION_MIN_CHARS` | 50 | **未验证值**（D9-2 原文：任何具体阈值都是未验证值） |
| `VISION_IMAGE_AREA_RATIO` | 0.6 | 同上；本机样本只有 1.000 与 0.003 两端，**中间地带没样本** |
| `OCR_MIN_SCORE` | 0.85 | 本机真扫描件 24/24、20/20 框都 ≥0.85，取它做"OCR 读不准"的下限 |

`DocBudget` 把这些收在一起：`claim_ocr_page()` / `claim_vision_page()` 按页数记账、`elapsed`/`remaining`
按 `time.monotonic()` 记账，任一越界即 `incomplete=True`、`error_code = ErrorCode.PARSE_INCOMPLETE`。
**它只返回值、不抛异常**（理由见 §8 缺口 4），由调用方决定是记 job details 还是抛。

## 8. 未验证项（明确没跑）

1. **VLM 真调用 0 次**。本机 `.env` 指向 opencode 网关（`PYKNORA_CHAT_BASE_URL=https://opencode.ai/inference/go`、
   `PYKNORA_CHAT_MODEL=deeseek-v4.1-flash`）——该模型是否吃 `image_url` **未验证**，用真 key 冒烟会有
   401/400 之外的不确定性，且会把扫描件外发。所以只跑了 `MockTransport`。**这是本轮最大的空白。**
2. **真实多页预算耗散没跑**。`DocBudget` 只有单测（20/50/600 的记账与越界），没有一份 50+ 页真文档跑穿。
3. **只有 1 份真实扫描件**（2 页、印刷体、无手写/无倾斜校正需求）。手写、低分辨率、彩色噪声、双栏
   扫描件一页都没跑 —— `uncertain → VLM` 这条升级路径**没有任何真实数据支撑**。
4. **同图重复抖动未定位**（1.8×，D10-8 记 3.8×）：`det` 阶段耗时波动，怀疑 onnxruntime 线程池调度，
   未做 `intra_op_num_threads` 对照实验。
5. **内存峰值未测**。D10-8 的 399–444 MB 是子进程形态的数字；本模块是"同进程调用 + PNG bytes 进内存"
   （Letter 200dpi PNG 约 0.7 MB，`to_pil()` 位图约 22 MB），**没有量过常驻峰值**。
6. **S2 页"先丢劣质文字层再重识别"没落地**（D10-2 附加规则 1）：丢弃文字层要 `parser.py` 协作，
   超出本轮写权限，`classify_page` 只是把这类页判成 `text_scan`，**丢弃动作由接线方做**。
7. **`looks_like_sentence()` 的阈值**（`min_chars=8` + 中英标点）只在本机两份真 PDF 上验证过，
   没有跨语料校准。

## 9. 契约缺口（需要 Lead 决策，我没越界改）

1. **`pypdfium2` 不是直接依赖**：它现在是 `pdfplumber` 的传递依赖（`uv.lock:1636` 是 pdfplumber 的依赖表）。
   `vision.py` 已做函数内延迟 import + `AppError(INTERNAL_ERROR, "视觉通道需要 pypdfium2 包…")` 兜底，
   但**建议把它提升为 `pyproject.toml` 的直接依赖**——否则哪天 `pdfplumber` 换实现，视觉通道就静默缺包。
2. **`RegionLocator.bbox` 的坐标系没有全仓约定**：`schemas/locator.py` 只写"单位随解析器（PDF 为 pt）"，
   没写原点与 y 方向。本轮定为 **pt、原点取页面左上、y 向下**（`px * 72 / dpi`），理由是 pdfplumber 的
   `x0/top/x1/bottom` 就是这个方向。`pdf.py` 若用别家库（PyPDF 的 `mediabox` 是左下原点）就会打架，
   **需要一次确认，最好写进 ADR-0008 D9-5**。
3. **`Block` 没有元数据字段**：D9-4 要求把 `vision_model` / `prompt_version` 写进块元数据，
   但 `parser.py` 的 `Block` 只有 `text/page_no/heading_path/locator/is_tabular`（禁改）。
   现在的落法是：`VlmResult` 自带 `model`/`prompt_version`/`cached`，**信息停在返回值里，进不了块**。
   要落地 D9-4 得给 `Block` 加 `meta: dict | None`（或让 worker 单独记账），**这是硬缺口**。
4. **`PARSE_INCOMPLETE` 存在**（`core/errors.py:26`，映射 422，不在 `_RETRYABLE`）—— 没有缺码问题。
   但本轮**不抛它**：`DocBudget` 只把 `error_code` 交出去。理由：超时/超预算在 D10-8 里是"降级不失败"，
   抛异常会让整单失败；D9-6 的"单页失败 → 占位块 + `PARSE_INCOMPLETE`"应该由 worker 在**汇总层**决定
   （我看不到 `workers/ingest_worker.py`，禁改）。**语义靠 `degraded` 字符串 + `DocBudget.incomplete` 表达。**
5. **`SUFFIXES` 是空集（有意）**：`vision.py` 不是按后缀分派的解析器。接线时**不要**把它塞进
   `parser.py` 的 `_PARSUB_MODULES`，否则任何 PDF 都会被机械分派到这里、绕过正常文字层抽取。
6. **`Settings.vision_*` 是本轮之外的人加的**（`settings.py:76-81`、`resolve_vision()` `:149-159`、
   `vision_configured` `:161-168`）。`vision.py` 直接用它，没有自己读 env —— 若那组配置被回退，
   本模块会退化成"永远 fallback 到 chat_*"，仍然可用但少了独立开关。

## 10. 复现

```powershell
cd E:\projects\pyknora
.venv\Scripts\python.exe -m ruff check src tests scripts
.venv\Scripts\python.exe -m ruff format --check src tests scripts
.venv\Scripts\python.exe -m pytest tests/unit/test_parse_vision.py -q --junitxml=.tmp_n14.xml
# 真 OCR 的两例想看计时，加 -s
.venv\Scripts\python.exe -m pytest tests/unit/test_parse_vision.py -q -s -k real_ocr
```

真扫描件单独跑（不进 `tests/`：样本在仓库外、单页 7–11 s，进 CI 会拖垮门禁。探针脚本是临时的、
未入库，下面只是记下当时怎么跑的）：

```powershell
.venv\Scripts\python.exe $env:TEMP\n14_probe4.py   # 渲染 + OCR + blocks_from_ocr，逐页打印
.venv\Scripts\python.exe $env:TEMP\n14_probe5.py   # pdfplumber 数信号 -> 判型 -> 通道
```

预期：`41 passed`、`0 skipped`；`real_ocr` 两例打印 `[n14]` / `[n14-cjk]` 计时行。
验收产物 `.tmp_n14.xml` 的 `testsuite` 属性为 `tests=41 failures=0 errors=0 skipped=0`。
