# ADR-0008：文档解析契约升级（格式覆盖、定位模型与运行时隔离）

- 日期：2026-11-02
- 状态：**提议**（七条决策经用户逐条确认；七项待定事项已于 2026-11-02 由用户授权代定，见「已决项」；按仓库纪律仍需评审接受，不据此宣称技术口径已定）
- 修订：2026-11-02 第二轮，外部只读评审发现 4 条 P0 / 7 条 P1 / 5 条 P2，已全部修正（见文末「评审记录」）；同日用户授权代定七项待定事项
- 修订：2026-11-02 第三轮，补 **D9 视觉通道（条件触发多模态）**。上一版把「扫描件无 OCR 兜底」写成缺陷却未给任何决策，属对原始需求的漏项（用户原始要求是「遇到表格或者扫描件条件触发用多模态模型」）。用户已确认：触发粒度页级起步（D9-1）、扫描页只到页级引用（D9-5）
- 关联需求与证据：`docs/PRD_PyKnora.md`（知识摄入）；`docs/tdd/06_Knowledge_Ingestion.md`；`docs/plans/全员模块实现路径.md` 契约 C2；AGENTS §4 I1/I3/I6；落地代码 `src/pyknora/services/ingest/parser.py`、`src/pyknora/services/ingest/splitter.py`、`src/pyknora/workers/ingest_worker.py`、`src/pyknora/services/queue/jobs.py`、`scripts/run_worker.py`；评估台账 `docs/Day03/05-失败样本记录.md`（32 条）。

## 背景

当前解析器只覆盖 **5 种格式 / 7 个后缀**（`services/ingest/parser.py:19`：`.pdf .docx .txt .md .markdown .html .htm`），且**同一格式内部丢信息**：

1. `_parse_docx`（`parser.py:108-139`）只遍历 `document.paragraphs`，**从未读取 `document.tables`** → 纯表格的 `.docx` 触发 `"DOCX 未提取到任何段落"`（`parser.py:138`），内容整体丢失。
2. `_parse_pdf`（`parser.py:75-105`）基于 `pypdf`，只得到**纯文本流**，表格与多栏版面被压平成段落，无法回指到单元格。
3. 扫描件与损坏文件同码：`"PDF 未提取到任何文本（可能是扫描件）"`（`parser.py:104`）——无 OCR 兜底，且不可区分。
4. `Block` 的定位信息只有 `page_no` 与 `heading_path`（`parser.py:32-45`），对 `.xlsx`/`.pptx` 这类以「工作表 / 幻灯片」为坐标的格式无表达能力。
5. 分发是 if 链（`parser.py:195-202`），格式扩充后该结构会成为事故源。

同时，运行时侧存在一个既有缺陷：作业租约固定 **90 秒**（`core/settings.py:104`），认领时在 `UPDATE` 里写入 `lease_expires_at`（`services/queue/jobs.py:143`，认领 SQL 整体见 `:138-158`），过期后作业可被**重新认领**（条件见 `jobs.py:150`）；`renew_lease()` 已实现（`jobs.py:198`）但**生产路径从未调用**（唯一调用方是 `tests/integration/test_queue_fencing.py:244-245`）。当前 hashing embedder + 小 PDF 跑得快，90 秒够用；一旦解析变慢（本 ADR 引入 pdfplumber 与大文件格式），超过 90 秒的作业会被第二个 worker 重跑整份解析。`lease_epoch` 参与 `ingest_worker.py:149` 与 `:365-370` 的发布/收尾校验，能挡**双发布**，**挡不住重复消耗**。

## 决策

### D1 验收口径 = 文本 + 结构 + 位置可回指（C 档）

解析成功的判据提高到「文本可读、结构可辨、位置可回指到**页 / 段 / 工作表+单元格 / 幻灯片 / 表格**」。

影响面（**逐处列全，实施时按此清单核对**）：

| 位置 | 改什么 |
| --- | --- |
| `services/ingest/parser.py:32-45` | `Block` 增加 `locator` / `is_tabular`；`ParsedText` 不变 |
| `services/ingest/splitter.py:52-94` | **表格块不能参与 `full_text` 拼接**（见 D3），`page_at`/`heading_at`（`:65-75`）需对表格块让路 |
| `db/repos/reads.py:30-36` | `_CHUNK_COLUMNS`（给 `v_active_chunks`）追加 `locator` |
| `db/repos/reads.py:42-49` | `_CHUNK_COLUMNS_BY_BUILD`（历史 build，带 `c./d./b.` 前缀）同步追加 |
| `models/tables.py:127` 附近 | `ChunkRow` 增加 `locator` 列映射 |
| `services/migration/snapshot.py:170-189` | 导出列清单同步 |
| `schemas/chunk.py:24`、`schemas/document.py:105,134` | 出参增加 `locator`（可选，`None` 表示旧数据） |
| `schemas/document.py:102-103` | **已对前端暴露 `char_start`/`char_end`**，须在契约中写明表格块的取值语义（见 D3） |
| `services/rag/answer.py:196-202` | 引用渲染优先用 `locator`，缺失回退 `page_no`/`heading_path` |
| 视图 | `v_active_chunks` 新列**只能追加到列表末尾**（0013 迁移注释已记此坑），`0012`/`0013` 两处定义都要改，**新增迁移 `0014`** |
| 前端 | 引用展示（`web/features/query/format.ts:138` 已渲染 `locator_ref`，需并列 `locator`） |

A 档（仅文本）与 B 档（文本+结构）被否决，见备选方案。

### D2 位置模型 = 格式无关 locator（discriminated union）

`Block.page_no` / `heading_path` 收敛为 `Block.locator`，用 Pydantic discriminated union（`kind` 字段）而非自由 `dict`：

| kind | 必需字段 | 适用 |
| --- | --- | --- |
| `page` | `page`, `heading`(可选) | PDF / 图片类 |
| `para` | `index`, `heading`(可选) | DOCX/TXT/MD/HTML 段落序号 |
| `sheet` | `sheet`, `cells`(如 `B2:C4`) | XLSX/XLS/ODS/CSV |
| `slide` | `slide`, `shape`(可选) | PPTX/ODP |
| `table` | `index`, `rows`(可选，如 `R1200:R1350`)；**`cells` 仅当来源是电子表格** | 表格块 |
| `region` | `page`, `bbox`(页内坐标) | 视觉块：扫描页整页 / 待补的图片区域（见 D9-5） |

**DOCX 表格不适用 `cells`**：`cells` 是 Excel 的 `A1` 坐标，DOCX 表格没有列标，只能给 `table.index`（第几张表）+ `rows`（行进区间）。D1 对 DOCX 的承诺因此是「可回指到第几张表的第几行」，不是「单元格」。

理由：`locator` 要落进 `chunks` 的 JSONB 并被前端渲染，自由 `dict` 会把类型错误推迟到运行期。`heading_path` 不删除，下沉进 `para`/`page` 的 `heading` 字段，**过渡期双写**（`chunks.heading_path` 列仍在使用）。

#### D2a 与既有 `locator_ref` 的分工（不新增双真相）

`chunks.locator_ref text` **已经存在**（`migrations/0001_init.sql:115`），由 `splitter.py:107` 写入 `f"offsets:{start}-{end_offset}"`，并已被 `0012:50`、`0013:51` 两个视图带出，被 `reads.py:33,46`、`snapshot.py:184`、`tables.py:127`、`schemas/chunk.py:24`、前端 `format.ts:138` 使用。

因此**不取代、只分工**：

| 字段 | 形态 | 语义 | 谁写 |
| --- | --- | --- | --- |
| `locator_ref` | `text` | **切分层**的文本偏移句柄，对外契约不变 | `splitter.py:107` |
| `locator` | `jsonb` | **解析层**的结构化位置（页/段/表/幻灯片/工作表） | `parser.py` 产出，经 splitter 透传 |

`locator_ref` 的 `offsets:` 形式对**表格块不成立**（见 D3），此时改为写入 `locator` 的紧凑序列化（如 `sheet:Sheet1!B2:C4`），并在本文与 `schemas/chunk.py` 注释里写明「`locator_ref` 的格式随块类型不同」。这样既不动既有列与前端渲染，也不产生第二套真相。

### D3 表格块的 I6 受控例外（与 `splitter.py` 的兼容规则）

`splitter.py` 现有机制（`splitter.py:52-63` 拼接 `full_text`、`:86-94` 要求 `content` 是 `full_text` 的原样切片、`:65-75` 用偏移反算 `page_at`/`heading_at`、`:107` 写 `locator_ref`）与「块内偏移 + 注入 `[表头]`」**直接冲突**。规则如下：

1. **表格块不进 `full_text` 拼接**：它是独立单元，不为它计算 `full_text` 偏移；
2. 表格块的 `char_start`/`char_end` **表示块内字符偏移**，`page_at`/`heading_at` 不对表格块求值（返回 `None`，由 `locator` 兜底）；
3. `locator_ref` 对表格块写 `locator` 的紧凑串（`sheet:...` / `table:...`），不再写 `offsets:`；
4. 切分：`len(text) <= chunk_size` 整块原子；超预算按行切分，**显式注入 `[表头] …` 前缀**，并在 `locator.rows` 记录行区间；
5. 前端看到 `is_tabular=true` 时，`char_start`/`char_end` **只能用于块内高亮，不能用于跳转原文**——此语义必须写进 `schemas/document.py:102-103` 的注释与前端提示。

对 **AGENTS §4 I6** 的适用范围因此作如下限定（**需用户在 `AGENTS.md` 中确认改写**）：

> I6 适用于纯文本流格式的连续块。表格类块不保证 `char_*` 可回拼原文，其可核对性由 `locator`（sheet/cells/rows）+ 显式 `[表头]` 标记共同保证。

理由：I6 的初衷是「用户能核对」。对表格而言 `Sheet1!B2:C4` 比字符偏移更强；若强制字符偏移，则无法表达单元格边界。

**D9 补充**：视觉块（`locator.kind="region"`）的 `char_start`/`char_end` 为 `null`，**I6 对它完全不适用**——连「块内偏移」都不成立，可核对性仅由 `region.page` + `bbox` 提供（见 D9-5）。

### D4 格式清单（三批）

| 批 | 格式 | 引擎 |
| --- | --- | --- |
| P1 | `.xlsx` | `openpyxl`（`read_only=True, data_only=True`） |
| P1 | `.xls` | `xlrd` 2.x |
| P1 | `.csv` | stdlib `csv`（编码嗅探 utf-8 → gb18030 → latin-1；分隔符嗅探） |
| P1 | `.pptx` | `python-pptx` |
| P1 | `.odt/.ods/.odp` | 自写 `zipfile` + `ElementTree` |
| P2 | `.eml` | stdlib `email` |
| P2 | `.rtf` | `striprtf`（**仅 B 档**：无结构定位） |
| P3 | `.doc/.ppt` | **默认拒收**（422 提示另存为）+ `PYKNORA_LIBREOFFICE_ENABLED` 可选开关（转换后仅 B 档/页级） |

**不做**：`epub`、压缩包（zip/rar/7z）、`json`、`yaml`、`xml`。

规模：**格式 5 → 14 种，后缀 7 → 16 个**（P1+P2 共 9 个新后缀，P3 另 2 个仅在开关开启时接受）。

工作量与验收（P1 自写项必须给出验收，否则不算可交付）：

- `.odt/.ods/.odp`：解析 `content.xml`，按 `text:p` / `table:table-row` 取块；`style` 只取标题层级，不还原格式；验收 = 一份三页 ODT + 一份含 3 表 ODS 能出正确块数与 `sheet`/`rows`，且 `number-rows-repeated` 上限生效；
- `.xls`：只取单元格值与工作表名，不读公式；验收 = 一份 `.xls` 与同名 `.xlsx` 内容一致；
- `.csv`：验收 = GBK/UTF-8 各一份、含引号内换行的样本不串行。

事实依据：`.xls` 有可靠的纯 Python 解析器（`xlrd` 2.x，**仅支持 `.xls`**）；`.doc`/`.ppt` 在本仓库已核范围内**没有**纯 Python 解析器（正文在 OLE2 私有流里，此断言未穷举 PyPI，属未验证项），只能经 LibreOffice 转换。启用 LibreOffice 等于**改变部署形态**（镜像 +400~700MB、headless 启动 1–3s、并发 profile 锁冲突、Windows/Linux 两套路径，三个数字均未核验），故默认关闭。

### D5 引擎选型：PDF 换 `pdfplumber`，排除 `PyMuPDF`

- **`pdfplumber`（MIT）** 作为 PDF 主解析器：可取得字符级 bbox 与线框（`lines`/`rects`），是达成 D1「表格可回指」的**当前可行路径**（不是唯一路径——`docling`/`unstructured` 也能做，但属 B4 已否决的「统一引擎」方案；许可证与体积未核）。代价：比 `pypdf` 慢（倍数待实测），新增 `pdfplumber` + `pdfminer.six`。
- **`pypdf` 降级**为「快速读页数 + 加密检测」。
- **`PyMuPDF` 排除**：AGPL-3.0，闭源分发与商业使用需购买授权。

`pdfplumber` 的性能必须**先测量再采用**：用 3 份真实 PDF（1 页 / 50 页 / 含表格）实测；若 50 页 × 1MB 超过 30s，则退回双后端（按页数分流），但必须写死「同一文件只走一条路径」，否则同一文件两次解析结果可能不同。

**ADR-0004 的额外要求**：0004 规定「抽取器须经 golden set 选择」。3 份 PDF 只够测耗时，**不够选型**；实现阶段须补一份 ≥10 文档的 golden set（含表格、多栏、扫描件）作为选型依据，否则 D5 不满足 0004 的前置条件。

### D6 运行时隔离：结构预检 + 常驻单进程池 + 硬超时 + 心跳

分两道闸，**第一道无论如何都做**：

**第一道（纯逻辑，必做）**

- ZIP 类格式（docx/xlsx/pptx/odt/ods/odp）：解压前累加 `ZipFile.infolist()` 的 `file_size`，超过 **200 MiB** 直接 `PARSE_FAILED`（`_read_bounded` 的 50 MiB 只卡上传体积，管不到解压后）；
- ODF `number-rows-repeated` / `number-columns-repeated` 单值上限 **10 000**（否则一行声明重复一百万次）；
- 单文档上限：**5 000 行 / 2 000 页 / 2 000 张幻灯片**。

**第二道（进程隔离）**

- `ProcessPoolExecutor(max_workers=1)` **常驻**单进程池（Windows 为 spawn，仅首次付启动代价）；父→子传 `bytes`；
- 子进程 import 链必须 lazy，禁止把 `sentence_transformers` 拖进解析进程；
- **parse 硬超时 60s**（必须 < 租约 90s），超时即**杀进程 + 重建池**，作业标 `PARSE_TIMEOUT` 且**不可重试**（理由与「截断 PDF 不应占满 3 次重试」同源，见 `workers/ingest_worker.py:61-63`）；
- **心跳 30s**：解析期间由父协程调 `renew_lease()`。

**心跳的前置条件（评审 P0-2，必须与心跳同批实施）**

`scripts/run_worker.py:68-77` 的 `_tick()` 用**一次** `session_scope()` 包住「认领 + 解析 + 发布」，`workers/ingest_worker.py:321-331` 同理。`jobs.py:138-158` 的认领是 `UPDATE`，**解析全程持有该作业行锁**。此时另开一个会话调 `renew_lease`（`jobs.py:198-215`，同样 `UPDATE` 同一行）会**阻塞到主事务提交**，心跳永远不会真正发出。

因此 D6 附带三条前置改动，缺一条心跳都不成立：

1. **认领单独提交**：`claim()` 完成后立即 `commit`，解析在事务外进行（发布阶段再开新事务）；
2. **心跳用独立连接**：不复用解析所在的 session；
3. **`scripts/run_worker.py` 与 `ingest_worker.py:321-331` 必须一起改**（上一版本 ADR 漏列这两处影响面）。

### D7 错误码：新增 `PARSE_TIMEOUT`

`core/errors.py` 现有 **17 个** `ErrorCode`（`:13-32`），**没有** `PARSE_TIMEOUT`。实施需同步：

- `core/errors.py:13-32` 新增 `PARSE_TIMEOUT = "PARSE_TIMEOUT"`；
- `_HTTP_STATUS`（`:35`）与 `_RETRYABLE`（`:55`）两张表登记（建议 422 / 不可重试）；
- `workers/ingest_worker.py:55-66` 的 `_NO_RETRY` 集合加入该码。

上一版本把该码当既有码使用，属事实错误。

### D8 评测基准处理（用户已定）

旧基准**废弃但不删除**：`eval/results/*-20261030/` 保留，在每个 `report.md` 顶部与 `eval/README.md` 标注「解析器 v1 基线，2026-11-XX 起不可与后续版本直接比较」。解析器升级完成后以**扩容后的语料**重跑一期，定新基准。

保留而非删除的理由：将来可能出现「新基准低于旧基准」，必须能查证是解析变差还是语料变难。

### D9 视觉通道（条件触发多模态）

背景缺口：本文第一版把「扫描件与损坏文件同码、无 OCR 兜底」（背景第 3 条）记为缺陷，却**没有任何决策消解它**。用户的原始需求是「遇到表格或者扫描件条件触发用多模态模型」，本版补齐。

#### D9-1 触发粒度 = 页级（块级排第二步，文件级否决）

| 粒度 | 做法 | 结论 |
| --- | --- | --- |
| **页级** | 逐页判定，命中即**整页转图**送视觉通道 | **采纳**。判据单一、可解释、成本可预估（页数 × 命中率）。代价：一页内「上半正文 + 下半扫描表」时整页重抽，可能与原始文字层不一致 |
| 块级 | 先抽文本块，用 bbox 覆盖算出未被文本覆盖的图片区域，仅该区域送视觉 | 排第二步。收益是省成本、避免重复抽取正文；代价是 bbox 覆盖与页边距处理，复杂度显著上升 |
| 文件级 | 整份文件「文本量 / 页数」低于阈值才启用 | **否决**：混合文档（前文后图）会被判「有文本」→ 后半扫描页**静默丢失**，比不做更危险 |

#### D9-2 判据 = 三信号 + 全部记账（不拍字符数阈值）

任一命中即该页走视觉通道（**取保守方向：宁可误触发**，因为漏检是静默丢失，误触发只多花一次调用）：

| 信号 | 判据 | 目标 |
| --- | --- | --- |
| S1 | 该页 `chars == 0` | 纯扫描页 |
| S2 | `chars < vision_min_chars` **且**该页有图片对象 | 带一层劣质 OCR 文字层的扫描页（只按「有没有文字」判会漏判） |
| S3 | 图片面积 / 页面积 ≥ `vision_image_area_ratio` | 整页翻拍 / 贴图 |

阈值全部进配置，默认取保守值；**每页记录 `{chars, image_count, image_area_ratio, triggered_by}`** 进 block 元数据与解析日志，使阈值可以事后用真实文档调参。理由：当前仓库只有 2 个 PDF、0 个扫描件，**任何具体阈值都是未验证值**，写成硬编码等于把猜测固化。

#### D9-3 通道 = 复用 OpenAI 兼容视觉端点（零新增依赖）

`core/settings.py:58-61` 已有 `chat_base_url` / `chat_api_key` / `chat_model`（默认 `gpt-4o-mini`，本身支持图片输入）/ `chat_timeout_s`。视觉通道复用同一协议：

- 新增配置组 `vision_enabled` / `vision_base_url` / `vision_api_key` / `vision_model` / `vision_timeout_s` / `vision_max_pages`，**默认继承 `chat_*`**，需要独立模型时再填；
- 请求 `POST {base_url}/chat/completions`，图片以 `data:image/png;base64,...` 传入；
- 复用 `services/rag/answer.py` 的 httpx 客户端风格，**不引 SDK**；
- 视觉调用在**父协程**（async httpx），**不进进程池**；进程池只负责本地字节解析（D6）；
- **本地 OCR 不作默认**：RapidOCR / PaddleOCR 会带入 onnx / torch 级依赖，与「主镜像最小化」冲突，留作后续可选后端。

#### D9-4 不可复现 → 用缓存钉住

VLM 同一页两次可能输出不同文字，会污染 `content_sha1_8` 并使 D8 的评测基线不可比。做法：

- 缓存键 = `sha256(页面位图) + vision_model + prompt_version`；命中直接复用，**不进网络**；
- `vision_model` 与 `prompt_version` 写入 block 元数据 → 任何一条引用都能回答「这段字由哪个模型、哪版提示词识别」。

#### D9-5 定位 = `region`（页级），`char_*` 留空，I6 完全不适用

- `locator.kind = "region"`，带 `page` + 整页 `bbox`；
- `char_start` / `char_end` = `null`——两列本就允许 null（`migrations/0001_init.sql:112-113`），**无需为此新增迁移**；
- 引用渲染为「第 N 页（扫描页，机器识别）」，与文本页的「第 3 页第 2 段」并列，不假装精确；
- **否决**「让 VLM 返回字符级 bbox 再反推偏移」：准确率不可靠，且会把 I6 变成一堆例外。

用户已确认接受该粗定位（2026-11-02）。

#### D9-6 失败降级 = 部分成功（不整份失败）

- 单页识别失败 / 超时 → 保留**占位块**（`text=""` + `reason`），整份作业判 `PARSE_INCOMPLETE`；
- `PARSE_INCOMPLETE` **已存在**（`core/errors.py:26`，映射 422 见 `:44`），无需新增错误码；
- 端点不可达 → 所有视觉页同上，**不整份失败**；
- `vision_max_pages`（默认 50）超出的页直接标 incomplete，**不静默跳过**。

#### D9-7 超时预算与 D6 合流

- 整份预算仍是 D6 的 60s；每页子预算 = `min(vision_timeout_s, 剩余预算)`，剩余 < 5s 停止后续页并标 incomplete；
- 视觉块的 `content` 由父协程回填后参与后续 splitter 流程，**不改变 `ParsedText` 的唯一出口**；
- 最坏情况由 `vision_max_pages` 与 60s 共同兜住。

#### D9 影响面（逐处列全）

| 位置 | 改什么 |
| --- | --- |
| `services/ingest/parser.py` | 页级三信号判定；`region` locator；视觉块 `char_*` 置空 |
| `core/settings.py:56-82` 附近 | 新增 `vision_*` 配置组 |
| `workers/ingest_worker.py:168-190` | 父协程调视觉通道 + 回填 + 部分成功分支（`PARSE_INCOMPLETE`） |
| `services/ingest/splitter.py` | 视觉块与表格块同样**不进 `full_text` 拼接**（D3 规则复用） |
| `schemas/chunk.py`、`db/repos/reads.py:30-36,42-49` | locator 出参包含 `region` 分支 |
| 前端 `web/features/query/format.ts:138` | `region` 分支显示「第 N 页（机器识别）」 |

## 备选方案

- **A 档（仅文本）**：改动最小，但等于承认「PDF 表格永远读不出来」，与升级目标直接冲突。否决。
- **B 档（文本 + 结构，无位置）**：能提升可读性，但无法回答「这句话出自哪个单元格」，引用回指退化为「出自这份文档」。否决。
- **保留 `pypdf`（PDF 降为页级）**：与 D1 的 C 档承诺矛盾——PDF 是最主要的文档类型，其表格拿不到定位等于 C 档对主力格式失效。否决，但记录该损失：**若评审不接受 pdfplumber 的性能代价，D1 需对 PDF 降级为 B 档**。
- **`PyMuPDF`**：技术与性能最优，但 AGPL-3.0。否决。
- **`camelot` / `tabula`**：`camelot` 依赖 Ghostscript（外部二进制），`tabula` 需 JVM。否决（两条理由均基于公开认知，未逐版本核验）。
- **统一引擎（`docling` / `unstructured` / `MarkItDown`）**：能一次覆盖多格式，但引入深度学习依赖（torch 级）、解析结果不可控、且与「多租户谓词必须在 SQL 里」的 I1/I2 口径无关却会拖慢镜像。否决。
- **双轨开关 `PYKNORA_PARSER=v1|v2`**：能保住旧行为，但两套解析逻辑长期并存是标准技术债配方。否决。
- **独立解析服务（常驻进程 + 队列）**：隔离更强，但等于新增一个部署单元，且与 AGENTS §2「队列 = PG 表 + SKIP LOCKED，不用 Celery/Redis」的口径冲突。否决。
- **强制严格向后兼容（快照锁）**：能保住既有评测基线，但用户已明确选择废弃旧基准（见 D8）。否决，且旧结果保留归档。
- **本地 OCR（Tesseract / RapidOCR）作为默认**：确定性优于 VLM、不出网，但只能给文字、**拿不到表格结构**，且引入 onnx / torch 级依赖。否决为默认，保留为可选后端（见 D9-3）。
- **文件级触发（整份文件无文本才启用）**：混合文档后半段静默丢失，见 D9-1。否决。
- **让 VLM 返回字符级 bbox 反推偏移**：准确率不可靠，且把 I6 变成一堆例外。否决（见 D9-5）。

## 后果

**正面**

- PDF 表格、DOCX 表格、XLSX/PPTX/CSV 内容首次可被检索与引用；
- 引用可回指到具体页/行/工作表单元格/幻灯片，引用可核对性从「文档级」升到「块级」；
- 结构预检把「压缩炸弹 / ODF 重复属性」这类攻击面挡在解析之前，与 `_read_bounded` 形成两层；
- 心跳修复让长解析作业不再被重复认领，**该缺陷独立于本 ADR 也应修**，且必须与「认领单独提交」同批做；
- 解析在子进程内崩溃不再拖垮 worker。
- 扫描件与图片表格首次可被检索：命中三信号的页走视觉通道，引用标注「第 N 页（机器识别）」；识别失败降级为 `PARSE_INCOMPLETE` 的部分成功，而不是整份失败。

**负面（必须承认）**

1. **`eval` 基线作废**（D8）：`eval/results/baseline-20261030` / `candidate-20261030` / `compare-20261030.md` 全部不可与新版直接比较，需重建。旧目录保留并标 `DEPRECATED`。
2. **跨域改动**：`services/rag/answer.py:196-202` 属成员3 目录（AGENTS §5），`db/repos/reads.py` 属成员2，`chunks` 表变更触及 **I1/I3** 红线，需相应成员复核。
3. **迁移 `0014`** 需为存量 `chunks` 回填 `locator`：存量数据无原始定位信息，只能回填 `kind="para"`（或按 `source_format` 推断 `page`）——**回填值不是真实定位，必须标注**。
4. **`splitter.py` 必须改**：表格块要跳出 `full_text` 拼接，这会改变既有切片路径的行为，而现有 `tests/integration/` 里对 `char_start`/`char_end` 的断言**不会因此变红**（它们断言的是「能拼回原文」，不是「切片结果与上次一致」）——存在**静默回归**风险，需补一条「表格块不参与拼接」的显式用例。
5. **解析耗时上升**：pdfplumber 慢于 pypdf，进程池与 60s 超时会提高单文档失败率（大文档变 `PARSE_TIMEOUT`）。
6. **依赖增加 6 个**（`pdfplumber`、`pdfminer.six`、`openpyxl`、`xlrd`、`python-pptx`、`striprtf`），需过 `--stage security`（pip-audit）。
7. **`lxml` 口径**：`python-pptx` 需要 `lxml`；`uv.lock` 显示本仓**直接声明** `lxml` 6.1.3（`uv.lock:1530-1533`），并非经 `python-docx` 传递带入。**AGENTS §2「明确不用 lxml」需改写**为「禁止直接 `import lxml` 做 HTML/XML 解析；OOXML 库（python-docx / python-pptx）的依赖除外」（见「已决项」）。
8. **视觉通道输出不可复现**（D9-4）：同一页两次调用可能给出不同文字。缓存键只对「同一输入」有效；位图渲染参数（DPI）一旦变化，旧缓存即失效，需重跑。
9. **视觉通道成本随页数线性增长**（D9-3）：一份 50 页扫描件 = 50 次模型调用，且与 D6 的 60s 预算竞争。`vision_max_pages` 与页级子预算只是兜底，**不降低总成本**。
10. **扫描页引用粒度退化为页级**（D9-5）：用户点引用只能落到页，仍需人工查找具体位置；这与文本页的「第 3 页第 2 段」不是同一体验，前端必须显式标注「机器识别」，不能让用户误以为是精确引用。

## 验证条件

**单元（进 pre-commit 门禁，不需数据库）**

- 每格式 × 3 状态：正常 / 空 / 畸形 → **14 种格式约 42 条用例**（P3 的 `.doc`/`.ppt` 用「拒收」断言替代解析断言），落 `tests/unit/test_parser_*.py`；
- 编码专项：GBK CSV、gb18030 TXT；
- 表格专项：合并单元格、空表头、超预算长表（验证分档切分 + `[表头]` 注入 + `locator.rows`）、**表格块不进 `full_text`**；
- 结构预检：ZIP 解压总量超限、ODF `repeated` 超限、行/页上限；
- locator 契约：discriminated union 非法 `kind` 必须抛错；`table.cells` 用于 DOCX 时必须被拒；
- 视觉通道判据：三信号各自命中 / 不命中的构造样本；`vision_enabled=false` 时**不得发起任何网络调用**；缓存键命中时**不进网络**；视觉块 `char_*` 必须为 `null`。

**集成（需真 PG）**

- 端到端：上传 → parse → split → publish → query，断言返回的 citation 能还原到 `locator`；
- 迁移 `0014` 幂等：重复执行 `scripts/migrate.py` 退出 0；
- 超时与心跳：构造必然超时的样本，断言作业状态为不可重试失败；断言解析期间 `lease_expires_at` 被推进（**该项以「认领单独提交」为前提，未改前必然失败**）；
- 视觉通道（用 stub 端点，不连真实模型）：断言「单页失败 → 整份 `PARSE_INCOMPLETE` 且保留占位块」、断言 `region` locator 能还原页码、断言未命中页不产生视觉块。

**命令**

```bash
uv run ruff check src tests scripts
uv run ruff format --check
uv run pytest -q tests/unit
uv run pytest -q                       # 需真 PG（5433 / 5434）
uv run python scripts/ci.py --stage security
```

**基准重建（D8）**

```bash
uv run python -m eval.build_eval_set     # 语料扩容后再执行
uv run python -m eval.run_eval --config eval/baseline_config.yaml
```

新语料须覆盖：多页含表格 PDF、XLSX、PPTX、CSV、含合并单元格的表、GBK 编码 TXT。**2026-11-02 未执行任何一项验证**——本 ADR 只是决策记录。

## 替代关系

- 与 **ADR-0004（DocReader 服务复用与浏览器双适配）** 互补不冲突：0004 约束的是「是否复用外部 DocReader 容器 / 浏览器抓取」，其「全量重写 parser 成本高」是**一行备选否决**而非禁令；本 ADR 约束的是「本地 ingest 解析的契约与格式覆盖」。**但 0004 的「抽取器须经 golden set 选择」对本 ADR 有约束力**，见 D5 末段。
- 与 ADR-0006（RBAC）与 0007（CJK 全文检索）无交集、无冲突。
- 本 ADR **修订 AGENTS §4 I6 的适用范围**（限定为纯文本流格式）与 §2 的 `lxml` 口径，两者均需用户确认后由用户改写 `AGENTS.md`（AGENTS §9：本文件修改需用户确认）。
- 无被替代的既有 ADR。

## 已决项（2026-11-02，用户 m04714「你定吧，我定不了」明确授权由作者代定）

七条原待确认项，现均已拍板。每条都写明**否决另一条路会失去什么**，便于日后复审时判断这个决定是否仍然成立。

| # | 决定 | 理由 | 另一条路的代价 |
| --- | --- | --- | --- |
| 1 | `AGENTS.md` §4 I6 限定为「纯文本流格式」 | 表格的可核对性由 `locator`（sheet/cells/rows）提供，比字符偏移更强；不限定则 C 档的表格承诺无法落地 | 保留 I6 原样 → 表格块必须保持「`content` 是原文子串」，无法注入 `[表头]`，跨页长表被切成无表头的碎片 |
| 2 | `AGENTS.md` §2 改为「禁止直接 `import lxml` 做 HTML/XML 解析；OOXML 库依赖除外」 | 事实如此：`uv.lock:1530-1533` 显示 `lxml` 是本仓**直接声明**的依赖，原口径已名不副实 | 不改 → 文档与事实长期矛盾，后续读者会误以为 `python-pptx` 违反红线 |
| 3 | 接受「`pdfplumber` 实测不达标则 PDF 降 B 档」的预设降级 | 性能是未知量，ADR 不该为一个未测的数字绑死架构；把降级路径写进 ADR，实测后照做，不必再走一轮决策 | 不预设 → 实测超标时要么硬上 pdfplumber（大文件频繁超时），要么临时拍板改契约 |
| 4 | 迁移 `0014` 的存量 `locator` 回填 `{"kind":"para","index":null,"inferred":true}` | 存量数据没有原始定位，回填任何具体值都是编造；标 `inferred` 让下游能区分「真定位」与「猜的」 | 不回填 → 旧切片全部 `locator=NULL`，前端需长期维护两条渲染分支 |
| 5 | 接受 6 个新增依赖 | P1 的 9 个新后缀没有一个能用标准库完成；`pdfminer.six` 的体积是 `pdfplumber` 的必然代价 | 不接 → P1 只剩 csv/txt 系，等于否定整个升级目标 |
| 6 | `PARSE_TIMEOUT` = 422 / 不可重试 | 超时是**确定性失败**，重试只会再超时一次并占满 3 次重试（与 `ingest_worker.py:61-63` 记的截断 PDF 事故同源） | 可重试 → 大文档把队列吞吐拖垮，且失败时间放大 3 倍 |
| 7 | 接受「认领单独提交」，改 `run_worker.py` 与 `ingest_worker.py` 的事务边界 | 不改则心跳发不出去（认领 `UPDATE` 全程持行锁），90 秒租约的重复认领缺陷永远修不掉 | 不改 → D6 只剩结构预检与超时，长解析作业被第二个 worker 重跑整份解析 |

第 1、2 条已同步写入 `AGENTS.md`，并在 `docs/IMPLEMENTATION_STATUS.md` 记录改动理由（AGENTS §9 要求）。若日后不同意第 1、2 条，回退 `AGENTS.md` 对应两行即可；此时本 ADR 的 D3 需随之改回「严格 I6」，且表格块的 `[表头]` 注入方案不成立。

### D9 已决（2026-11-02 第三轮，用户逐条确认）

| # | 决定 | 用户原话 | 另一条路的代价 |
| --- | --- | --- | --- |
| 8 | 触发粒度 = **页级起步**，块级排第二步，文件级否决（D9-1） | m04775「按照你的建议」 | 直接做块级 → 复杂度前置，链路未通就陷在 bbox 计算里；文件级 → 混合文档后半段静默丢失 |
| 9 | 扫描页引用**只到页级**：`region` + 页码 + 标注「机器识别」（D9-5） | m04789「你推荐的」 | 要字符级就得让 VLM 出 bbox 反推偏移，准确率不可靠且 I6 例外爆炸 |

D9-2 至 D9-7（三信号判据、复用 OpenAI 兼容端点、缓存钉住不可复现、`region` 定位、`PARSE_INCOMPLETE` 部分成功、60s 合流）为上述两条定案后**顺着推出**的技术选择，未经用户逐条确认，标注为「提议」而非「已定」，实施前若与用户理解不符须重开。

## 未验证项（如实记录）

**选型证据类**

- `pdfplumber` 相对 `pypdf` 的实际耗时倍数：**未测**；
- `pdfplumber` 的 MIT 许可证：**未逐版本核验**（只核了 PyMuPDF 是 AGPL-3.0，同样未逐版本）；
- `pdfminer.six` 是否纯 Python、体积量级：**未核**；
- `camelot` / `tabula` 的否决理由（Ghostscript / JVM）：基于公开认知，**未验证**；
- LibreOffice 的「镜像 +400~700MB、启动 1–3s、profile 锁冲突」三个数字：**未核**；
- 「`.doc`/`.ppt` 没有纯 Python 解析器」：**绝对断言，未穷举 PyPI**；
- 「pdfplumber 是达成本目标的可选路径之一」中，备选（docling/unstructured）的许可证与体积：**未核**；
- 视觉通道：实际部署模型对**扫描表格**的识别质量与幻觉率：**未测**；
- 三信号阈值（`vision_min_chars` / `vision_image_area_ratio`）：**无样本可验**（仓库内 0 份扫描件，只有 2 个 PDF）；
- VLM 单页延迟（1–3s 是量级估计）：**未测**，它直接决定 `vision_max_pages` 与 60s 预算是否够用；
- `pdfplumber` 的 `bbox` 与图片对象坐标是否足以支撑 D9-1 第二步的「块级补洞」：**未核**。

**机制类**

- 心跳在「认领单独提交 + 独立连接」下是否真能发出：**未实测**（本 ADR 已给出前置条件，但未验证）；
- 进程池在 Windows 下的首次启动开销与 `bytes` 复制成本：**未测**；
- `run_worker.py` 改事务边界后对 `SKIP LOCKED` 并发语义的影响：**未测**；
- 存量 `chunks` 的 `source_format` 是否足以推断 `kind`：**未核**（取决于 `documents` 表是否存 MIME/后缀）。

**实现前必须补的读取类**

- `locator_ref` 已存在、`0012`/`0013` 已占用号段：本轮评审才发现，说明原稿**未通读迁移目录**——实施前须完整通读 `migrations/` 与 `v_active_chunks` 定义。

## 评审记录

2026-11-02 外部只读评审（子 agent `7c526352`，未跑测试、未起库）结论：**不合格**，4 条 P0。本轮已全部修正：

| 级别 | 问题 | 本版处理 |
| --- | --- | --- |
| P0-1 | 迁移编号撞号（`0012`/`0013` 已存在） | 全文改为 `0014`（D1 / 后果 3 / 验证条件 / 待确认 4） |
| P0-2 | D6 心跳不可实现（认领与解析同事务持行锁） | 新增「心跳的三条前置改动」+ 影响面补 `run_worker.py:68-77`、`ingest_worker.py:321-331` |
| P0-3 | 定位双真相（`locator_ref` 已存在却未提） | 新增 D2a 分工表：`locator_ref` 保留、`locator` 新增，不取代 |
| P0-4 | D3 与 `splitter.py` 机制冲突，影响面漏列 | 新增 D3 第 1–5 条兼容规则 + 影响面补 `splitter.py` + 后果 4 记静默回归风险 |
| P1 | `PARSE_TIMEOUT` 是既有码（实际没有） | 新增 D7（错误码）并列入 `errors.py` / `_NO_RETRY` |
| P1 | `table.cells` 塞不进 DOCX 表格 | D2 表拆分：`cells` 仅电子表格，DOCX 用 `index`+`rows` |
| P1 | 视图新列只能追加末尾（ADR 未写） | D1 影响面「视图」行写明 |
| P1 | `.odt/.ods/.odp` 自写无验收 | D4 补工作量与验收三条 |
| P1 | D5「唯一可行路径」与备选自相矛盾 | 改为「当前可行路径」，并说明备选属已否决方案 |
| P1 | 漏 0004 的 golden set 要求 | D5 末段 + 替代关系段补写 |
| P2 | D7 跳号 / 「六条」实为 7 项 / 格式数 / 用例数 / `page` 缺 `heading` | 依次修正：补 D7、改「七条」、改「5→14 格式 / 7→16 后缀」、改「约 42 条」、D2 表补 `heading` |
| P2 | 引用行号错误 | 修正 `jobs.py:143`（非 129）、`jobs.py:275-284` 的 `has_live_processing`（非 280 的 epoch 守卫）、`reads.py:30-36`/`42-49` 两份列清单、`parser.py:195-202`、`renew_lease` 改为「生产路径从未调用」、`lxml` 改为「直接声明依赖」 |
