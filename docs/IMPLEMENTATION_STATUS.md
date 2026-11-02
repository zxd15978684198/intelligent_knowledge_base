# PyKnora 实施状态

更新日期：2026-11-02（Asia/Shanghai）。**2026-11-02 追加（最新）：解析升级 DAG W3 —— 九类新格式解析器 + 统一派发表（N7–N10）**——`SUPPORTED_SUFFIXES` 7 → **16**，新增 `parsers/{tabular,slides,odf,mail_rtf}.py`（xlsx/xls/csv/pptx/odt/ods/odp/eml/rtf）与 `_PARSUB_MODULES` 延迟导入路由；`TableLocator` 增 `slide`/`sheet` 两个容器字段，PPTX/ODS 表格不再丢页号；新增 `test_parser_dispatch.py` 做「白名单 ⇄ 派发表 ⇄ 模块 `SUFFIXES`」三表对账；门禁 `ruff` 全过、`727 tests / 0 failures / 16 skipped / 10.09s`。**真实 xlsx/pptx/rtf 未验**（7 个解析依赖未入 `pyproject.toml`，本机无网络）。详见下方「本轮：解析升级 DAG 的 W3 波次」。**2026-11-02 追加：解析升级 DAG W1 五路全回齐 + N4 pdfplumber 复测（ADR 新增 D5-1，提议）**——W1 的 N1–N5 已全部交付并提交 `27f5a3e`（12 文件）。N4 实测：常规 ≤40 页文档 **2.18–2.81×（中位 2.5×）**，**200 页 56.08s** 越过 D5 的 30s 线 1.8 倍（越线点在 150–200 页之间），耗时跟**每页字符量**走而非 KB；**内存 3.45–3.8 MB/页且近线性**（200 页 +690 MB，外推 1032 页 ≈ 3.5–3.9 GB，比 90s 租约更早出问题）；**表格检测不可信**（S4 9 次检测 **0 张可用**，带框文本框被当 1 列整页文字）；最坏情况是**文本与表格分两次 `open()` = 12.8×**，必须一份文档只 open 一次、同 page 对象先 `extract_text` 再 `find_tables`。以上写入 ADR 新增 **D5-1（提议）**，并把「≥150 页按页分流」「≤100 页/批」「表格准入规则」写进施工计划 N13 验收。**2026-11-02 追加：解析升级 DAG 的 W1 波次（N1–N5 五路并行执行）**——DAG 见 `docs/plans/文档解析升级施工计划.md` §3.1（5 波，仅 W2/N6 单节点汇流）；本轮产出 `schemas/locator.py`（六种 `kind` 契约，44 用例）、`services/ingest/guards.py`（解压前第一道闸，27 用例）、`core/errors.py` 的 `PARSE_TIMEOUT`；N5 实测**推翻** D9-7 的 20s 页级预算（16 次单页 max **35.03s**、18.75% 超 20s、子进程峰值 RSS **444 MB**、spawn→首帧 30.9s），已写入 ADR 新增 D10-8（提议）；门禁 `ruff` 全过、`570 passed, 5 xfailed`。详见下方「本轮：解析升级 DAG 的 W1 波次（N1–N5）」。**2026-11-02 追加：文档解析升级施工计划 v1.0（ADR-0008 的落地施工图，提案，纯文档轮）**——用户问「文档解析升级你落实成计划没」，答案是**没有**：`docs/adr/0008_*.md` 决策齐了（D1–D10），但 `docs/plans/` 下无任何解析计划、代码落地率 **0%**（`parser.py:19` 仍 7 个后缀、仍 4 个 `_parse_*`、无 `PARSE_TIMEOUT`、`chunks.locator` 列未建）。本轮补 `docs/plans/文档解析升级施工计划.md`（S1–S8，约 8–12 人日），见下方「本轮：文档解析升级施工计划 v1.0」。**2026-11-02 追加：RAG 评测完善计划 v1.0（同时参考 Day4 + Day5 的评测与可观测性口径，提案，纯文档轮）**——新计划写入 `docs/plans/RAG评测完善计划.md`（P0–P6，约 6 人日），取代 v1.3 的差距与阶段口径；12 条缺口全部实读取证（评测集 20 条无 `case_id`、raw 28 键无 `request_id`、全仓无日志配置、安全组只有 2 条 deny、`models/tables.py` 无 usage 列）。详见下方「本轮：RAG 评测完善计划 v1.0」。**2026-11-02 追加：ADR-0008 补 D10 双通道（OCR 认字 / VLM 认结构）+ 本机实测数字**——用户指出「OCR 适合提取无结构的图片内容，多模态模型是提取有结构化的内容」，而 D9 只有一条 VLM 通道，属于设计错误（把识图和认字合并）。本轮选型 `pypdfium2`（渲染）+ `rapidocr-onnxruntime`（识别）并用真实 40 页 PDF 实测：**单页 OCR 3.5–19.7s**，推翻了原「整份 60s 预算」假设，D9-7 改为按页预算；D9-5 收窄为「OCR 块可到 region+bbox、VLM 块仍页级」。见下方「本轮：ADR-0008 补 D10 双通道」。**2026-11-02 追加（最新）：ADR-0008 补 D9 视觉通道（条件触发多模态）**——用户指出原始需求含「遇到表格或扫描件条件触发多模态模型」，而第一版 ADR 只把它写成缺陷、无任何决策（漏项）。本轮补 D9-1…D9-7，并回改 D2（加 `region` 分支）、D3（I6 对视觉块完全不适用）、后果（负面 +3 条）、验证条件与未验证项；D9-1、D9-5 经用户确认，D9-2…D9-7 标「提议」。**2026-11-02 追加：前端收尾轮（N10 + N11）**——补齐成员管理页（10 文件 + 36 用例）与 Playwright e2e/axe（6 spec / **12 例全过**，5 路由 axe **全 0 违规**），并修真浏览器才暴露的 3 个缺陷：业务请求不带 `Authorization`（`client.ts` 三档 token 语义）、对比度 3.36:1 不达 AA（`--color-fg-subtle` → `#5f7175`）、无障碍名与可见文字不符；`pnpm typecheck` 0 错、`vitest` **32 文件 / 250 用例**、`build` 9 路由，见下方「本轮：前端收尾（N10 + N11）」。**2026-11-01 追加：前端 P0 交付**——`web/` 从零建成 Next.js 16 + React 19 应用（174 文件 / 8 条路由），`pnpm typecheck` 0 错、`pnpm exec vitest run` 28 files / 212 tests passed、`pnpm build` exit 0；配套修 `filename` 恒 `null`（`tests/api/test_api_documents.py` 41 passed）。真浏览器 e2e/axe 与真后端 SSE 联调**未跑**，见下方「本轮：前端 P0 交付」。**2026-10-30 追加：Day03 DAG 执行与真实基线评测完成**——计划 §8 的 23 个节点按 6 个波次（G1–G4 并行 + G5/G6 串行收尾）执行完，两轮真实评测落盘（`eval/results/baseline-20261030/`、`candidate-20261030/`、`compare-20261030.md`，`app_commit=4e16d652cea0f730244571dd92c16a1a112f0be4`、`repeats=1`）；七指标 baseline→candidate：Recall@5 1.000→1.000、Precision@5 0.200→0.200、引用准确率 0.356→0.318、无证据拒答 0.667→0.333、权限拦截 1.000→1.000、检索 P95 375.1→211.8 ms、问答 P95 5586.0→5831.2 ms；**结论：候选不优于基线，本轮不采用**（`docs/Day03/04-优化前后对比.md`），见下方「本轮：Day03 DAG 执行与真实基线评测」；**2026-10-30 追加：Day03 可信 RAG 闭环基座落盘**——评测工装 `eval/`、真实语料入库（A 区 KB 3 篇 **98** 活跃片 = 61/35/2；B 区 deny KB 1 片）、评测集 **20** 条（fact 6 / exact_term 4 / synonym 3 / no_evidence 3 / deny 2 / version 2）、契约加 `active_document_version_id` 与 `retrieval_strategy` 开关、证据门控 + `RunBudget` + `DoneEvent.status` 五态；6 个新单测文件 **283 passed, 1 xfailed**（8.39s，exit 0），`tests/api` 与基线/候选评测两轮均未跑（不写指标），**任务 13/14 回调 `进行中/75%`**，见下方「本轮：Day03 可信 RAG 闭环」；**2026-10-30 追加：授权撤权链补两条真库 HTTP 用例**——同时**更正上一轮的两条错误结论**（`_load_membership` 早已是真查询、`degraded` 早已进 SSE 首个 `retrieval` 事件，都是我引用了过期状态），见下方「本轮：授权撤权链补两条真库 HTTP 用例」；**2026-10-30 追加：功能检查 P0–P3 收口**——坏文件打死 Worker、测试清空参考数据、错误体两套等七条已修，全量 375 passed，见下方「本轮：功能检查 P0–P3 收口」；**2026-10-29 追加：审查三条缺陷收口（经用户「要，一并处理审查」授权）——`services/queue/jobs.py` 的 `claim()` 增加 `job_types` 过滤并由 `workers/ingest_worker.py` 显式声明 `SUPPORTED_JOB_TYPES=("ingest","purge")`（原先任何 Worker 都可能领走不认识的类型、判 unknown 后作业直接丢）、`services/auth/jwt.py` 强制 `exp`（原先缺 `exp` 即放行 = 永久令牌，fail-open）、`api/v1/documents.py` 抽出 `_read_bounded()` 按 1MiB 分块读到上限即断（原先整包读进内存才判断 50MB），新增 5 条用例，全量 **308 passed / exit 0**，见下方「本轮：审查三条缺陷收口」**；紧接其后是检索两路真并行（成员3 域，经用户「5要并行」授权）——`services/retrieval/search.py` 的 `search_hybrid` 原先 docstring 写「并行」实则两串行 `await`，改为 `asyncio.gather` 两路；因 `AsyncSession` 不支持并发语句，新增独立 session 依赖 `get_keyword_search_repo()`，`query.py`/`chat.py` 注入透传，另加 1 条「关键词必须走第二个仓库」的单元断言，全量 **303 passed / exit 0**，见下方「本轮：检索两路真并行」**；紧接其后是成员管理与权限自省补全（成员4 域，经用户「你写一份成员管理修复计划，然后执行」授权代做）——补上 6 个端点（`GET`/`POST /workspaces/{ws}/members`、`DELETE .../members/{id}`、`PUT /workspaces/{ws}/owner`、`GET .../members/{id}/permissions`、`GET /auth/me`）、2 个 repo 方法（`delete_membership` / `transfer_ownership`）、22 个新用例（其中 5 个是"新端点忘挂权限"的结构守卫），全量 **297 passed / exit 0**，见下方「本轮：成员管理与权限自省补全」**；再往前是中文全文检索从「查不出」修到「能查出」（成员2 域，经用户「现在先不做评测集合，把中文 FTS 修好或砍掉」授权）——`tsvector` 改为 `to_tsvector('simple', pyknora_cjk_bigram(content))`、查询侧改用 `pyknora_cjk_tsquery()` 并做 AND→OR 两级降级、新增 `ChunkRepo.search_keyword` 原语，新增迁移 `migrations/0007_cjk_bigram_fts.sql`（零新扩展、零新镜像），见下方「本轮：中文 FTS 修好」**；紧接其后是「角色是数据，不是 DDL 枚举」（成员4 域，经用户「补」授权代做）——摘掉 `workspace_memberships.role` 的列举式 `CHECK`，换成格式约束 + 指向 `roles(role_key)` 的外键，新增 `migrations/0008_roles_are_data.sql`、角色 CRUD 两个端点、`RoleCatalog` 改由 `roles` 表驱动，见下方「本轮：角色是数据，不是 DDL 枚举」**；再往前是 `role_grants` 从「预留」变「已接入」（成员4 域，经用户「好吧你落地role_grants」授权代做）——运行期判权链改为 `workspace_memberships.role` → `roles` → `role_grants`，新增 15 个权限点、6 个 `/workspaces/{ws}/roles|permissions` 端点、`api/deps.py` 权限依赖工厂，全量 **263 passed**，见下方「本轮：`role_grants` 落地」**；再往前是 P0–P4 安全与一致性收口（JWT 密钥生产护栏 / 停用账号令牌立即失效 / RLS 部署缺口启动告警 / `role_grants`·`is_public`·权限缓存三处口径注释），见下方「本轮：P0–P4 安全与一致性收口」**；再往前是文档事实校正——错误码 16 → 17（`AGENTS.md` §2、结构说明两处、`docs/tdd/07` §7 登记 4 个新增码为提议），见下方「本轮：文档事实校正」**；再往前是任务 17 迁库演练完成（成员5 实现 / lead 记录）——17/17 任务全部 `已完成`（「本轮：任务 17 迁库演练」）、任务 16 重建索引 `/admin/reindex` 完成（「本轮：任务 16 重建索引」）、B0 环境对账 + B2 主链路烟测（「本轮：B0 环境对账 + B2 主链路烟测」）、合并远端 chat 分支 + 修 `index_migrations` 主键口径（「本轮：合并远端 chat 分支」）；更早的成员2 两条 P0 修复、成员2 四任务收口、成员4 验收闭环、成员1 S05、身份基座落地、数据契约外键、Phase 0 / P0-T01 章节保留为其自身时点的历史记录。

## 本轮：解析升级 W3 节点 N12（进程池隔离 + 租约心跳，2026-11-02）

- 任务：DAG W3 的第五路。解析器此前直接跑在 worker 主进程里——一个吃内存的 200 页 PDF（实测 peak 776 MB）或一个卡死的 ODF 会把整个 worker 拖住，而租约 90s 到期后作业会被另一个 worker 重新领走，同一份文件被并发解析两遍。
- 改动文件：新增 `src/pyknora/services/ingest/pool.py`（187 行）、`tests/unit/test_parse_pool.py`（262 行）；修改 `src/pyknora/workers/ingest_worker.py`（+136/−11）、`scripts/run_worker.py`（+19/−11）。
- 实现要点：
  - **解析进常驻进程池**：`ProcessPoolExecutor(max_workers=1)` + 60s 硬超时（`pool.run_in_pool` 接在 `ingest_worker` 的解析调用点，`ingest_worker.py:302`）；超时/子进程崩溃即整池重建（`BrokenProcessPool` 从 `concurrent.futures.process` 导入——写错命名空间会让全量单测在收集期就 ImportError，本轮踩过）。
  - **`AppError` 跨进程重建**：异常对象不可 pickle，子进程侧 `_run_in_child` 就地降级成三元组，父进程原样重建，错误码与 `retryable` 不丢。
  - **租约心跳 30s**（`HEARTBEAT_INTERVAL_S=30.0`，租约 90s → 丢两次仍安全），连续 3 次失败才放弃（`HEARTBEAT_MAX_CONSECUTIVE_FAILURES=3`）；心跳走**独立 session**，且 `run_once` 改成「先提交认领、再把作业体包 SAVEPOINT」——否则认领的 `UPDATE` 一直持行锁，独立连接上的续租会阻塞到作业结束（ADR 0008 D6 记录的这个坑）。
  - `PARSE_TIMEOUT` 进 `_NO_RETRY`（超时不重试，重试只会再超时一次）。
- 已执行命令与真实结果：`ruff check`（pool.py / test_parse_pool.py / ingest_worker.py / run_worker.py）→ `All checks passed!`；`pytest tests/unit/test_parse_pool.py tests/unit/test_errors_parse_timeout.py` → 17 passed，exit 0；全仓 `pytest tests/unit` → **727 tests / 0 failures / 16 skipped**。
- 未验证：未真起两个 worker 竞争同一作业（心跳与重领的并发语义只有单元级覆盖）；进程池的 60s 超时未用真实慢文件触发（用例用桩函数 sleep）；`uv run --no-project` 下子进程能否 recover 未测（Windows spawn 与 fork 的 import 差异只在真 worker 里体现）。
- 下一步：N6b（`reads.py`/`schemas/document.py`/`services/rag/answer.py` 三处 locator 贯通）待跨会话释放后启动 → W5 收口。

## 本轮：解析升级 DAG 的 W3 波次（N7–N10 + 派发表接线，2026-11-02）

- 任务：用户 m06360「根据文档升级计划绘制 DAG 执行路线，然后根据 DAG 开启子 agent 并行执行，非单节点禁止串行」的第 3 波。四个节点并行施工，**Lead 独占 `parser.py` 派发表**（5 路同改一个文件必冲突，故派发集中到收口这一步）。
- 改动文件：新增 `src/pyknora/services/ingest/parsers/{tabular,slides,odf,mail_rtf}.py`、`tests/unit/{test_parse_tabular,test_parse_slides,test_parse_odf,test_parse_mail_rtf,test_parser_dispatch}.py`；修改 `src/pyknora/services/ingest/parser.py`、`src/pyknora/schemas/locator.py`、`src/pyknora/services/ingest/parsers/{slides,odf}.py`、`docs/adr/0008_*.md`（D5-1、D10-8 由「提议」改「已决」）。
- 各节点结果：
  - **N7 电子表格**（`.xlsx/.xls/.csv`，363 行 + 471 行用例）：一表一块 `Block(is_tabular=True, locator=SheetLocator(sheet=…, cells="左上:右下"))`，制表符渲染、全空行仍计入范围以免行号错位；`check_zip_size` 先于 `load_workbook`，跨表累计行数走 `check_document_limits`。
  - **N8 幻灯片**（`.pptx`，200 行 + 343 行用例）：文本形状 → `SlideLocator(slide=n, shape=name)`（1 起），表格 → `TableLocator(index=全文序号, rows=…, slide=n)`，组合形状递归、图片/图表跳过、空页不产块。
  - **N9 ODF**（`.odt/.ods/.odp`，384 行 + 538 行用例）：零新依赖（`zipfile`+`ElementTree`），闸门顺序 `开 zip → check_zip_size → content.xml → check_odf_repeated → ET.fromstring`；重复次数**超限抛错不截断**（`number-rows-repeated="20000"` 抛、`"10000"` 放行并真展开）。
  - **N10 邮件/RTF**（`.eml/.rtf`，264 行 + 138 行用例）：`.eml` 只用标准库 `email`，先判 `Content-Disposition: attachment` 再递归（避免把转发邮件正文吸进来）；`.rtf` 走 `striprtf`，缺包 → `PARSE_FAILED`（该分支有不依赖真库的用例覆盖）。
  - **Lead 接线**：`SUPPORTED_SUFFIXES` 7 → **16**、`SUPPORTED_MIME` 补 9 个族；新增 `_PARSUB_MODULES`（后缀 → 子包模块）与 `_parse_via_submodule()`，**必须延迟导入**——`parsers/*.py` 反向 import `parser.Block/ParsedText`，顶层 import 会循环；`test_parser_dispatch.py` 115 行做三表对账（白名单 ⇄ 派发表 ⇄ 各模块 `SUFFIXES`），并真的走一遍 `parse()` 确认路由与参数。另修 N8 报的契约缺口：`TableLocator` 增可选 `slide`/`sheet`，PPTX/ODS 表格不再丢「在哪一页/哪张表」。
- 三处子 agent 当场纠正我的错误指令（记录以免复犯）：`ParaLocator.index`/`TableLocator.index` 是 **1 基**（`Field(ge=1)`），我写的「从 0 开始」会被 pydantic 拒；`draw:page` 在 drawing 命名空间而非 presentation；`.rtf` 的 `parse` 签名**不带** `content_type`。
- 已执行命令与真实结果（Lead 收口）：`ruff check src tests scripts` → `All checks passed!`；`ruff format --check src tests scripts` → `161 files already formatted`；`pytest tests/unit`（JUnit XML 计数）→ **`727 tests / 0 failures / 0 errors / 16 skipped / 10.09s`**，exit 0。
- 未验证：**真实 `.xlsx`/`.pptx`/`.rtf` 解析没跑过**——`openpyxl`/`python-pptx`/`striprtf` 都不在 `.venv`，本机 `uv run --with` 连 pypi 超时，相关用例走 `sys.modules` 桩或 `importorskip` 跳过（`.xls` 的 xlrd 路径**完全未验**）；未接 N12 的进程池/超时隔离（解析器现在仍在 worker 主进程内跑）；`.doc/.ppt` 按 D4-P3 默认 422 拒收，未接 LibreOffice 开关。
- 阻塞说明：**7 个解析依赖未入库**（`pyproject.toml` 里 `openpyxl`/`xlrd`/`python-pptx`/`pdfplumber`/`striprtf`/`pypdfium2`/`rapidocr-onnxruntime` 全无），当前只能 `uv run --with` 临时装，生产里这三类文件会落到「缺包 → `PARSE_FAILED`」。需能联网时补依赖并复跑被跳过的用例。
- 下一步：**N12 已随本次一并交付**（进程池 + 租约心跳，见上方段）→ 随后 N6b（跨会话文件释放后）→ W5 收口。

## 本轮：解析升级 DAG 的 W1 波次（N1–N5，2026-11-02）

- 任务：用户 m06360「根据文档升级计划绘制 DAG 执行路线，然后根据 DAG 开启子 agent 并行执行，非单节点禁止串行」。DAG 已写入 `docs/plans/文档解析升级施工计划.md` §3.1（5 波，仅 W2/N6 是单节点汇流）。本轮执行 **W1 五路并行**，全部节点只写自己名下的文件、不碰 git、不改 `pyproject.toml`/`uv.lock`（临时依赖走 `uv run --with`）。
- 改动文件：新增 `src/pyknora/schemas/locator.py`、`src/pyknora/services/ingest/guards.py`、`tests/unit/test_locator_schema.py`、`tests/unit/test_errors_parse_timeout.py`、`tests/unit/test_parse_guards.py`、`docs/plans/parsing-bench-ocr.md`（+ N4 的 `parsing-bench-pdf.md`）；修改 `src/pyknora/core/errors.py`、`src/pyknora/workers/ingest_worker.py`、`tests/unit/test_citations.py`（过期计数 17→18）、`docs/adr/0008_*.md`（新增 D10-8、D5-1）、`docs/plans/文档解析升级施工计划.md`（§3.1 DAG、W2 拆分为 N6a-1/N6a-2/N6b、N13/N14 验收补实测约束）。
- 各节点结果：
  - **N1 locator 契约**：`schemas/locator.py` 127 行，六种 `kind` 的 discriminated union（`page`/`para`/`sheet`/`slide`/`table`/`region`），`bbox` 用 `tuple[float,float,float,float]` + 有序性校验，基类 `frozen=True, extra="forbid"`，`inferred` 放基类；44 用例。Lead 独立复核 `TypeAdapter(Locator).validate_python(...)` 三种 kind 均正确，`LOCATOR_KINDS=('page','para','sheet','slide','table','region')`。
  - **N2 PARSE_TIMEOUT**：`core/errors.py:33-37` 加码、`:58` 加 `422`；`workers/ingest_worker.py:67` 进 `_NO_RETRY`。**纠正 Lead 的一处错误指令**：`_RETRYABLE` 是「可重试集合」，`retryable` 实现为 `code in _RETRYABLE`，所以「不可重试」由**缺席**保证，照「三处登记」加进去反而会变成可重试——只在集合上方加注释钉死语义。
  - **N3 第一道闸**：`services/ingest/guards.py` 151 行 + 27 用例。边界统一为**闭区间**（`actual > limit` 才抛），理由是 D6 原文是「上限 10 000」，而 10 000 在 ODF 规范里合法。ODF 重复次数用「剥前导零 + 比长度/字典序」而非 `int()`，规避 Python 3.12 `int()` 4300 位上限把预检自己搞崩（已测 5001 位数字串不崩）。
  - **N5 OCR 子进程实测**：`docs/plans/parsing-bench-ocr.md`。**推翻 D9-7 的 20s 页级预算**：16 次单页 min 7.32 / median 13.62 / max **35.03s**，>20s 占 18.75%，同图重复 5 次波动 3.8× 且与框数无关；子进程识别期峰值 RSS **398.9–444.1 MB**（稳态仅 50–100 MB），spawn→首帧 **30.9s**。已写入 ADR 新增 **D10-8（提议）**：建议页级 ≥45s 或超时降级为 `ocr_slow`、容量按峰值规划、`max_workers=1` 常驻是性能前提。
  - **N4 PDF 引擎实测**：`docs/plans/parsing-bench-pdf.md`（219 行）。4 份真实 PDF + 1 份 1032 页文档按 50/100/150/200 页切片。倍数中位 **2.5×**；**200 页 56.08s 越 30s 线 1.8 倍**，但 50 页冷启 18.13s **不触发** D5 字面口径，故建议把红线改成页数口径「≥150 页按页分流」；**内存 3.45–3.8 MB/页近线性**（peak 776 MB，外推 1032 页 ≈3.9 GB）；**表格准入必须过滤**（S3 4/4 且算术自洽，但 S4 9 次检测 0 张可用、X1 上大量 ASCII 目录树误判）；字符完整度不是问题（pdfplumber 不丢正文，反而修回 214 个 `U+007F`）。已写入 ADR 新增 **D5-1（提议）**。
- 已执行命令与真实结果（Lead 收口，全仓）：`ruff check src tests scripts` → `All checks passed!`；`ruff format --check src tests scripts` → `148 files already formatted`；`pytest tests/unit` → **`570 passed, 5 xfailed in 16.07s`**。
- 未验证：`PARSE_TIMEOUT` 尚无生产代码抛出（超时执行器属 N12）；`guards.py` 未接进 `parser.py`（属 N6/N12）；200 MiB ZIP 用桩验证而非真实归档；≥5 页真实扫描件样本缺失；`locator` 未落库（0014 属 N6）。
- 阻塞说明：**W2/N6 暂缓**，不是 DAG 依赖问题（N1 已满足），而是工作区有**另一会话**未提交的 `db/repos/reads.py`、`schemas/document.py`、`services/rag/answer.py`（N6 三个目标文件），此时动等于覆盖他人工作。等其提交后再放行。
- 下一步：N4 回来后收口 W1 提交 → 跨会话改动落盘后启动 N6。



## 本轮：文档解析升级施工计划 v1.0（ADR-0008 落地，2026-11-02，提案）

- 任务：用户 m06323「之前我说文档解析升级你落实成计划没」。核查结论：**决策已落 ADR，施工计划从未落盘，代码 0 行**。本轮补施工计划，**纯文档轮：没有改任何代码、没有改表、没有新增依赖**。
- 改动文件：新增 `docs/plans/文档解析升级施工计划.md`（v1.0，约 230 行）；本文件。
- 现状基线（实测，全部有行号）：
  - `src/pyknora/services/ingest/parser.py:19` 仍是 `SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm"})`（7 个后缀）。
  - 仍只有 4 个解析函数：`_parse_pdf`（`:75`，pypdf）/ `_parse_docx`（`:108`，不读 `document.tables`）/ `_parse_text`（`:142`）/ `_parse_html`（`:175`）。
  - `src/pyknora/core/errors.py:13-32` 现有 17 个 `ErrorCode`，**无** `PARSE_TIMEOUT`；`chunks.locator` 列不存在（`migrations/0014` 未建）。
  - 相关提交只有 `f7b3aa1` / `987469a` / `b60a177`，**全是文档**，`git log` 无任何 `feat(m2)` 解析提交。
  - `docs/plans` 按 `解析|ADR-0008|pars` 过滤 → **0 命中**（这就是「没落实成计划」的证据）。
- 计划骨架（S1–S8，约 8–12 人日）：**S1 契约先行**（`schemas/locator.py` 六种 `kind` + `splitter.py` 表格块 I6 例外 + `PARSE_TIMEOUT` + `migrations/0014_chunk_locator.sql` 存量回填 `inferred:true` + 8 处读写贯通）→ S2 P1 格式七件套（xlsx/xls/csv/pptx/odt/ods/odp）→ S3 P2/P3（eml/rtf；`.doc`/`.ppt` 默认 422）→ S4 PDF 换 `pdfplumber` + golden set ≥10 → S5 运行时隔离（两道闸 + 心跳三条前置）→ S6 D10 双通道（`pypdfium2` + `rapidocr-onnxruntime`）→ S7 评测基准切换（D8）→ S8 前端 `locator` 渲染。
- 顺序理由（写进计划 §3）：`locator` 要穿过 `reads.py` 两处列清单、`tables.py:127`、`snapshot.py:170-189`、`schemas/{chunk,document}.py`、`answer.py:196-202`、前端 `format.ts:138` 共 8 处；先接格式再补 locator 等于把 9 个解析器返工一遍。
- 依赖清单（计划 §5）：`openpyxl` / `xlrd` / `python-pptx` / `pdfplumber`（连带 `pdfminer.six`）/ `pypdfium2` / `rapidocr-onnxruntime` / `striprtf`。**发现口径不一致**：ADR「已决项」写「接受 6 个新增依赖」，D10 又加 2 个，实际 7–8 个——已写入计划 §5，需在 ADR 补一行更正。
- 已执行命令与真实结果：`Get-ChildItem docs\plans -Filter *.md` + `Select-String` 过滤 → 0 命中；`git log --oneline` 三条 ADR 提交全为文档。**未跑** `ruff`/`pytest`——本轮只写 Markdown，无 Python 变更。
- 未验证（已写入计划 §8）：`pdfplumber` 相对 `pypdf` 的真实倍数；`rapidocr` 在解析子进程内的内存与首帧加载；`.doc`/`.ppt` 是否有可靠纯 Python 解析器；新增依赖的镜像体积增量；`0014` 回填 `inferred:true` 是否影响前端渲染。
- 未改 `docs/plans/成员任务进度表.md`：本轮不改变 17 个任务状态（S1–S8 是 ADR-0008 的施工分解，尚未纳入进度表编号）。
- 下一步：等用户批准施工顺序，从 **S1** 开工；S1 只动类型/错误码/迁移，不接任何新格式，便于回退。

## 本轮：RAG 评测完善计划 v1.0（参考 Day4 + Day5，2026-11-02，提案）

- 任务：用户 m06235/m06238 要求「参考 Day5 与 Day04 两份讲义，**同时**参考其中的 RAG 评测与可观测性内容，另写一份 RAG 评测完善计划」。本轮产出**新计划文档**，**纯文档轮：没有改任何代码、没有改表、没有新增依赖**。
- 改动文件：新增 `docs/plans/RAG评测完善计划.md`（v1.0，约 240 行）；本文件。
- 与上一份的关系：取代 `docs/plans/RAG评测与可观测性完善计划.md`（v1.3）的 §3 差距清单与 §4 阶段口径；v1.3 保留为讨论记录，不删除。
- 本轮的实测证据（全部实读，非推断）：
  - 评测集 **20 条**（`eval/results/baseline-20261030/summary.json:9` `raw_lines: 20`；`counts.samples: 20`），6 类 category：fact 6 / exact_term 4 / synonym 3 / version 2 / no_evidence 3 / deny 2（由 `per_category.*.samples` 求和）。
  - **评测集无 `case_id` 字段**：`grep '"case_id"' eval/eval_set_v0.1.jsonl` → 0 命中；raw 用 `sample_id`（`eval/run_eval.py:794`）。
  - raw 记录 **28 键**，字段清单见 `eval/run_eval.py:793-819`；**不含 `request_id`**。
  - 8 指标齐备且带 `definition`（`summary.json:10-91`）：recall_at_5 / precision_at_5 / citation_accuracy / abstain_accuracy / deny_interception / retrieval_p95_ms / chat_ttft_p95_ms / chat_total_p95_ms。
  - 单因素守卫 6 个主因素 + `--compare-config` exit 2；deny 泄漏 exit 3（`eval/README.md:111`）；三件套纪律（`eval/README.md:105-106`）。
  - `X-Request-ID` 存在（`src/pyknora/api/main.py:285-288`、`:127-129`、`:93`），但**不进日志行**（无 handler）+ **不回流评测**。
  - `models/tables.py` **无** `usage`/`ttft`/`latency` 列（grep 0 命中）——讲义 §8.3 的 `agent_runs.usage` 在本仓库不存在，用量只能从响应体 `prompt_tokens`/`completion_tokens` 取。
- 12 条实测缺口（计划 §2.2）：G1 无 case_id｜G2 无 manifest_sha256｜G3 raw 无 request_id｜G4 全仓无日志配置、INFO 全丢｜G5 无 stage/outcome/error_type｜G6 安全组只有 2 条 deny，缺 I/P/A 各 4｜G7 无 review 三态｜G8 no_evidence 的 status 缺失靠文案 fallback｜G9 无分阶段计时｜G10 无 gate.py｜G11 无三故障定位手册｜G12 性能基线无人工产物落点。
- 阶段（合计约 6 人日）：P0 逐例可追溯（case_id + manifest_sha256 + request_id，0.5d）→ P1 日志底座（`core/logging.py` + contextvar + 四事件，1d）→ P2 分阶段计时（1d）→ P3 安全组扩到 12 题（1.5d）→ P4 review 三态载体（0.5d）→ P5 `eval/gate.py` + 基线管理（1d）→ P6 三故障排障手册（0.5d）→ P7 `/metrics` 不做。
- 「参考」的口径：只抄**契约**（检查项 / 指标定义 / 失败判据 / 表结构），不抄**内容**（不搬讲义 24 题、不搬 `fixtures`、不搬 `SYNTHETIC-HR-A-731` 之类标记）。
- 硬口径（计划 §4，共 8 条）：`null ≠ 0 ≠ 通过`｜安全组任一泄漏即整体 FAIL｜超时与报错留在分母｜门槛只在跑基线前冻结｜**性能更快但泄露即失败的优化**（Day4 §8.1）｜性能优化必须连带回归缓存/并发/上下文（Day4 §10.5）｜`repeats=1` 不许说稳定、n=20 的 P95 只能描述｜引用数字必须带 commit / 配置 / 样本量三件套。
- 已执行命令与真实结果：只有读与 grep（`grep '"case_id"'` = 0 命中；`grep 'usage|ttft|latency' src/pyknora/models/tables.py` = 0 命中）。**未跑** `ruff`/`pytest`——本轮只写 Markdown，无 Python 变更。
- 未验证（已写入计划 §7）：20 条的 category 分布是求和而非逐行读 jsonl；`manifest.json` 依赖的 `active_build_id` 未连库确认；`schemas/chat.py:91-92` 的 `prompt_tokens`/`completion_tokens` 是否每条 `done` 事件都带回未逐行核对。
- 未改 `docs/plans/成员任务进度表.md`：本轮不改变 17 个任务状态，无任务行需变更。
- 下一步：等用户批准范围与目录归属（尤其 `eval/` 是否算成员2 扩展域），从 **P0** 开工；不跨阶段。

## 本轮：RAG 评测与可观测性完善计划（2026-11-02，提案）

- 任务：用户 m04972 给出两份讲义（`Day5-学生实战讲义.md`、`PyKnora_Day04_学生实战讲义.md`），要求「参考两文的 RAG 评测部分，对项目做评测与可观测性完善；**不要硬编码参考文档内容**；前端已做；**部署和 CI 当前暂时不做**」。m05000「好先为我撰写一份 RAG 评测和可观测性完善计划」。本轮产出**计划文档**，**纯文档轮：没有改任何代码、没有改表、没有新增依赖**。
- 改动文件：新增 `docs/plans/RAG评测与可观测性完善计划.md`（约 330 行：现状盘点 / 差距清单 / P0–P6 阶段 / 分层门禁 / 口径约定 / 目录归属 / 风险 / 未验证项）；本文件。
- 现状盘点（全部实读或实跑所得，非推断）：
  - **评测侧**：`eval/` 有 6 个 py（`corpus.py` / `build_eval_set.py` / `run_eval.py` / `make_configs.py` / `report.py` / `selfcheck.py`）+ 2 个 yaml + `eval_set_v0.1.jsonl`（**20 条，单一分组**）+ `corpus_manifest.json`（**5 篇 / 98 chunk**）+ README；`eval/results/{baseline,candidate}-20261030/` 两轮真跑 + `compare-20261030.md`；指标 8 项。**缺**：门槛判定脚本、用例分组、`env_fingerprint`、P50、成本字段。
  - **观测侧**：有 `request_id`（`src/pyknora/api/main.py` 中间件）、`run_id`（`api/v1/chat.py`）、`job_id`（`workers/ingest_worker.py`）。**缺**：结构化 JSON（全仓 **50 处** `logging.getLogger(__name__)` + `%s` printf 风格）、`stage` / `outcome` / `error_type`、业务 `duration_ms`、`retrieval_count`、`token_usage`、`/metrics`。计时仅两处：`api/main.py:186-195`（embedder warmup）、`services/rag/budget.py:48-63`（`time.monotonic()`）。
  - **门禁侧**：只有 L1（`scripts/ci.py --stage all` = ruff check → ruff format --check → `pytest -q tests/unit`，实测约 7–11s）；`security` 故意独立不进 hook（`AGENTS.md` §6.2）；**无 L2/L3**。
- 计划骨架（合计约 4.5 人日）：P0 JSON 日志底座（0.5d，新增 `src/pyknora/core/logging.py`）→ P1 四个结构化事件（0.5d：`retrieval_completed` / `prompt_built` / `first_token` / `chat_finished`）→ P2 逐例字段补齐（1d，改 `eval/run_eval.py`）→ P3 用例分组与扩容（1d，v0.1→v0.2 + `injection`/`poison` 各组 ≥5）→ P4 `eval/gate.py` + 基线管理 + `env_fingerprint`（1d）→ P5 接入 `ci.py` 作为**非默认** stage（0.5d）→ P6 可选 `/metrics`（0.5d）。
- 明确不做（写进计划 §1.2）：OpenTelemetry / 结构化 Trace、GitHub Actions / 部署流水线、`/metrics`（仅列 P6）、structlog/loguru 等新依赖、RAGAS/DeepEval 等评测框架、解析侧任何改动（属 ADR-0008）。
- 「参考」的口径：**只抄契约（字段名 / 指标定义 / 失败判据），不抄实现（题目 / 阈值数字 / 代码 / 目录结构 / Actions yaml）**。判据：加的每个字段都要能用一个真实 bug 解释其必要性。计划内阈值**全部留空**，必须用本项目 baseline 实测回填。
- 三条硬口径（计划 §6）：`null ≠ 0 ≠ 通过`（未测不得算通过，`review` 为 null 占比 > 20% 直接 FAIL）｜`injection` / `privilege` 任一分组出现泄漏即整体 FAIL（不看比率）｜超时与报错的 case 留在原分母内。
- 跨域标注（计划 §7）：`services/retrieval/search.py`、`services/rag/answer.py`、`api/v1/chat.py` 属**成员3**，改动只限 logger 调用与计时、不动逻辑，需其复核并在本文件记录；`src/pyknora/api/main.py` 是共用文件，**只追加**、不改 `configure()` 签名（`AGENTS.md` §4）。
- 已执行命令与真实结果：**无**。本轮只写 Markdown，无 Python 变更，`ruff`/`pytest` 不覆盖 md，跑门禁只会验证无关代码。
- 未验证（已写入计划 §9）：`eval/run_eval.py` 内部结构**未逐行确认**（`retrieved[].rank` / `citations[].invalid` / `token_usage` 是否已存在，按文件名与约 1900 行推断）；现有日志是否已有下游消费方**未 grep 全仓**；分阶段计时的真实数值未测；阈值全部空缺；日志落盘与轮转策略未定；人工复核（`review`）流程未定义；`posix` 下的日志位置未定。
- 未改 `docs/plans/成员任务进度表.md`：本轮是计划产出，不改变 17 个任务的状态，无任务行需变更；§3.3「已完成」四条件对本轮不适用（不宣称任何任务完成）。
- 下一步：等用户确认计划范围与目录归属处理方式，从 **P0（JSON 日志底座）** 开工；不跨阶段。

## 本轮：ADR-0008 补 D10 双通道（OCR 认字 / VLM 认结构，2026-11-02 第四轮）

- 任务：修正 D9 的设计错误——只有一条 VLM 通道，等于让多模态模型去干 OCR 的活。用户 m04844 给出职责边界：**OCR 提取无结构的图片内容，VLM 提取有结构化的内容**。本轮据此把 D10 写进 ADR 并**实测两种通道的真实成本**。仍属**纯文档轮 + 临时实测**，没有改任何生产代码、没有改表。
- 改动文件：`docs/adr/0008_Document_Parsing_Contract.md`（D9-3 的「本地 OCR 不作默认」**推翻**、D9-7 **重写为按页预算**、新增 `### D10` 七小节、后果负面 +3 条、新增「D10 已决」表 6 行、未验证项 +5 条）；本文件。
- 实测对象：仓库内真实文件 `.tmp_ref/eval_corpus/基本财务管理制度.pdf`（611KB / 40 页 A4），本机 CPU，临时脚本 `.tmp_ocr_tune.py` **已删除，仓库不留痕**。
- 实测数字（命令 + 真实输出，非估算）：
  - 页面渲染 @200dpi `pypdfium2`：**0.02s**（首帧 0.34s），1654×2339。
  - 引擎初始化：**2.04s** → 必须进程内复用，不能每页新建。
  - OCR 目录页（17 框）：**3.52s**（检测 1.46 / 分类 0.02 / 识别 1.99）。
  - OCR 封面页（42 框）：**6.86s**（检测 2.39 / 识别 4.36）。
  - OCR 正文密集页（28–30 框）：**11.72–19.66s**，均值 **14.58s**；4 页串行 **58.31s**。
  - 缩放到长边 1600 → 均值 13.27s；1280 → 13.09s（**几乎无收益**，瓶颈在行数不在像素）。
  - 4 线程并发 4 页：墙钟 **36.03s**（9.01s/页墙钟），单页耗时升至 ~35s，总吞吐 **1.6×**。
  - `intra_op_num_threads=4`：17.71s（**无收益**）。
  - 识别质量（目录页原样）：`新筑股份` / `XINZHU CORPORATION` / `Q/XZG·JG·13·11-2020` / `第二十二章 财务分析管理`，置信度均值 0.97–0.99，中英混排与间隔号均正确。
- 三条结论：① **耗时与页面文字行数近似线性**（0.4–0.5s/行）；② 降 DPI 无效，优化只能靠**按需触发 + 并发**；③ **本地 OCR 并不比云端 VLM 快**（3.5–19.7s/页 vs VLM 1–3s/页量级），它的价值在确定性、零边际费用、有 bbox——这条必须写死，防止后来者误以为本地一定更快。
- 第四轮定案（写入 ADR「D10 已决」）：OCR 与 VLM 拆两条通道并按页型路由（`text_scan`→OCR / `structured`→VLM / `uncertain`→VLM 兜底，低置信度才升级）｜渲染 `pypdfium2`（`pdfplumber` 不能光栅化、`pymupdf` AGPL 否决、`pdf2image` 需外部 poppler）｜识别 `rapidocr-onnxruntime`（有框与置信度，正好对上 `region` locator 与升级判据）｜D9-7 改按页预算（单页 20s / `ocr_max_pages=20` / `vision_max_pages=50` / 整份 600s，**与 90s 租约解耦**，租约靠 D6 心跳续）｜D9-5 收窄（OCR 块可回指 bbox，VLM 块仍页级）｜**缓存介质未定**（进程内 LRU vs 新表，若选表需再加一次迁移）。
- 已执行命令与真实结果：`uv run --with pypdfium2 --with rapidocr-onnxruntime python .tmp_ocr_tune.py`（输出即上方数字，脚本与日志已删）。**未跑** `ruff`/`pytest`——本轮只改 Markdown，无 Python 变更，跑门禁只会验证无关代码。
- 未验证（已写入 ADR「未验证项」）：`pypdfium2`/`rapidocr-onnxruntime`/`onnxruntime` 的**许可证与体积均未核验**；OCR **中文准确率没有数字**（只肉眼看过一份样张，未在 golden set 上评估）；并发内存峰值未测；GPU 与无网络环境未测、CPU 型号未记录（**本机数字不可直接移植**）；OCR 的 bbox 到引用渲染链路未打通（`region` 只有契约，前端未实现）。
- 下一步不变：D4 → D6 → D5 → D2/D3 → 迁移 `0014` → 基准重建；**D10 排在 D2/D3 之后**（依赖 `region` locator）；`ocr_enabled` / `vision_enabled` 默认关闭，上线前必须先在真实扫描件上测阈值与准确率。

## 本轮：ADR-0008 文档解析契约升级（2026-11-02，成员2 域 + 跨域影响）

- 任务：把文档解析升级的六组决策（B1–B6）固化为决策记录。**纯文档轮，没有改任何代码、没有改表**。
- **同日第三轮补漏**：用户质疑「我开始不是让你升级要求处理多个是数据，遇到表格或者扫描件条件触发用多模态模型；你定的符合吗」——经 grep 核实，第一版 ADR 全文只有两处提到「扫描件」，都是缺陷描述与测试语料要求，**没有任何 OCR/多模态决策**，属于把我的既有结论（grill 前评估里写过「多模态暂不做」）当成既定前提、未回到用户原始需求核对。已补 D9 七小节并回改上游决策。
- **外部评审（子 agent `7c526352`，只读）结论「不合格」，报 4 条 P0**，逐条复核全部属实并已修正：迁移撞号（实际已到 `0013` → 全文改 `0014`）｜心跳发不出去（认领与解析同一事务、全程持作业行锁）｜`locator_ref` 早已存在（`migrations/0001_init.sql:115`、`splitter.py:107`）而 ADR 只字未提 → 补 D2a 分工表｜D3 与 `splitter.py:52-94` 冲突且影响面漏列。另修正我三处事实错误：`jobs.py:280` 是 `has_live_processing` 谓词而非 epoch 守卫｜`renew_lease()` 并非零调用（`tests/integration/test_queue_fencing.py:244-245`）｜`reads.py:32-45` 实际是两份列清单（`30-36` / `42-49`）。**教训：写 ADR 前未通读 `migrations/` 与 `chunks` 建表语句。**
- 七项待定事项已由用户授权代定（m04714「你定吧，我定不了」），逐条理由写入 ADR「已决项」：I6 限定为纯文本流｜`lxml` 口径改写｜PDF 预留降级 B 档｜`0014` 存量回填标 `inferred`｜接受 6 个新依赖｜`PARSE_TIMEOUT` 422 不可重试｜接受改事务边界以支持心跳。
- 改动文件：新增 `docs/adr/0008_Document_Parsing_Contract.md`（约 300 行：决策 D1–D8 + D2a 分工 + 备选方案 + 后果 + 验证条件 + 已决项 + 未验证项 + 评审记录）；`docs/adr/README.md` 表格补登 **0007**（此前漏登，文件早已存在）与 **0008** 并更新文首说明；**`AGENTS.md` 两处口径改写**（§2 `lxml` 改为「禁止直接 `import`」并单列一行、§4 I6 限定为纯文本流格式），理由见下方「已决项」两条；本文件。
- 六条定案：D1 验收口径 C 档（页/段/表+单元格/幻灯片可回指）｜D2 格式无关 locator（discriminated union）｜D3 表格块 I6 受控例外 + 分档切分 + `[表头]` 注入｜D4 格式三批（P1 xlsx/xls/csv/pptx/odt-ods-odp，P2 eml/rtf，P3 .doc/.ppt 默认拒 + LibreOffice 开关）｜D5 PDF 换 `pdfplumber`、排除 `PyMuPDF`(AGPL)｜D6 结构预检 + 常驻单进程池 + parse 硬超时 60s + 心跳 30s｜D8 旧评测基准废弃不删、升级后扩容语料重跑。
- D9 定案要点：触发粒度**页级起步**（块级补洞排第二步、文件级否决，混合文档会静默丢后半）｜三信号 S1 `chars==0` / S2 低字数且有图（防劣质 OCR 文字层）/ S3 图片面积占比超阈值，阈值进配置且**每页记账**以便拿真实文档调参｜复用 `chat_*` 的 OpenAI 兼容端点（`settings.py:58-61`，默认 `gpt-4o-mini` 支持图片），**零新增依赖**，视觉调用在父协程、不进进程池｜缓存键 `sha256(页面位图)+model+prompt_version` 把不可复现钉住｜`region` locator + `char_*` 为 null（`migrations/0001_init.sql:112-113` 本就允许，**无需迁移**）｜失败降级复用既有 `PARSE_INCOMPLETE`（`errors.py:26`，422）｜与 D6 的 60s 预算合流，`vision_max_pages` 默认 50。
- 本轮的**独立发现**（与解析无关，应单独修）：`services/queue/jobs.py:198-215` 的 `renew_lease()` **全仓从未被调用**；租约固定 90 秒（`core/settings.py:104`），超过 90s 的作业会被重新认领（`jobs.py:150`），`lease_epoch` 只能挡双发布、挡不住重复消耗。
- 已执行命令与真实结果：**无**。本轮只写 Markdown，`ruff`/`pytest` 不覆盖 md，未跑任何解析侧验证。
- 未验证（已写入 ADR「未验证项」）：`pdfplumber` 相对 `pypdf` 的耗时倍数；`PyMuPDF` 许可证结论未逐版本核验；6 个新增依赖（`pdfplumber`/`pdfminer.six`/`openpyxl`/`xlrd`/`python-pptx`/`striprtf`）的版本、维护状态、许可证均未核验；进程池在 Windows 下的启动开销与 `bytes` 复制成本未测；`python-pptx` 对 `lxml` 的依赖形态未在干净环境实测；**视觉通道对扫描表格的识别质量与幻觉率未测；三信号阈值无样本可验（仓库内 0 份扫描件）；VLM 单页延迟（1–3s 是量级估计）未测；`pdfplumber` 的 bbox 能否支撑块级补洞未核**。
- 待定事项：七项原待定事项已由用户授权代定，无遗留；**D9-2…D9-7 属顺着两条定案推出的技术选择，标「提议」而非「已定」，实施前若与用户理解不符须重开**（D9-1 页级粒度、D9-5 页级引用已经用户明确确认）。
- 未改 `docs/plans/成员任务进度表.md`：ADR 是决策记录，不改变 17 个任务的状态，无任务行需变更。§3.3「已完成」四条件对本轮不适用（本轮不宣称任何任务完成）。
- 下一步：按 D4（格式骨架）→ D6（预检 + 进程池 + 心跳 + 认领单独提交）→ D5（`pdfplumber` 耗时实测 + ≥10 文档 golden set，ADR-0004 要求）→ D2/D3（locator + 表格切分）→ 迁移 `0014` → 基准重建 的顺序实现。**D9 视觉通道排在 D2/D3 之后**（它复用 `region` locator）；`vision_enabled` 默认关闭，上线前必须先用真实扫描件测三信号阈值与识别质量，否则等于把未验证的判据直接放生产。

## 本轮：前端收尾（N10 成员管理 + N11 e2e/axe，2026-11-02，成员5 域 + 共用 `lib/api`/`globals.css`）

- 任务：`docs/plans/PyKnora_前端DAG执行路径.md` 的两个遗留节点 **N10**（成员管理）与 **N11**（e2e + axe），以及它们暴露出的三个跨feature缺陷。**17 个后端任务状态不变**（仍 `已完成/100%`，任务 13/14 维持 `进行中/75%`）——前端是独立工作流。
- 改动文件：
  - **N10 新增 15 个**：`web/features/members/{api,queryKeys,roles,errors,hooks}.ts` + `{RoleSelect,MemberAddDialog,MemberTable,OwnerTransferDialog}.tsx` + `web/app/(app)/settings/members/page.tsx` + `web/tests/unit/features/members/{fixtures,api.test,errors.test,member-hooks.test,member-components.test}.ts(x)`。
  - **N11 新增 18 个**：`web/tests/e2e/{auth,kbs,documents,query,chat,a11y}.spec.ts`、`fixtures/power-module.txt`、`support/{paths,env,state,api,backend-env,worker,global-setup,global-teardown,seed,ui,fake-chat-upstream.mjs}`、`ACCEPTANCE.md`；**重写** `web/playwright.config.ts`（3 条 `webServer` + globalSetup/Teardown，删 mobile 项目）。
  - **修 3 处产品代码**：`web/lib/api/client.ts`（token 三档语义 + `getToken` 导入）、`web/app/globals.css`（`--color-fg-subtle`）、`web/features/kbs/KbRowActions.tsx`（aria-label）；**加 2 条契约用例** `web/tests/contract/api-client.test.ts`。
  - **文档**：`web/README.md` §6 重写为诚实缺口 + 新增 §6.1「已修的历史缺陷」；`docs/plans/PyKnora_前端DAG执行路径.md` §7.1（N10/N11 转 ✅）+ 新增 §7.4；本文件；`docs/plans/成员任务进度表.md` v1.26。
- 已执行命令与真实结果：
  - `cd web && pnpm typecheck` → `tsc --noEmit` 无诊断输出，**TYPECHECK_EXIT=0**。
  - `pnpm exec vitest run` → **Test Files 32 passed (32) / Tests 250 passed (250)**，`VITEST_EXIT=0`（248 → 250：新增客户端 token 契约 2 条）。
  - `pnpm build` → **BUILD_EXIT=0**，9 条路由（新增 `/settings/members`）。
  - `pnpm exec playwright test` → **12 passed (1.0m)**，`E2E_FULL_EXIT=0`（登录跳转 / 密码错误 alert / 建库对话框 / 上传 202→轮询 succeeded / 检索排名表 / 问答流式收敛 / 5 路由 axe）。
  - `pnpm exec playwright test tests/e2e/a11y.spec.ts` → **5 passed (43.9s)**，`A11Y_EXIT=0`，5 条路由均 `critical=0 serious=0 moderate=0 minor=0`。
  - e2e 前置实测：`docker start pyknora-pg-p0`（宿主 5434）、`pnpm exec playwright install chromium`（build 1248）。
- 三个真 bug 的证据与修法：
  1. **业务请求不带 `Authorization`（P0）**：e2e trace 显示 `/auth/me` 带 authorization 200、`/knowledge-bases` 不带 → 401，`/kbs` 渲染成「登录状态已失效」。根因 `lib/api/client.ts` 只在显式传 `token` 时加头，而 `features/{kbs,documents}/api.ts` 全没传；MSW 不校验鉴权，所以 212 条单测全绿也发现不了。修法：**省略 `token` = 用 `lib/auth/token-store` 的会话令牌；显式 `null` = 强制匿名；字符串 = 覆盖**（不能用 `??`，否则退出登录后仍带旧令牌）。
  2. **对比度不达 AA（P0）**：`--color-fg-subtle: #7d8f95` 在白底 **3.36:1**、canvas 底 **3.13:1**，而它被用于 10–12px 正文 11 处；axe `color-contrast` 把 5 个路由全判 serious。压暗到 `#5f7175` → **5.12:1 / 4.76:1**。
  3. **无障碍名与可见文字不符（P1）**：改名按钮可见「改名」但 `aria-label="重命名「X」"` → axe `label-content-name-mismatch` serious×2。改为无障碍名以可见词开头。
- 未验证 / 已知缺口：① 移动端窄屏（原 mobile 项目已删）；② 聊天「回答已渲染」态与错误态/空态/对话框态的 axe 未扫（流式期间 DOM 每 60ms 变，不可复现）；③ 键盘可达性与读屏器实测未做；④ 深色模式未做；⑤ CI 缺 `playwright install` 步骤；⑥ 真实 LLM 未联调（e2e 用假 OpenAI SSE）；⑦ 客户端 token 兜底只在 e2e 与契约测验过，多标签页 `storage` 事件登出未验。
- 未修（后端待办）：**P1-1 写后读窗口**——建库 201 后立刻上传偶发 `404 knowledge base not found`（约 8 次 1 次，库里该行确实存在）；推断 `api/deps.py` 的 `get_session` yield 依赖在响应发出后才 commit。e2e 侧退避重试 3 次并打 `[e2e][warn]` 留痕，**没有掩盖**；修复涉及成员1/2 域的 session 生命周期，需单独一轮。
- 下一步：① 决定是否修 P1-1（`get_session` 改为在 `yield` 返回前 commit，或把写路径显式 `await session.commit()`）；② 若要发布，补 `playwright install` 到 CI；③ 补聊天流式态与错误态的 axe。

## 本轮：Day03 DAG 执行与真实基线评测（2026-10-30，Lead 记录）

- 任务：计划 `docs/plans/PyKnora_Day03_可信RAG闭环实施计划.md` §8 的 **23 个节点**（T0.1–T6.3，其中 T3.1 与 T4.0 合并为单写者节点 TC），按 **6 个波次**执行：G1（4 路：语料清点 / runner / 契约与开关 / `evidence.py`）、G2（3 路：建 KB 由 Lead 打 HTTP / 候选配置 / `answer.py` 分流）、G3（2 路：评测集 / `chat.py` 接门控）、G4（文档一文件一写者，`docs/Day03/01`–`08`）、G5（回归）、G6（演示与收口）。起始 HEAD = `4e16d652cea0f730244571dd92c16a1a112f0be4`（即两轮评测的 `app_commit`，无 drift）。对应进度表任务 13/14 仍 `进行中/75%`（成员3 域），任务 4/6/7 不改。
- 改动文件（按目录归并；`git status` 实测 14 个已改 + 若干未跟踪）：
  - `eval/` **新增 11 个**工装/配置文件：`corpus.py`、`build_eval_set.py`、`run_eval.py`、`make_configs.py`、`report.py`、`selfcheck.py`、`README.md`、`baseline_config.yaml`、`candidate_config.yaml`、`corpus_manifest.json`、`eval_set_v0.1.jsonl`；`eval/results/` 新增 8 份真实产物（两轮各 `raw.jsonl`/`summary.json`/`report.md` + `compare-20261030.md` + `README.md`）。
  - `src/` **改 11 个文件**（任务书估 12，实测 11）：新增 `services/rag/evidence.py`、`services/rag/budget.py`；改 `api/main.py`（embedder 预热）、`api/v1/chat.py`、`api/v1/query.py`、`core/settings.py`、`schemas/chat.py`、`schemas/query.py`、`services/rag/answer.py`、`services/retrieval/rrf.py`、`services/retrieval/search.py`（策略开关 + `_resolve_candidate_k()`）。
  - `tests/` **新增 11 个**：unit 9（`test_eval_harness`、`test_eval_set_check`、`test_evidence`、`test_evidence_calibration`、`test_rag_answer_gate`、`test_rag_budget`、`test_rag_chat_events`、`test_report`、`test_retrieval_strategy`）+ api 2（`test_api_refusal.py` 7 用例、`test_api_security_safe.py` 3 用例）；**改 2 个**：`tests/api/test_api_chat.py`、`tests/unit/test_retrieval_search.py`。
  - `docs/Day03/` **8 份**（`01-优化选型` … `08-AI协作与问题清单`，08 原为骨架、收口时回填）；两份计划：`docs/plans/PyKnora_Day03_可信RAG闭环实施计划.md`、`docs/plans/Day03_DAG执行路径.md`。
  - 仓库根：`.gitignore` **+7 行**，忽略 `eval/results/`、`eval/reports/`（运行产物可重跑再生成；入库的是评测集、两份 config、runner）。
- 已执行命令与真实结果：
  - `uv run --frozen ruff check src tests scripts eval` → `All checks passed!`；`ruff format --check src tests scripts eval` → `147 files already formatted`（exit 0）。
  - 基线：`uv run --frozen python eval/run_eval.py --config eval/baseline_config.yaml --out eval/results/baseline-20261030 --base-url http://127.0.0.1:8030 --repeats 1` → 20 条全 200（deny 两条 404），Recall@5 1.000 / Precision@5 0.200 / 引用准确率 0.356 / 无证据拒答 0.667 / 权限拦截 1.000 / 检索 P95 375.140 ms / 问答 P95 5586.000 ms（另有 TTFT P95 5078.340 ms）。
  - 候选：同形命令（`candidate_config.yaml` → `eval/results/candidate-20261030`）→ 引用准确率 0.318 / 无证据拒答 0.333 / 检索 P95 211.750 ms / 问答 P95 5831.230 ms（TTFT 5125.850 ms），其余同基线。
  - 对比：`uv run --frozen python eval/report.py eval/results/baseline-20261030 eval/results/candidate-20261030 --out eval/results/compare-20261030.md` → 「指标格子 14 / 失败台账 32 条 / 退化 4 条 / 复算不一致 0 项」，可比性 19 项 FAIL 0 / WARN 0 / SKIP 0，两份 config 逐字段 diff 唯一差异 = `retrieval_strategy`。
  - 自检：`uv run --frozen python eval/selfcheck.py` → **PASS 25 / FAIL 1 / SKIP 0（26 项），退出码 1**；唯一 FAIL = `tests/test_citations.py` 无真实落点（CIT-01/02/03）。
  - **全量测试（Lead 串行补跑，2026-10-30 22:26–22:34）**：`uv run --frozen pytest -o addopts="" -q` → **737 passed, 5 xfailed / exit 0**（499.76s；`PYKNORA_DATABASE_URL=postgresql+asyncpg://postgres:dev@127.0.0.1:5434/pyknora`、`PYKNORA_EMBEDDING_BACKEND=hashing`；跑前已停 Worker，避免 `TRUNCATE` 与 `SKIP LOCKED` 抢锁）；`ruff check src tests scripts eval` → `All checks passed!`；`ruff format --check src tests scripts eval` → `148 files already formatted`。5 个 `xfailed` 全部来自 `tests/unit/test_citations.py` 的 `xfail(strict=True)`，即 CIT-01（chunk_id 层）/CIT-02/CIT-03 三条尚缺实现的校验（见该文件 docstring 与本节「未验证」第 ⑦ 条）。
- 结论：**候选不优于基线，本轮不采用**。四条判据见 `docs/Day03/04-优化前后对比.md` §5.4：引用准确率 -0.037（16/45→14/44）、无证据拒答 -0.333（误拒 5→4、漏拒 1→2）、问答 P95 +245.2 ms（两轮 TTFT 都 5.0–5.1 s，属上游 LLM 抖动）、检索 P95 -163.4 ms 不可比（候选池更大却快 43%，是噪声而非收益）。
- 未验证 / 已知缺口：① **已补跑（2026-10-30 22:34）**：全量 `pytest` → 737 passed / 5 xfailed / exit 0，`tests/api` 与 `tests/integration` 均在其内，见上一条；仍未验证的是 `tests/api` 那三个新/改文件在**真实 LLM**（非桩）下的行为——全量跑的是 `PYKNORA_EMBEDDING_BACKEND=hashing`，chat 用例注入替身；② `repeats=1` 无波动信息，n=20 的 P95 只是 20 个观测里的第 19 慢，**不能当性能基线**；③ 模型名两处并存未裁决（config 写 `deeseek-v4.1-flash`、跑批环境变量写 `deepseek-v4.1-flash`；未读 `.env`）；④ HF 回源耗时两个口径未统一（代码注释 121.5 s、任务书 134 s）；⑤ `.env` 未读未改，运行配置靠命令行覆盖（`eval/results/README.md:23`），用户是否已填 chat 三件套未验证；⑥ `RunStatus` 计划七态仍只落五态（缺 `RUNNING`/`CANCELLED`，Q1 待裁决）；⑦ `tests/unit/test_citations.py` 已建（7 例：4 passed + 3 `xfail(strict=True)`），但 CIT-01 只到「序号越界」层、chunk_id 级校验与 CIT-02/03 的「拒」仍未实现——自检 FAIL 由「文件缺失」降级为「实现缺口」；⑧ `eval/results/` 已进 `.gitignore`，换机器需重跑才能复现数字。
- 下一步：① ~~Lead 串行跑全量 `pytest`~~（已补跑，737 passed / 5 xfailed / exit 0）；② 裁决 Q1（`RunStatus` 七态是否补 `RUNNING`/`CANCELLED`）与 Q3（指标七还是八），并补计划措辞；③ 实现 `test_citations.py` 里三条 `xfail` 对应的校验（chunk_id 级 `UNKNOWN_CITATION`、引用二次授权、版本失效判定），实现后 `strict=True` 会 XPASS 逼着删标记；④ 若重跑，先统一模型名拼写（会改两份 config 的 sha256 与 compare 的配置一致性，需重算）；⑤ 不建议为「让候选赢」再调参——`docs/Day03/01-优化选型.md` 已证检索侧不是瓶颈，方向是 D1 引用裁剪 + D2 门控精度。

## 本轮：Day03 可信 RAG 闭环——评测工装、真实语料、证据门控与运行预算落盘（2026-10-30，成员2/3 域 + 共用 `settings.py`/`schemas`；Lead 记录）

- 任务：Day03 波次的工装、语料与代码落盘（T0.2–T1.1 评测工装/语料/评测集、T3.1+T4.0 契约与策略开关、T4.1–T4.3 证据门控、T5.1 运行预算）；对应进度表任务 **7 / 13 / 14**（成员3 域）与 **4 / 6**（成员2 域的语料与索引验证）。本轮把**任务 13 / 14 由 `已完成/100%` 回调为 `进行中/75%`**（交付物被改、验收未跑，依据 AGENTS.md §3.3 的「已完成」四条件），任务 7 / 4 / 6 不改状态（理由见 `docs/plans/成员任务进度表.md` §3 的 v1.23 口径说明）。起始 HEAD `4e16d65`；起始工作树 = 本波次未提交改动；未改 `.env` / `pyproject.toml` / `migrations/` / `AGENTS.md`。
- 中文 FTS 在前轮已修（`migrations/0007_cjk_bigram_fts.sql` + `pyknora_cjk_bigram()` / `pyknora_cjk_tsquery()`），本轮未动。
- 评测工装（`eval/`，均未跟踪）：`corpus.py` 18671B、`build_eval_set.py` 47900B、`run_eval.py` 79023B、`make_configs.py` 21297B、`report.py` 72321B、`baseline_config.yaml` 1494B、`candidate_config.yaml` 1558B、`README.md` 12106B、`corpus_manifest.json` 2402B、`eval_set_v0.1.jsonl` 11400B；**`eval/reports/` 为空**——基线、候选都还没有跑出 `raw.jsonl` 或任何指标。
- 真实语料与评测集（真库只读实测）：A 区 KB `1556633e-fff3-4a27-8731-adc00d2ff5eb`（workspace `d2c28dec-61ca-4799-82c2-e3ba4b7e037e`）3 篇文档，`v_active_chunks` **98** 片 = 基本财务管理制度.pdf **61** + 员工手册v2.docx **35** + 团建与年会通知.md **2**；`chunks` 基表（含退休版本）**133** 行；三篇的 `active_index_version` 分别为 1 / 1 / **2**（员工手册 v1→v2 已切到 v2）。B 区 deny KB `ca1bbf7e-afa6-41c6-94de-3e27ac5fe414`（workspace `433ef35b-e2cf-4303-aa0b-88db8d7d6fc7`）仅 `财务内部预算.md` **1** 片。`eval/eval_set_v0.1.jsonl` **20** 条：fact 6 / exact_term 4 / synonym 3 / no_evidence 3 / deny 2 / version 2；18 条指向 A 区 KB，2 条 deny 的 `kb_id` 为 `null`、`deny_kb_id` 指向上述 B 区 KB。
- 契约：`src/pyknora/schemas/query.py:76` `RetrievedChunk.active_document_version_id`、`src/pyknora/schemas/chat.py:29` `Citation.active_document_version_id`；`QueryResponse.retrieval_strategy` 同步新增并由 `api/v1/query.py:61-69` 透出；`src/pyknora/services/retrieval/rrf.py:73-78` 把 `active_document_version_id` 从 `ScoredChunk`/`ActiveChunk` 继续往下传（不传则出参永远是 `None`，"引用来自现行版"只能靠人工比对）。
- 运行状态：`src/pyknora/schemas/chat.py:55` 新增 `RunStatus(StrEnum)` = `COMPLETED` / `ABSTAINED` / `TIMEOUT` / `BUDGET_EXCEEDED` / `FAILED`（**计划 §7.3 的七态里本轮只实现五态**，`RUNNING` 与 `CANCELLED` 未做）；`:90` `DoneEvent` 加 `status`（必填）、`run_id`、`prompt_version`、`verdict`。
- 证据门控与运行预算：新增 `src/pyknora/services/rag/budget.py`（`RunBudget`：`from_settings` / `elapsed` / `remaining` / `timed_out` / `calls_exhausted` / `blocked` / `check` / `begin_retrieval` / `cancel` / `finish` / `stop_message`）与 `src/pyknora/services/rag/evidence.py`（`Verdict` 三态 `SUPPORTED` / `INSUFFICIENT` / `CONFLICTING`，`Evidence`、`EvidenceAssessment`、`assess()`、`split_unsupported_claims()`）。接线在 `src/pyknora/api/v1/chat.py`：`_evidence()`（57-88）剔除 `active_document_version_id is None` 的片段；`run_id` / `RunBudget` / `blocked()`（116-118）；`assessment = assess(...)`（147）；citations 改由 `select_context_chunks(..., assessment=...)` 的上下文生成（152-169）；`stream_answer(..., assessment=assessment)`（232）；`done` 事件带 `verdict`、`invalid_citations`。`src/pyknora/services/rag/answer.py`：`select_context_chunks(..., assessment=None)`（91）、`stream_answer(..., assessment=None)`（239）、`INSUFFICIENT` / `CONFLICTING` 直接产出拒答/冲突 payload（256-263），**两种拒答都不调模型**。
- 检索策略开关（A/B 用）：`src/pyknora/services/retrieval/search.py` 新增 `HybridSearchResult.retrieval_strategy` / `candidate_k`、`_CANDIDATE_POOL_FACTOR = 4`、`_resolve_candidate_k()`；`search_hybrid(..., retrieval_strategy=None)` 缺省读 `settings.retrieval_strategy`，未知值 → `INVALID_REQUEST`（`details.allowed` 列 `baseline` / `candidate`）；`baseline` 复用改造前的 `_candidate_limit`，**默认线上行为不变**。`src/pyknora/core/settings.py:24-28` 集中定义 `RETRIEVAL_STRATEGY_BASELINE` / `RETRIEVAL_STRATEGY_CANDIDATE`，`:84-94` 加字段 `retrieval_strategy: Literal["baseline","candidate"]` 与 A/B 口径注释；`search.py` 注释写明 `top_k >= 25` 时两策略候选池都封到 50，**A/B 只能用 `top_k < 25`**。
- 配置追加（`settings.py` 属共用文件，只追加）：`:68` `chat_session_id: str = ""`、`:78` `chat_budget_timeout_s: float = 25.0`、`:82` `chat_max_retrieval_calls: int = 2`。
- 文档：新建 `docs/Day03/` 八份骨架（01–08，5748–10394B；`03-基线评测报告.md` 仍写「骨架（**全部数值待填**）· 尚未跑过任何一轮基线」）、`docs/plans/PyKnora_前端实施计划.md`（Day4 前置）、`docs/plans/Day03_DAG执行路径.md`（23 个任务 T0.1–T6.3 的依赖图、单写者边界与「子 agent 只跑 `tests/unit`」的编排规则）。
- 测试文件：新增 `tests/unit/test_eval_harness.py`（收集 144 条）、`test_eval_set_check.py`（85）、`test_evidence.py`（27）、`test_rag_answer_gate.py`（8）、`test_rag_budget.py`（13）、`test_rag_chat_events.py`（7）；`tests/unit/test_retrieval_strategy.py`、`test_report.py` 与 `tests/api/test_api_refusal.py`、`test_api_security_safe.py` 在本轮后段出现，未纳入下面的执行记录。`tests/api/test_api_chat.py` 已适配新契约（`DoneEvent.status` 变必填、citations 来源变化），**尚未执行**。
- 已执行（真实输出）：`git status --short` → 11 个已改（`.gitignore`、`api/v1/chat.py`、`api/v1/query.py`、`core/settings.py`、`schemas/{chat,query}.py`、`services/rag/answer.py`、`services/retrieval/{rrf,search}.py`、`tests/api/test_api_chat.py`、`tests/unit/test_retrieval_search.py`）+ 未跟踪（`docs/Day03/`、三份计划、`eval/`、`services/rag/{budget,evidence}.py`、新增测试等）；`git diff --stat` → **11 files changed, 1156 insertions(+), 130 deletions(-)**。`uv run --frozen pytest -q --no-header -p no:cacheprovider` 跑 6 个新单测文件（`test_eval_harness` / `test_eval_set_check` / `test_rag_budget` / `test_rag_chat_events` / `test_evidence` / `test_rag_answer_gate`）→ 收集 **284** 条，**283 passed, 1 xfailed in 8.39s**，exit 0。数据库只读：`docker exec pyknora-pg-p0 psql …` 实测上述 98 / 61 / 35 / 2、133、1 与 `active_index_version` 1 / 1 / 2；`eval_set_v0.1.jsonl` 逐行解析 → 20 条且六类计数与上文一致。
- 未验证 / 已知缺口：① `tests/api`（含适配后的 `test_api_chat.py` 与新增 `test_api_refusal.py`、`test_api_security_safe.py`）与 `tests/integration` 一条未跑、全量更未跑（`tests/api` 会 `TRUNCATE`，按本轮纪律只能由 Lead 串行跑）；② 基线/候选评测一轮未跑，`eval/reports/` 空、`docs/Day03/03` 全部待填——**本轮不写任何指标**；③ `RunStatus` 只实现五态，客户端断开（`CANCELLED`）与流内 `RUNNING` 未做；④ `chat_budget_timeout_s=25.0` 是兜底值，**未按计划 §7.3 的 `max(3×问答 P95, 10s)` 校准**（无基线 P95），`chat_max_retrieval_calls=2` 目前只是预留（一次问答只检索一次）；⑤ `.env` 的 chat 三件套待用户填：`chat_base_url` 缺 `/v1`、`chat_model` 拼作 `deeseek-v4.1-flash`、缺 session 头（前两条有 `eval/baseline_config.yaml` 生成时的抄录值 `https://opencode.ai/inference/go`、`deeseek-v4.1-flash` 佐证，session 头来自 Lead 口径；**我未读写 `.env`**，也没有发过真实 chat 请求，是否 4xx / 答不出**未验证**）；⑥ `src/pyknora/services/rag/answer.py:19-22` 顶部 docstring 仍写「`api/v1/chat.py` 目前没有传 `assessment`……门控拒答在线上并没有生效」，与 `chat.py:232` 实况相反（过期注释，属成员3 文件，未改）；⑦ 库里有一对残留的同名 Day03 评测库（A `edfd4093-ce28-46ac-bc58-d48751c55a31` ws `982167da…` 98 片、B `d6887ea9-aa02-4314-96bd-01ab3583f296` ws `6af90160…` 1 片），当前 config 与评测集指向 `…0f6c05` 那对，残留未清理；⑧ `docs/plans/Day03_DAG执行路径.md` §5 仍写「库内语料 0 文档」，成文早于语料入库，口径已过期（该文件不在我的写范围，仅登记）。
- 下一步：① 用户填 `.env` 的 chat 三项（`base_url` 补 `/v1`、模型名、session 头）；② 以 `PYKNORA_RETRIEVAL_STRATEGY=baseline` 起 uvicorn（独立端口）→ `uv run python eval/run_eval.py --dry-run` → `--config eval/baseline_config.yaml --repeats 3`，产出 `raw.jsonl` 20 行与七指标并回填 `docs/Day03/03`；③ 候选轮必须**改服务端环境变量并重启**后再跑（`run_eval.py` 的请求体里没有策略字段，客户端传参不生效）；④ Lead 串行跑 `pytest -q tests/api/test_api_chat.py` 后再跑全量，绿了即按进度表「v1.23 口径说明」把任务 13 / 14 回调 `已完成/100%`。

## 本轮：授权撤权链补两条真库 HTTP 用例（2026-10-30，成员4 域；纠正上一轮的错误结论）

- 背景：上一轮收尾回答里我把「`_load_membership` 仍是 `return True` 占位」和「流式 `degraded` 没有进 SSE」列为两条遗留隐患。**这两条都是我引用了过期状态、没先取证就下的结论**：`load_membership` 早已是真查询（`src/pyknora/services/auth/membership.py:25`，`find_membership` 还 join `principals.status`，提交 `44f9af3`/`011116b`/`b85a663`），`degraded` 也早已搭第一个 `retrieval` 事件发出（`src/pyknora/api/v1/chat.py:113-117`）。误判来源是 `tests/api/conftest.py:55-60` 那段仍在描述「以前 `_load_membership` 恒 True」的历史注释。
- 但复核发现一个**真实的覆盖缺口**（不是缺陷）：撤权链的两条路径此前只有桩级/单元覆盖——`tests/unit/test_auth_a4.py` 把 `load_membership` 换成桩，证明的是 `get_scope` 的分支；「成员关系被删」「`X-Workspace-ID` 指向别人的空间」这两条从没在 **HTTP + 真库** 上跑过。SQL 写错（漏 `user_id`、漏 join `principals.status`）时桩测不出，真库测试才测得出来。
- 改动：`tests/api/test_auth_revocation.py` 新增 2 条——`test_removed_membership_revokes_existing_token`（先断言 200，再 `DELETE FROM workspace_memberships …` 并断言 `rowcount == 1`（防测试数据没删掉导致后面那条 403 变假绿），最后断言 403 + `FORBIDDEN`）与 `test_header_cannot_select_a_foreign_workspace`（自带 header 200，改指 `WORKSPACE_B` 后 403）。
- 不改的行为：`degraded` **不**另立独立 SSE 事件——`docs/tdd/07` §6 的顺序契约是 `retrieval`、`token*`、`done`，加事件类型属破坏性变更；`retrieval.degraded` 已满足「降级必须可见」。
- 已执行：`uv run --frozen pytest -o addopts="" tests/api/test_auth_revocation.py` → **4 passed in 1.11s**；全量 `uv run --frozen pytest` → **377 passed / exit 0**（unit 138 / contract 20 / api 142 / integration 77）；`ruff check` → All checks passed；`ruff format --check` → 1 file already formatted。
- 未验证：`degraded` 在**独立客户端**侧的解析行为没有外部消费者可验；撤权链未覆盖「成员关系被删后立刻重新加回」的恢复路径（停用/恢复有，删/加没有）。

## 本轮：功能检查 P0–P3 收口（2026-10-30，经用户「p0-p3，修完再 review 一遍」授权；成员1/2/4 域 + 新迁移 `0013`）

- 任务：真系统功能检查（`/receiving-code-review`，起真 API + 真 Worker + 桩 LLM，跑 42 + 16 + 16 项黑盒断言）暴露出的 P0/P1/P2 全部收口。**不属于** 17 个任务，不改主看板状态（仍 17/17 `已完成/100%`）。用户同一句里还有两个约束：**评测集与 CI 本轮不做**，所以「阈值定多少 / 要不要 rerank」依旧没做。
- 缺陷 A（P0，坏文件打死 Worker）：坏 PDF 让 `pypdf.errors.PdfStreamError` 从 `services/ingest/parser.py` 逃出来 → `workers/ingest_worker.py` 的 `run_once` 兜底 `except Exception` 记录失败后**又 `raise`** → `db/session.py` 的 `session_scope()` 整个回滚，**失败记录一起被吞掉**：作业永远停在 `pending` / `attempt=0` / `job_attempts` 零行，Worker 每轮重领同一个队头坏作业，队列被堵死。实测 job `18114163-f0ef-4ec3-a96e-3759e53101a6`，12:33 ↔ 12:36 三次重试状态完全不变。
- 缺陷 B（P1，测试把参考数据清空）：`tests/api/conftest.py` 与 `tests/integration/conftest.py` 的 `TABLES` 清场清单都含 `roles` / `role_grants`——这两张是**迁移种下的参考数据**，不是业务数据。api 侧清完会补种（自带 `seed_role_catalog`），**integration 侧不补** → 跑一次全量测试，开发库 `roles=0` → 任何新用户注册都撞 `workspace_memberships_role_fkey`。现象是「注册 409 账号已存在」，与实际原因毫无关系。
- 缺陷 C（P1，注册把外键违约说成账号冲突）：`api/v1/auth.py` 的 `except IntegrityError` 无差别抛 `409 CONFLICT "account already exists"`，上面那条外键违约就被伪装成业务冲突，把排查引向错误方向。
- 缺陷 D（P1，错误体两套）：pydantic 校验失败回 `422 {"detail":[…]}`、未知路由回 `404 {"detail":"Not Found"}`，与其余接口的 `{"error":{code,message,retryable,details},"request_id"}` 完全不是一个形状，且都不带 `request_id`——客户端按统一体解析就会炸。
- 缺陷 E（P2，跨租户检索 200 + 空）：`/query` 用别人的 `kb_id` 返回 `200 {"results":[]}`，而 `/documents/{id}` 同类情形是 404。同一个语义两套答案。
- 缺陷 F（P2，切片跨页只记起始页）：4 页 PDF 实测 22 条切片里有一块横跨第 3、4 页，`chunks.page_no` 只能存一个值 → 记 3。用户从引用跳过去，后半段在第 4 页。
- 缺陷 G（P2，没有文档列表端点）：只能按 `document_id` 查详情，列不出「这个库有哪些文档」。
- 改法 A：解析层把 PDF/DOCX 的库异常统一转 `AppError(ErrorCode.PARSE_FAILED)`（`details` 只留异常**类型名**，不塞消息——可能夹二进制碎片）；`_NO_RETRY` 收进 `PARSE_FAILED`（坏文件是确定性失败，重试没有意义）；`run_once` 的兜底改为**记录失败 + 返回 `status="failed"`，不再 re-raise**（注释写明 re-raise 会被 `session_scope()` 回滚吞掉失败记录）。`_record_failure` 同时分三支：`STALE_ATTEMPT`/`VERSION_SUPERSEDED` → `superseded`；其余不重试码 → `max_attempts=1` 立即终态 `failed`；可重试 → 按配置重试。**坏文件此前会被记成 `superseded`**，审计上看起来像「被新版本替换了」，事实是它从未成功。
- 改法 B：把补种抽到 `tests/role_catalog.py`（数据源 `services/auth/permissions.SEED`，4 角色 49 条授权），api 与 integration 的 conftest 都调它——**清场与补种必须成对**，谁清谁补，不许只清不补。
- 改法 C：按约束名分流。`_DUPLICATE_CONSTRAINTS = {"principals_username_key","principals_email_key"}` 命中才回 409；其余（含上面的外键违约）回 `500 INTERNAL_ERROR` 并把 `details.constraint` 带出来。
- 改法 D：`api/main.py` 新增 `RequestValidationError` 与 `StarletteHTTPException` 两个 handler，统一成同一错误体。**422 只回 `{loc,type,msg}` 三元组、不回显 `input`/`ctx`**——注册密码就在 `input` 里；校验错误最多留 20 条，免得把整个请求体镜像回去。405 保留 405（业务码表里没有对应项，硬拗成 422 反而失真）。
- 改法 E：`/query` 与 `/chat` 挂 `require_kb_access`，别人的 `kb_id` 一律 404；`deps.py` 里「仓库没注入」从 `None.get` 崩 500 改为 `DEPENDENCY_UNAVAILABLE`。`chat` 的依赖在建流**之前**解析，天然满足「流开始前必须失败」。
- 改法 F：新增 `migrations/0013_chunk_page_span.sql` 给 `chunks` 加 `page_end_no`；`splitter.py` 用 `page_at(char_end - 1)` 写结束页（单页切片两者相等、无页码文档两者皆空）；`schemas/chunk.py` 增加「起止页反向即报错」的模型校验；`models/tables.py` / `schemas/query.py` / `rrf.py` / `snapshot.py`（迁移导出列表）/ `chat.py` 的引用全链路带上它。`snapshot.py` 那处尤其不能漏——**漏了就等于迁库/恢复之后引用又只能回指起始页**，正是这一列要修的问题。
- 改法 G：`db/repos/reads.py` 新增 `list_for_kb` / `count_for_kb` / `get_many`（批量取 build，避免列表页 N+1），`api/v1/documents.py` 新增 `GET /knowledge-bases/{kb_id}/documents`（`DOCUMENT_READ` + `limit≤500` / `offset`，`total` 是全量计数，`searchable` 与详情页用同一判据 `build.state == "active"`）。
- 用例：新建 `tests/api/test_api_error_envelope.py`（5 条：422 统一体 + `request_id` 与响应头一致 + **密码不回显** + 404/405 统一体）、`tests/integration/test_ingest_poison.py`（2 条：截断 PDF → `PARSE_FAILED` 且库内 `status='failed'`/`attempt=1`/`job_attempts` 有行 = 失败没被回滚；坏作业之后队列没被堵）、`tests/role_catalog.py`；`tests/api/test_api_query.py` +1（跨区 404 且**检索根本没被打到**）、`test_api_chat.py` +1（跨区 404）、`test_api_documents.py` +5（列表 / 分页 / 软删后为空 / 跨区 404 / 参数边界 422）、`test_auth_api.py` +1（外键违约不再报"账号已存在"）、`tests/unit/test_parser_splitter.py` +3（跨页起止页 / 单页相等 / 无页码皆空 / 反向报错）。
- 已执行：`uv run --frozen ruff check src tests scripts` → `All checks passed!`；`ruff format src tests scripts` → `127 files left unchanged`；全量 `uv run --frozen pytest` → **375 passed / exit 0**（125.18s；unit 138 / contract 20 / api 140 / integration 77）。
- 复检（2026-10-30，真 API + 真 Worker + 桩 LLM，三轮脚本共 74 项）：`func_check` 41/42、`edge_check` 14/16、`chat_check` 15/16、本轮专验 22/23。**四条"不通过"全部是断言过期，不是缺陷**：① `chat` 未配模型 → 503 这条在本轮跑不起来（这次刻意配了桩，`chat` 真的能答）；② 超限上传返回 422 `INVALID_REQUEST` 而不是 413/400（实现选择，本轮不动）；③ 切片分页用例的样本文档只切出 1 条，凑不出两页；④ 跨租户 `chat` 现在回 404——**正是 P2-D1 要的结果**，旧断言还写着"200 + 空 citations"。专验里"作业 attempts 字段"一条读的是 API 响应，API 不暴露该字段；直查库 `ingestion_jobs.attempt=1` / `job_attempts` 1 行，失败确实被记账。**live 证据**：坏 PDF 作业终态 `failed` + `PARSE_FAILED` 且库内 `attempt=1`、`job_attempts` 有 1 行；随后正常文件 `succeeded`（队头没被堵）；真两页 PDF 上传入库后切片 `(page_no, page_end_no) = [(1, 2)]`；`GET …/documents` 返回 `total=3` 且 `limit=1&offset=1` 仍报全量；跨租户 query/chat/documents 三个入口一律 404；422/404 均为 `{"error","request_id"}` 且错误体不含 `input`；跑完全量测试后 `roles=4`/`role_grants=49`、新用户注册 201。
- 未验证：① 跨页修复的 live 验证止于**两页 PDF**（两页刚好跨页），没再用四页以上 PDF 复核多段跨页；② `page_end_no` 对**已入库的历史文档**为空（不回填，要重灌才有），列表/引用侧按 `None` 处理；③ 坏 PDF 用例用的是截断 PDF，真实加密 PDF / 扫描件只走到「未提取到文本」那条分支；④ 测试补种只保证「跑完测试库是完整的」，并不阻止别人手工 `TRUNCATE` 参考表。
- 环境备注：同上一轮——`.env` 的 `PYKNORA_DATABASE_URL` 指向 `127.0.0.1:5433`（另一个 phase0 栈、密码不是 `dev`），测试一律用 `PYKNORA_DATABASE_URL=…:5434/pyknora` 覆盖；**未改代码默认值、未改 `.env`**。
- 下一步：按用户要求对修完的系统**重走一遍 `/receiving-code-review`**（复核这七条是否真的闭合、有没有修出新问题）；评测集与 CI 仍按用户口径不做。

## 本轮：删除只删一层 + 发件箱无人派发 + 流式降级不可见（2026-10-30，经用户「评测和 CI 现在还不做…其他的按照你的建议进行修复」授权；成员1/2/3 域 + 新模块 `services/outbox/`）

- 任务：五成员审查后的四处收口（**不属于** 17 个任务，不改主看板状态）。用户同一句里还有两个约束：**评测集与 CI 本轮不做**——所以「`candidate_k` 到底设多少」「要不要 rerank / 补搜」「阈值怎么定」这三件依赖评测数据的动作**没做**，不是忘了做。
- 缺陷 A（P0，删除只删一层）：`services/ingest/documents.py` 的 `purge_document_chunks` 只 `DELETE FROM chunks` 就返回。每删一篇文档留下一条 `document_blobs`（原始字节，与 `byte_size` 同量级）、一条 `document_versions`、一行退休 `index_builds`，并且 `documents.desired_document_version_id` 指向已删掉的版本。**这些行没有任何读路径能看见**（视图要 join 活动 documents），所以功能测试永远是绿的——只能翻库发现，磁盘只增不减。
- 缺陷 B（P0，删库不删内容）：`services/kb/service.py` 的 `delete_knowledge_base` 只软删 KB 那一行。`documents` 对 `knowledge_bases` 是 `ON DELETE RESTRICT`、KB 自己也是软删，于是文档、chunk、向量全留。上一轮 S1（`f4819a1`）只让视图跳过已删 KB——**检索不再命中，但 HNSW 索引与磁盘照旧占**，且这些行归属的库已经查不到。
- 缺陷 C（P1，事件从未派发）：`0001_init.sql:207-209` 写明「与业务同事务登记，dispatcher 至少一次派发」，`services/ingest/publish.py:275-295` 与 `services/kb/service.py` 也都在事务里往 `outbox` 写行，但**全仓没有一行代码读过 `outbox`**：`dispatched_at` 永远是 NULL，`outbox_pending_idx` 只增不减。
- 缺陷 D（P1，降级对流式调用方不可见）：`/query` 早就有 `degraded`，`/chat` 的 SSE 只把它写进服务端日志——同一次检索，非流式调用方看得见降级、流式调用方看不见。
- 改法 A：`purge_document_chunks` → `purge_document`，返回 `PurgeReport(chunks, blobs, versions)`（**分三类计数**，否则「删了 0 行」看不出漏在哪一层）。删除顺序**由外键决定、不可换**：`chunks → document_blobs → document_versions`（`document_blobs.document_version_id`、`document_versions.document_id` 都是 `ON DELETE RESTRICT`），`index_builds` 只退休（历史读取要用）。同一事务里把墓碑的三个版本指针置空，且带 `AND deleted_at IS NOT NULL`——作业是异步的，不能靠「调用方一定先删过」来保证前提。`workers/ingest_worker.py` 的 purge 分支改用它，日志按三类分别打。
- 改法 B：`delete_knowledge_base` 新增 `_cascade_delete_documents`——`FOR UPDATE` 取出未删文档 → 同事务 `deleted_at` 置位 + `active_build_id = NULL` + `generation/row_version` +1 → 逐篇 `queue.enqueue(job_type="purge")`。**先断可见性再入队**：反过来会让窗口期内的检索命中已删文档。重复入队安全（purge 幂等），漏入队会让 blob 字节永久留下，所以宁可重复。
- 改法 C：新建 `src/pyknora/services/outbox/dispatcher.py`（`OutboxEvent` / `DispatchReport` / `dispatch_pending(session, *, deliver=None, limit=100)`）：`WHERE dispatched_at IS NULL ORDER BY created_at, event_id FOR UPDATE SKIP LOCKED LIMIT :limit`，逐行调订阅者，**成功才写 `dispatched_at`**。先投递后打标记 = 至少一次（崩溃只会重复投递，消费方须幂等）；反过来会丢事件，那更坏。单行失败不中断整批——毒事件不该卡住后面所有行。默认订阅者 `log_delivery` 只写日志：仓里确实没有第二个订阅者，不假装已经送到了什么。挂载点在 `scripts/run_worker.py` 的 `_tick`，**不放进 `run_once`**——队列空时 `run_once` 提前返回，空闲 Worker 上发布事件会永远等不到派发。
- 改法 D：`src/pyknora/schemas/chat.py` 的 `RetrievalEvent` 加 `degraded: dict[str, str]`，`api/v1/chat.py` 把它搭第一个 `retrieval` 事件发出去——流一旦开始状态码就固定，之后没有任何地方能表达「只召回了一路」。
- 用例：`tests/integration/test_outbox_dispatch.py`（7 条：多轮不重派、订阅者抛异常则保持 pending 且不阻塞同批其余行、payload 是 JSON 字符串也能派、`limit<=0` 空转、已派发的不再选中）；`tests/integration/test_purge_and_kb_delete.py`（5 条：三类行都清零且 build 退休、墓碑指针置空、非墓碑不被改坏、删库级联软删文档并逐篇入队、无残留）；`tests/api/test_api_chat.py` +2（降级时 `retrieval.degraded` 带分支与错误码、正常路径为 `{}`）；`tests/integration/test_rls.py` +1 条 `test_active_chunks_view_join_works_under_rls_and_soft_deleted_kb`——**补掉 S1 遗留的那条"未验证"**：`0012` 给视图加了 `JOIN knowledge_bases`，而 `knowledge_bases` 自己也有 RLS、视图是 `security_invoker`，两者叠加出错时的现象是**非 owner 角色读到空集而不是报错**。该用例走 `pyknora_app`（`session` 是超级用户，测这个等于没测）断两件事：非 owner 在 A 区上下文读得到自己那行、软删 KB 后立刻读不到。**两条都单独做过反证**（在真库上换掉视图定义再跑）：① 去掉 `security_invoker` → 在 B 区上下文读到 A 区行，断言 `跨区读到了 A 区切片——RLS 没生效` 失败；② 去掉 KB join → 软删后仍读到 1 行，断言 `软删知识库后仍读得到切片` 失败。恢复 `0012` 后 7 passed。
- 已执行：`uv run --frozen ruff check src tests scripts` → `All checks passed!`；`ruff format --check src tests scripts` → `124 files already formatted`；`pytest -q -o addopts="" tests/integration/test_outbox_dispatch.py tests/integration/test_purge_and_kb_delete.py` → **12 passed**（26.51s）；`tests/api/test_api_chat.py` → **8 passed**；`tests/integration/test_rls.py` → **7 passed**；全量 `uv run --frozen pytest -q` → **356 passed / exit 0**。
- 未验证：① 「两个 Worker 同时派发」只由 `SKIP LOCKED` 语义保证，没有真起两个进程压测（用例里是两个 session 串行模拟）；② 删库级联没有对「文档很多的库」测过——一次 `FOR UPDATE` 全取、没有分批，大库会长时间持锁；③ `document_blobs` 的字节在库里（`bytea`），删了就是删了，没有回收站。
- 环境备注：本机 `.env` 的 `PYKNORA_DATABASE_URL` 指向 `127.0.0.1:5433`，那里跑的是另一个 phase0 栈的 PG（密码不是 `dev`）→ 直连报 `password authentication failed for user "postgres"`。本轮全部测试用 `PYKNORA_DATABASE_URL=postgresql+asyncpg://postgres:dev@127.0.0.1:5434/pyknora` 覆盖（`pyknora-pg-p0`）。**没有改代码默认值、没有改 `.env`**：AGENTS §2 已写明别人机器上常见 5434 + `pyknora-pg-p0`，先 `docker ps` 再改环境。
- 下一步：剩下的问题不再是「缺功能」而是「缺证据」——没有评测集就没有召回率/排序质量，`candidate_k` 仍是拍的、rerank 没有依据；CI 不做则门禁只在本机。按用户口径这两件留到后面。
- 文档口径同步：`docs/项目结构说明.md` 补了新目录 `services/outbox/`、`purge_document` 与派发器的关键位置、`_tick` 链路，并把测试数从 **236 校正为 356**（40 个 `test_*.py`：unit 134 / contract 20 / api 127 / integration 75）、迁移范围更新到 `0001–0012`。**`AGENTS.md` §2 的「合计 236」仍过期**——按 §9 该文件是流程基线、修改需用户确认，本轮不动，等用户点头后一并改。

## 本轮：知识库软删后检索仍命中——改视图收口（`S1`）（2026-10-30，成员1 域 + 成员2 域）

- 任务：五成员审查发现的 **S1**（严重）。不属于 17 个任务，是「已完成模块」的补洞，不改主看板状态。
- 缺陷（实测可复现）：`knowledge_bases` 的删除是软删（`src/pyknora/db/repos/knowledge_bases.py:112-133` 只写 `deleted_at` / `generation` / `row_version`，文档、`index_builds`、`chunks` 原样留着），而 `v_active_chunks`（`migrations/0001_init.sql:236-269`）只过滤 `documents.deleted_at`，**没有 join `knowledge_bases`**。删库之后：`GET /kb/{id}/documents` 走 repo 的 `deleted_at IS NULL` → 空（看起来删干净了），检索走视图 → 切片照旧返回。同一个库在两处有不同答案。
- 改法：新增 `migrations/0012_v_active_chunks_skip_deleted_kb.sql`——`CREATE OR REPLACE VIEW` 加 `JOIN knowledge_bases k ON (workspace_id, kb_id)` + `k.deleted_at IS NULL`，并显式带 `WITH (security_invoker = true)`；`src/pyknora/db/repos/reads.py:207-247` 的 `list_chunks_by_build`（I1 的受控例外，读 `chunks` 不读视图）补同一条谓词。
- 为什么改视图：视图是 I1 的唯一真相，四个读入口（`list_active_chunks` / `get_chunks` / `search_similar` / `search_keyword`）全打它，在服务层补过滤漏一处就是一处旁路。`CREATE OR REPLACE` 只换定义、不搬数据、无窗口期，回滚也只换回定义。不改 `0001`（I4）。
- 为什么 inner join 安全：`documents_kb_fk`（`migrations/0002_knowledge_bases.sql:40-44`，复合外键 `(workspace_id, kb_id)` `ON DELETE RESTRICT`）保证每个 document 都挂在真实存在的 KB 上，inner join 不会因「KB 行不存在」误删可见行。
- 为什么顺带写上 `security_invoker`：`0003_identity_acl.sql:146-152` 是在视图建好之后才 `ALTER VIEW ... SET` 的。REPLACE 时显式带上该选项，让「全新库顺序重放」与「开发库重复应用」得到同一结果；否则将来若有人把 0012 挪到 0003 之前，视图会退回 security definer 并绕过底下各表的 RLS。
- 测试：`tests/integration/test_read_contracts.py` +1 条 `test_soft_deleted_kb_hides_chunks_everywhere`（软删前三个入口都有数据；软删后活动视图两入口 + 历史两个 build 全 `[]`）。**两处改动各自单独反证过**：① 视图换回 0001 旧定义 → 用例在「活动视图」断言处 fail，报「Left contains 2 more items」；② 视图已修好、但 `list_chunks_by_build` 去掉 KB join → 用例在「历史读取」断言处 fail。
- 已执行：`uv run --frozen ruff check src tests scripts` → All checks passed；`ruff format --check` → 120 files already formatted；`pytest -q -o addopts="" tests/integration/test_read_contracts.py` → **8 passed**（+1）；全量 `uv run --frozen pytest -q -o addopts=""` → **341 passed / exit 0**（134.98s，`PYKNORA_DATABASE_URL=…:5434/pyknora`）。
- 未验证：0012 只在 `pyknora-pg-p0`（宿主 5434）上应用过，全新库从 0001 顺序重放到 0012 未重跑（`scripts/migrate.py` 按文件名顺序自动带上，与 0009/0011 同一条路径，但没实测）；`security_invoker` 在非 superuser 角色下叠加 RLS 的行为未单独验证（`tests/integration/test_rls.py` 全绿，但那是既有用例）。**（2026-10-30 补记：后半条已闭环——新增 `test_active_chunks_view_join_works_under_rls_and_soft_deleted_kb` 走 `pyknora_app` 验证「KB join + security_invoker + RLS」三者叠加，并做过两条反证，见上方 2026-10-30 那一轮。）**
- 下一步：审查三批修复里的 S3a/S3b（`snapshot.py` bytea 与成员关系补导）已随批次 1 提交 `849e174`；剩 S4 的 HTTP 侧用例与「批次 3」之外的收尾项。

## 本轮：角色目录写入改平台闸（`A1`）——封掉跨租户改写全局角色矩阵（2026-10-30，经用户「1.A1」裁决；成员4 域）

- 任务：五成员审查发现的 **S2**（严重）。不属于 17 个任务，是「已完成模块」的补洞，不改主看板状态。
- 缺陷：`roles` / `role_grants` 是**全局表**（`migrations/0003_identity_acl.sql:65-76` 无 `workspace_id`，`roles.role_key` 全局 UNIQUE），但三条写端点只要求工作区级 `workspace.manage_roles`。任一租户的 owner/admin 可改写**所有租户**共用的矩阵：删掉 `member` 的 `document.read` 是跨租户 DoS，把 `workspace.manage_members` 授给 `guest` 是扩大越权面。三条端点是 `PUT /workspaces/{id}/roles/{key}/permissions`、`POST /workspaces/{id}/roles`、`DELETE /workspaces/{id}/roles/{key}`。
- 改法（A1）：`src/pyknora/services/auth/permissions.py` 新增 `PLATFORM_MANAGE_ROLES = "platform.manage_roles"` 与 `PLATFORM_PERMISSIONS`，`is_known_permission` 认它但**不进** `ALL_PERMISSIONS`（否则 `SEED["owner"] == ALL_PERMISSIONS` 会把平台权限发给每个租户的 owner）；三条端点改挂 `require_permission(PLATFORM_MANAGE_ROLES)`。该权限点没有任何角色持有 → 租户侧一律 403 ⇒ 这是「关掉」不是「暂缓」。
- 为什么是 A1 而不是 A2/B：A2（删路由）会丢掉「角色是数据」那条链已建好的目录管理能力；B（给 `roles` 加 `workspace_id`）要重建 9 处引用的主键与外键，且「角色模板」与「角色实例」是两件事，属更大设计。先关再开比先开再补便宜。
- 代价（明写）：三条端点内部的参数校验（角色名格式 / `owner` 保护 / 未知权限点）随闸门一起封存，HTTP 侧不可达，已在测试文件注明「等平台后台需要时再以平台身份开放」。
- 用例：`tests/api/test_api_role_grants.py` 从 14 条改为 20 条——12 条（4 角色 × 3 端点）全 403 且**断言库里一点没变**（只断状态码会被「依赖抛错前已写库」遮掩）；`test_catalog_change_takes_effect_on_next_request` 改由 `replace_role_grants` 造现场，保留原「权限唯一真相是那张表」的核心断言；4 条 repo 级用例覆盖目录 CRUD / 新角色可指派 / 外键 `RESTRICT` 拦删除。造角色全部幂等（`roles` 不在 `clean_db` 清场清单里，清了会连内置角色一起没）。
- `tests/unit/test_permissions.py` +2 条：`platform.*` 与 `ALL_PERMISSIONS` 不相交、**没有任何角色持有平台权限**（哪天有人加进 `SEED`/`0006` 这条会红，届时必须同时决定「哪个平台角色持有它」）。
- 已执行：`uv run --frozen ruff check src tests scripts` → All checks passed；`uv run --frozen ruff format --check` → 全绿；`tests/api/test_api_role_grants.py` 连跑两轮 → **20 passed / 20 passed**（幂等性实跑）；全量 `uv run --frozen pytest -q -o addopts=""` → **340 passed / exit 0**（`PYKNORA_DATABASE_URL=…:5434/pyknora`）。
- 未验证：没有平台管理员身份 ⇒「平台侧真的能改」这条路径**没有 HTTP 用例**，只验证了「租户侧改不了」。S1（`v_active_chunks` 未过滤已删 KB）仍待批次 3。
- 环境备注：本轮全量首跑出现 175 个 integration `ERROR`，原因是 Docker daemon 又停了（本会话第 3 次），重启 `Docker Desktop` + `docker start pyknora-pg-p0` 后复跑即 **exit 0**——**不是代码问题**，别照着 ERROR 去查代码。
- 下一步：批次 3 = S1 改视图（`v_active_chunks` 加 `knowledge_bases.deleted_at IS NULL` 谓词，`CREATE OR REPLACE` 幂等）。

## 本轮：两条转出端外键补索引（2026-10-30，经小组长「我允许你修改别人的目录」授权；成员4 域 + 新迁移）

- 任务：审查清单最后一条 #5「两条外键无索引」。`workspaces.owner_principal_id`（`ON DELETE RESTRICT` → `principals`）与 `workspace_memberships.role`（`ON UPDATE CASCADE ON DELETE RESTRICT` → `roles`）都是外键的 **referencing side**（转出端）。PG 只为被引用端保证唯一，**从不为转出端自动建索引**；实测 `pg_indexes` 确认这两列都不在任何现有索引的前导列里（`workspaces` 只有 `workspaces_pkey`；`workspace_memberships` 只有 `_pkey` / `_user_idx` / `(workspace_id, user_id)`）。
- 代价落在三条路径上：① 删账号——`RESTRICT` 检查要按 `owner_principal_id` 反查 `workspaces`；② 改/删角色——`ON UPDATE CASCADE` / `ON DELETE RESTRICT` 要按 `role` 反查成员表；③ 读侧 `WHERE owner_principal_id = $1`（我拥有哪些工作区）与 `WHERE workspace_id = $1 AND role = 'admin'`（这个库有几个管理员）的 role 谓词。
- 改法：新增 `migrations/0010_fk_indexes.sql`（0009 已被 `ingestion_jobs` 索引占用），两条单列索引 `workspaces_owner_principal_idx (owner_principal_id)`、`workspace_memberships_role_idx (role)`，`IF NOT EXISTS` 幂等、不写 `DROP`（I4：迁移只追加）；不用 `CONCURRENTLY`，与 0009 同一取舍（迁移单事务执行、建索引时表为空）。
- 为什么分两条不是一条复合索引：前缀不同（一条以 `owner_principal_id` 打头，一条以 `role` 打头），一条复合索引无法同时服务；且 `role` 基数是个位数，索引很小。
- 已执行：`Get-Content -Raw | docker exec -i pyknora-pg-p0 psql -U postgres -d pyknora -v ON_ERROR_STOP=1 -f -` → `CREATE INDEX` ×2、exit 0；随后 `pg_indexes` 查回 `workspaces_owner_principal_idx`、`workspace_memberships_role_idx`。
- 新增 `tests/integration/test_fk_indexes.py`（4 条 → **4 passed**）：守两件事——索引存在且**前导列就是外键列**（前导列不对等于没有）、被索引的外键约束仍然存在（约束没了就提示把迁移和用例一起清掉）。
- **守卫做过反证，否则等于没测**：`DROP INDEX workspaces_owner_principal_idx` → 红（`实际是 None`）；把 `workspace_memberships_role_idx` 建成 `(user_id)` → 红（`实际是 'user_id'`）；两次恢复后复跑 **4 passed**。
- 未验证：**没有 `EXPLAIN` 前后对比**——本机 `workspaces` / `workspace_memberships` 均 0 行（集成测试每例 `TRUNCATE`），小表上规划器无论如何都走 seq scan，写"提速多少"就是编数据。本迁移的依据是"外键检查必然扫转出端"这一结构性事实；等有真实数据再补 `EXPLAIN (ANALYZE)`。
- 本机 `scripts/migrate.py` 仍不能跑（docker 从 Python 子进程被拒，已知环境边界，见前文），故 0010 用 pwsh 管道手动应用；换台机器上 `uv run python scripts/migrate.py` 会按文件名顺序自动带上 0010。
- 已执行（全量）：`pytest -q --junitxml` → **328 tests / exit 0**；`ruff check src tests scripts` → All checks passed；`ruff format --check` → 120 files already formatted。

## 本轮：候选池宽于返回数——`candidate_k` 默认 `top_k * 2`（2026-10-30，成员3 域）

- 任务：审查清单第 3 条「`candidate_k` 从不传」。原状：`search_hybrid` 有 `candidate_k` 参数，但**没有任何调用方传它**，于是 `candidate_limit = top_k`——两路各只取 `top_k` 条，融合池一共 `2 * top_k` 条。
- **为什么这是缺陷而不是调参**：RRF 只能重排它看得到的候选。某片段在一路排第 `top_k + 1`、另一路完全没命中时，`candidate_k == top_k` 的配置下它永远浮不上来——而「两路互补」正是混合检索相对单路唯一的价值。默认配置下的融合不是"更保守"，而是**基本白做**：只是把两份长度相同的列表交错一下。
- 改法（`services/retrieval/search.py` 新增 `_candidate_limit()`，把"取多少候选"收敛到一处）：默认 `min(top_k * 2, MAX_TOP_K)`；显式传 `candidate_k` 时仍校验 `<= MAX_TOP_K`，并新增 `candidate_k < top_k → INVALID_REQUEST`（候选池比返回数窄是自相矛盾，且要在调模型/打库之前拒掉）。封顶仍走 `MAX_TOP_K`，否则 `top_k=50` 会算出 100 而越界。
- 倍数 2 只保证"结构上可行"，**具体取值要评测集才能定**——本轮没有评测集，不声称这是最优值。
- 连带修 `tests/api/test_api_query.py` 的夹具：它把 `limit == 3` 写死（`top_k=3` 时恰好等于旧默认），现在两路各取 6 条候选，断言改为 6 并写明原因。
- 新增用例 3 条：`test_hybrid_default_candidate_pool_is_wider_than_top_k`（`top_k=3` → 两路各 6）、`test_hybrid_default_candidate_pool_never_exceeds_max_top_k`（`top_k=MAX_TOP_K` → 仍 50）、`test_hybrid_rejects_candidate_k_below_top_k`（`top_k=10, candidate_k=5` → 422 且没调模型）。
- 已执行：`ruff check` → All checks passed；`ruff format --check` → 119 files already formatted；定向三文件 → 34 passed；全量 → 见下。
- 未验证：默认倍数 2 是否真的提升召回（需评测集跑 Recall@k / nDCG 对比 1x / 2x / 4x，本轮明确不做）；`top_k=50` 时因封顶不再放大，该边界下融合收益仍等于旧行为。

## 本轮：已完成模块补洞批次 1 — JWT 头部类型 / 迁移 SQL 的 bytea / 快照补导成员关系（2026-10-30，经用户「1.A1 2.你怎么说」授权）

- 任务：五成员审查后的修复清单，批次 1（S4 + S3a + S3b）。三条都是「代码跑得通但边界没守住」，不是新功能。
- **S4 `services/auth/jwt.py:104-109`**：给 `header`/`payload` 加 `isinstance(dict)` 类型闸。原状：`[]`、`"x"`、`123` 都能被 `json.loads` 解析成功，随后 `header.get(...)` 抛 `AttributeError`（实测 `'list' object has no attribute 'get'`）、`"exp" not in 123` 抛 `TypeError`；两者都逃出 `decode_token`，被通用异常处理变成 **500 + `retryable=true`**——客户端伪造的令牌被算成服务端故障，违反 I8。现在统一 401 `malformed token`。
- **S3a `services/migration/snapshot.py` 的 `render_sql`**：新增 `_BYTEA_COLUMNS = {("document_blobs","content")}` 与 `_sql_value(table, column, value)`，二进制列渲染成 `decode('<base64>', 'base64')`。原状：`_sql_literal` 把 base64 字符串渲染成普通字面量插进 bytea 列，PG 按 bytea 的 escape 格式当**字面文本**存下去——不报错、行数也对，但 blob 长度是原文的 4/3。旁证：JSON 重放路径 `_decode_rows` 一直有 `base64.b64decode`，SQL 路径没有，**两条路径对同一份快照给出不同结果**。`_row_to_json`（编码）/`_decode_rows`（解码）/`_sql_value`（渲染）三处现在对齐。
- **S3b 快照补导 `workspace_memberships`**：`TABLES` 增加该表（列 `membership_id, workspace_id, user_id, role, created_at`），`REPLAY_ORDER` 放在 `workspaces` 之后、`knowledge_bases` 之前；`principal_ids` 并入成员 `user_id`（该列是真外键，只导 principals 里 `created_by` 那批人会漏成员）。原状：八张表里没有成员关系，快照搬到新库后 `load_membership` 查不到人 → `get_scope` fail-closed → **所有人 403**，而 `verify_metadata` 只比它自己导出的表，照样输出「全部校验项一致」——**校验的是自己导出的子集，缺陷天然不可见**。
- `verify_metadata` 新增三项：① `workspace_memberships` 成员集合 `(user_id, role)` 一致（不比对 `membership_id`，两边都是 `gen_random_uuid` 必然不同）；② `document_versions` 行数一致；③ `document_blobs` **行数 + `octet_length` 字节总数**一致——这一条是 S3a 的探测手段：base64 文本写进 bytea 不报错、行数也一样，只有字节总数会露馅。
- 新增用例 4 条：`tests/unit/test_auth_a4.py::test_non_dict_jwt_header_is_401_not_500`（用**有效签名**构造，保证走到头部形状检查而非签名检查）、`tests/unit/test_migration_snapshot.py::test_workspace_memberships_are_exported_and_replayed`、`::test_sql_value_decodes_blob_content_from_base64`、`::test_render_sql_wraps_blob_base64_in_decode`（把合法的 `decode(...)` 去掉后断言 base64 不再出现在别处，避免负向断言被自己的输出命中）。`_minimal_tables()` 也补了 memberships 行。
- **四条用例都做过反证**（否则等于没测）：把三处修复临时退回后复跑 → 红在 `assert 'workspace_memberships' in ['principals','workspaces',...]`、`assert "'QUJD'" == "decode('QUJD','base64')"`、`assert decode('AAFiaW5...','base64') in <sql>`、`AttributeError: 'list' object has no attribute 'get'`（正是线上 500 的那个异常）。恢复后 **24 passed**。
- 已执行：`uv run --frozen ruff check src tests scripts` → All checks passed；`ruff format --check` → 120 files already formatted；定向两文件 → 24 passed；全量 `uv run --frozen pytest` → **332 passed / exit 0**（112.98s，含覆盖率闸）。
- 环境注记（踩了两次）：① 定向单文件跑 pytest 必须加 `-o addopts=""`，否则 `addopts` 里的 `--cov-fail-under` 会让「单文件跑全绿」变成 exit 1 且不打印汇总行；② 全量测试要显式 `PYKNORA_DATABASE_URL=postgresql+asyncpg://postgres:dev@127.0.0.1:5434/pyknora`——默认 5433 上跑的是并行会话的 `pyknora-phase0-postgres-1`，口令不是 `dev`，会得到 `InvalidPasswordError` 并被误读成 171 个 setup ERROR。
- 未验证 / 未做：S1（`v_active_chunks` 未过滤已删 KB）、S2（`roles`/`role_grants` 全局、可被任租户 owner 改写）仍在后续批次；`document_blobs` 的字节校验只有单元层（真库往返未建用例）。

## 本轮：全新装库 RLS 漂移修复（0011，2026-10-30，经用户「要」授权）

- 怎么发现的：为验证 `0009` 真正进入迁移链，我在**全新空库**上从 `0001` 重放到 `0010`，
  结果 `pg_policies` 只有 **9** 条，而长期开发库是 **10** 条。差的正是 `index_migrations`。
- 根因是执行顺序，不是策略写错：`migrations/0003_identity_acl.sql:124-128` 的 RLS 表清单里
  已经写了 `index_migrations`，但这张表由 `0004` 创建，`0003` 先执行 →
  `to_regclass('public.index_migrations')` 恒为 NULL → `CONTINUE` 安静跳过，且永远不会回头补。
  开发库之所以有 10 条，是这张表建好后又被人重新应用过一次 `0003`——历史重放的巧合。
- 后果：全新部署的环境里，`index_migrations`（带 `workspace_id`、`/admin` 侧暴露的迁移台账）
  失去数据库层兜底，只剩应用层谓词。属于「升级库正常、全新装库少一层防护」的漂移，
  只有从零重放才会暴露。
- 改动：`migrations/0011_index_migrations_rls.sql`（新建）——单独给这张表
  `ENABLE ROW LEVEL SECURITY` + 建 `ws_isolation` 策略，策略表达式与 `0003:134-138`
  **逐字一致**；沿用 0003 的 `DROP POLICY IF EXISTS` + `CREATE POLICY` 写法保证幂等；
  表不存在时 `RAISE NOTICE` 跳过。**没有改 0003**（I4：已合并号段不改历史文件，
  改了反而让升级库与全新库继续分叉）。
- 已执行（真实输出）：开发库重复应用 0011 → `DO` / `COMMENT`，`policies = 10`（幂等成立）；
  全新库 `pyknora_mig11` 从 `0001` 重放到 `0011` → **11 个迁移全部 OK**、
  `policies = 10`、`index_migrations.relrowsecurity = t`；`ruff check src tests scripts`
  → `All checks passed!`；临时库已 DROP。
- 未验证：`scripts/migrate.py` 本身在本沙箱跑不通（`docker` 命名管道对子进程拒绝，环境限制），
  全链重放是用 pwsh 直连 docker 按同语义复刻的；正常终端/CI 下未跑。
- 下一步：给「RLS 覆盖完整性」加一条结构性守卫（全新库重放后断言策略数 = 应覆盖业务表数），
  用户此前说过暂不做，本次仍未做。

## 本轮：RLS 生效性验证 + 作业表索引（2026-10-30，经用户「先做 a b」授权）

- 任务：缺陷审查遗留的两条基础设施问题——① RLS 从未在非特权角色下验证过；②
  `ingestion_jobs (workspace_id, document_id)` 是外键首列的热路径却无索引。
- **先说一处自我纠正**：我先前判断"RLS 从未在非特权角色下验证过"，**这是错的**。
  `tests/integration/test_rls.py`（成员4，提交 `44f9af3`，6 条用例）早已用独立登录角色
  `pyknora_app`（非 owner / 非 superuser / 非 bypassrls，密码见 `0003_identity_acl.sql`）
  验证了跨租户隔离、未设 `app.workspace_id` 时 fail-closed、越界写入被拒、以及视图
  `v_active_chunks` 的 `security_invoker`。我按 `pg_class.relrowsecurity` 和 conftest 的
  superuser 连接做判断，没查现有测试文件就下了结论——属于核查不充分，本轮实测这 6 条
  **全绿**（`6 passed`）。因此原计划新增的 `test_rls_effective.py` 是重复造轮子，**不建**。
- 真实缺口只有一个：`ingestion_jobs` 上有复合外键
  `ingestion_jobs_workspace_id_document_id_fkey (workspace_id, document_id)`，但该列序上
  没有索引（PG 不为 referencing 端自动建索引）。代价落在"删文档时的级联检查"和
  "这份文档有哪些作业"两条路径上。
- 改动：`migrations/0009_ingestion_jobs_doc_idx.sql`（新建）——补
  `(workspace_id, document_id, created_at DESC)` 复合索引，`IF NOT EXISTS` 幂等，列序对齐外键；
  尾部 `created_at DESC` 让"最新一次作业"省一次 sort。不用 `CONCURRENTLY`：本仓库迁移是
  单事务逐文件执行，CONCURRENTLY 不允许出现在事务块内。
- 已执行（真实输出）：`psql -f migrations/0009_...` → `CREATE INDEX` / `COMMENT`；
  `pg_indexes` 查询 → `ingestion_jobs_ws_doc_idx | CREATE INDEX ... (workspace_id, document_id, created_at DESC)`；
  `pytest -q tests/integration/test_rls.py` → **6 passed**；`ruff check src tests scripts`
  → `All checks passed!`。
- 未验证：索引**收益**未做 EXPLAIN 前后对比（开发库数据量太小，planner 不会选它，只验证了存在性；
  真实收益需在有量级的库上跑）；`0009` 无回滚脚本（I4 迁移只追加）。
- 下一步：c（RLS 覆盖守卫测试：新增业务表漏挂 RLS 时自动红）用户明确暂不做；
  `workspaces` / `workspace_memberships` 两张身份表仍不开 RLS（登录链路必须先读它们，属 ADR-0006 口径）。

## 本轮：citation 校验——citations 必须是模型真读过的那些 + 越界引用可见（2026-10-30，成员3 域）

- 任务：审查清单第 2 条。原问题一句话概括：**`retrieval` 事件里的 `citations` 装的是"检索结果"，不是"答案的依据"**，且没人检查模型正文里的 `[资料 N]` 是否有效。
- 两个具体缺陷（都可复现，不是理论风险）：
  1. **幻影引用**：`ChatRequest.top_k` 上限 20，而上下文只取 `chat_max_context_chunks=6`；`api/v1/chat.py` 却拿**全部** `chunks` 去建 citations。传 `top_k=20` 就有 14 条 chunk 没进过模型的 prompt，却作为"答案引用"发给客户端。
  2. **零校验**：system prompt 要求模型用 `[资料 N]` 标注依据，但代码里没有任何地方解析这些标注。模型写 `[资料 9]`（上下文只有 6 条）或 `[资料 0]`，服务端照单全收，客户端看到的是一段"有出处"的答案。
- 改动：
  - `services/rag/answer.py`：新增三个纯函数——`select_context_chunks()`（上下文上限的唯一取值点）、`cited_indices()`（正则 `\[资料\s*(\d+)\]`）、`invalid_cited_indices()`（越界序号）。`stream_answer` 内部改为调用 `select_context_chunks`，不再自己写 `chunks[:N]`。
  - `api/v1/chat.py`：`context_chunks = select_context_chunks(...)`，**citations 与 prompt 用同一个切片**；新增 `_done()`，在发出 `done` 前统计越界引用并 `logger.warning`。
  - `schemas/chat.py`：`DoneEvent` 新增 `invalid_citations: int = 0`（默认 0，向后兼容）。
- **边界（有意不做的）**：不改正文。token 是流式逐块发出的，回头改会让客户端看到的内容与"模型实际说了什么"不一致，排查时无法复原；越界只做"计数 + 日志 + 事件字段"。也不按 `chunk_id` 校验——模型看不到 UUID，没法可靠复述，真正的落点只能是序号。
- 新增用例 5 条：`test_select_context_chunks_caps_at_setting`、`test_cited_indices_parses_markers`、`test_invalid_cited_indices_flags_out_of_range`（单测）；`test_chat_citations_match_what_the_model_actually_read`（检索 8 条 → citations 6 条、且 `stream_answer` 收到 6 条）、`test_chat_reports_citations_outside_context`（`[资料 9]` → `done.invalid_citations == 1`）。两条 API 用例在旧代码上必红（旧代码发 8 条 citations、且 `done` 无该字段）。
- 已执行：`ruff check` → All checks passed；`ruff format --check` → 119 files already formatted；定向两文件 → 12 passed；全量 → 见下。
- 未验证：真实模型的引用合规率（没有模型 key，测试用脚本桩，只能证明"解析与上报"这条链路对，不能证明 `gpt-4o-mini` 实际会不会越界）。

## 本轮：检索单路降级 + 降级可见（2026-10-30，经小组长授权，成员3 域）

- 任务：审查清单第 1 条——`search_hybrid` 原先"两路并行但零容错"：`asyncio.gather` 任一分支抛异常整请求 500，而另一路明明能答。起始 `HEAD=3557751`。
- **设计取舍：降级必须可见**。本项目已经吃过一次静默失效的亏（`tsvector` 整段汉字一个 token → FTS 那一路恒空且不报错，融合结果比纯向量还差却没有任何信号）。所以这次不是"悄悄降级"，而是把失败事实随结果一起返回：
  - `HybridSearchResult(hits, degraded)`（frozen dataclass，`services/retrieval/search.py`）——`degraded` 空 = 两路都成功；非空时键是分支名（`vector`/`keyword`）、值是错误码。
  - `QueryResponse` 新增 `degraded: dict[str, str]`（默认 `{}`，向后兼容）并在 `api/v1/query.py` 原样透出；`api/v1/chat.py` 降级时打 `logger.warning`（SSE 的首个 `retrieval` 事件形状不变，避免动流式契约）。
- 降级规则（**唯一判定处在 `search_hybrid`**）：单路 `INVALID_REQUEST` → **不降级、原样上抛**（请求本身不合法时另一路返回什么都是错的，降级会把"参数错了"伪装成"搜得少"）；单路其他异常 → 记 `degraded[分支]=错误码` + warning，另一路照用；两路全挂 → 抛第一个错误（`AppError` 原样、其他折叠成 `INTERNAL_ERROR`），**不返回空列表**——空列表等于谎称"库里没有匹配"。
- 顺带收掉一条死代码：`search_vector` 原先除测试外无调用方；现在 `search_hybrid` 的向量分支复用 `search_vector`，两条路对 embedding 失败的处理从此只有一份实现（原先 hybrid 里把"embed → 校验返回条数"又写了一遍）。
- 改动文件：`services/retrieval/search.py`、`schemas/query.py`、`api/v1/query.py`、`api/v1/chat.py`、`tests/unit/test_retrieval_search.py`（+4 条降级用例、`FakeSearchRepo` 加 `vector_error`/`keyword_error` 两个注入点）、`tests/api/test_api_chat.py`（桩函数改返回 `HybridSearchResult`）。
- 新增用例：`test_hybrid_degrades_to_keyword_when_vector_branch_fails`（断言 `degraded == {"vector": "DEPENDENCY_UNAVAILABLE"}`）、`test_hybrid_degrades_to_vector_when_keyword_branch_fails`、`test_hybrid_raises_when_both_branches_fail`、`test_hybrid_does_not_degrade_on_invalid_request`。
- 已执行：`ruff check src tests scripts` → All checks passed；定向三文件 → 29 passed；全量 `pytest` → 见下。
- 未验证：SSE `chat` 路只加了日志、**没有把 degraded 放进事件流**（改动流式契约要同步前端，本轮不动）；降级的真实触发（真把 embedding 服务打挂）没做故障注入。

## 本轮：`top_k` 资源边界（2026-10-30，经小组长「我允许你修改别人的目录」授权，成员3 域）

- 任务：代码审查留下的 `top_k` 无上限（`docs/IMPLEMENTATION_STATUS.md` 上一轮自己记的"遗留未修"第 2 条）。起始 `HEAD=1104ed3`。
- **授权记录（第一次）**：用户原话「我是小组长，我允许你修改别人的目录」。此前我的纪律是"只在自己目录写入、别人的目录只追加并请本人复核"；自此在**本会话范围内**可以直改成员3/4/5 的目录。这不改 `AGENTS.md` §4 I5 本身——I5 仍是默认纪律，这次是**小组长对本次会话的显式例外**，所以记在这里而不是改 AGENTS。
- 为何这一条值得修：`top_k` 会同时进向量路与关键词路两条 SQL 的 `LIMIT`，还决定融合阶段建多少个 `RetrievedChunk` 和响应体大小。`QueryRequest.top_k` 原先只有 `gt=0`，服务层也只查 `< 1`，所以 `top_k=100000` 是一次请求放大三处资源（DB 扫描 / 内存 / 响应体积）。
- 改动：
  - `src/pyknora/schemas/query.py`：新增 `MAX_TOP_K: Final[int] = 50`（注释写明 50 的依据与"为什么是资源边界不是调参项"）；`top_k` 由 `Field(default=10, gt=0)` 改为 `Field(default=10, ge=1, le=MAX_TOP_K)`，超限返回 **422 而不是静默截断**——静默截断会让调用方误判"库里只有 50 条"。
  - `src/pyknora/services/retrieval/search.py`：新增 `_require_bounded_limit(value, *, name)`，`search_vector` 与 `search_hybrid` 的 `top_k`、以及 `search_hybrid` 的 `candidate_k` 都走它。**两层不是重复校验**：schema 挡 HTTP 入口，服务层挡脚本/worker/内部调用，而 `limit` 是直接进 SQL 的。
  - 用例：`tests/api/test_api_query.py` 新增 `test_query_rejects_top_k_above_max`（422 且 `repo.query_vector is None`，证明校验在建连打库之前）与 `test_query_accepts_top_k_at_exactly_max`（边界值放行，用 `QueryRequest` 直接构造——本文件夹具把 `limit == 3` 写死了，走 HTTP 会假失败）；`tests/unit/test_retrieval_search.py` 新增 `test_limits_above_max_top_k_are_rejected`（含 `details == {"max": 50, "actual": 51}`）与 `test_hybrid_rejects_over_max_candidate_k`。
- 已执行：`uv run --frozen ruff check src tests scripts` → All checks passed；`uv run --frozen pytest tests/unit/test_retrieval_search.py tests/api/test_api_query.py` → 21 passed；全量 `pytest` → 见下方交付话术（连库需 `PYKNORA_DATABASE_URL` 指向活库）。
- **踩到的假红（记录以免后人重复排查）**：中途 `tests/api/*` 集体 403，而同一条用例在几分钟后自己变绿。原因不是我的改动——并行会话当时正在改 `services/auth/jwt.py`（未提交），鉴权链处于中间态。教训与上一轮一致：同一仓库多会话并行时，全量测试发红先看 `git status` 里有没有别人的在途文件。
- 未验证：`candidate_k` 仍**没有调用方传入**（`query.py` 不传），所以它现在只是"传了会被限住"，而不是"已经被用起来"；调到 `top_k*2` 属于融合效果调优，需要评测集才能判断值不值得改，本轮不动。
- 下一步：`search_vector` 仍是死代码（`query.py` 已走 `search_hybrid`），要不要删需成员3 定。

## 本轮：审查三条缺陷收口（2026-10-29，经用户「要，一并处理审查」授权）

- 任务：代码缺陷审查（用户 `/receiving-code-review 检查当前项目有无缺陷`）里被判定要修的三条——
  `#6` 队列 `claim()` 不带类型过滤；P1-a `jwt.decode_token` 对**缺 `exp`** 的令牌放行；
  P1-b 上传先 `await file.read()` 读满整个请求体再判断 50MB 上限。起始 `HEAD=1104ed3`。
- 改动：
  - `src/pyknora/services/queue/jobs.py:69`：`claim()` 新增 `job_types: Sequence[str] | None = None`，
    非 None 时拼 `AND c.job_type = ANY(:job_types)`；None 保持"不过滤"（兼容既有调用与测试）。
  - `src/pyknora/workers/ingest_worker.py:284`：新增模块常量
    `SUPPORTED_JOB_TYPES: Final[tuple[str, ...]] = ("ingest", "purge")`，`run_once` 显式传给 `claim`。
  - `src/pyknora/services/auth/jwt.py:131`：`exp` 改为**必填**——缺失即 `UNAUTHENTICATED / "malformed token"`
    （`issue_token` 永远写 `exp`，所以"签名对但没有 exp"只可能是手工构造或伪造）。
  - `src/pyknora/api/v1/documents.py:118`：抽出 `_read_bounded(file, limit)`——先用 `file.size` 声明值挡，
    再按 1MiB 分块读到 `limit + 1` 字节立即中断，`read_upload()` 改为调用它。
  - 测试：`tests/unit/test_upload_bounded.py`（新增 3 条：声明超限零读取 / 未声明超限提前中断 /
    正常读完）、`tests/unit/test_auth_a4.py`（+`test_token_without_exp_is_rejected`）、
    `tests/integration/test_queue_fencing.py`（+`test_claim_filters_by_job_type`）。
- 已执行（真实输出）：`uv run ruff check src tests` → `All checks passed!`；
  `uv run ruff format --check src tests` → `111 files already formatted`；
  定向 `pytest -q tests/unit/test_upload_bounded.py tests/unit/test_auth_a4.py
  tests/integration/test_queue_fencing.py` → **21 passed**；
  `PYKNORA_DATABASE_URL=…:5434 uv run pytest -q` → **308 passed, exit 0**。
- 未验证：`claim` 的 `job_types=None` 兼容路径没有专门用例（现有测试都走新参数或默认）；
  `_read_bounded` 的 `file.size` 在生产 ASGI 下由 Starlette 从 multipart 头填入，测试里是伪值。
- 下一步：审查剩余的 #4（连接池 2 session/请求未压测）等低风险项；`2 extra` 遗留项见上一轮段。

## 本轮：检索两路真并行（2026-10-29，成员3 域，经用户「5要并行」授权）

- 任务：缺陷审查（用户 `/receiving-code-review 检查当前项目有无缺陷`）列出的第 5 条——
  `src/pyknora/services/retrieval/search.py` 的 `search_hybrid` docstring 写着「并行候选检索」，
  实际是两串行 `await`。起始 HEAD `2aa9d8d`，`git status` 起始为干净。
- 关键约束（决定实现形状）：**SQLAlchemy 的 `AsyncSession` 不支持并发语句**，同一 session 上
  `asyncio.gather` 两路查询会抛 `InvalidRequestError`。所以「并行」必须由调用方提供**两个绑定
  不同 session 的仓库**，不能靠库里自己拆。
- 改动：
  - `src/pyknora/api/deps.py`：新增 `get_keyword_search_repo()`（独立 session 的 `SqlChunkSearchRepo`）。
  - `src/pyknora/services/retrieval/search.py`：`search_hybrid()` 新增 `keyword_repo: ChunkSearchRepo | None = None`；
    传入则 `asyncio.gather` 两路，不传则退回顺序（语义一致，单测/脚本不必开真连接）。
  - `src/pyknora/api/v1/query.py`、`src/pyknora/api/v1/chat.py`：注入并透传 `keyword_repo`。
  - `tests/api/test_api_query.py`：`_client()` 补 `get_keyword_search_repo` 的 override——只 override
    第一个依赖时关键词那路会真连库返回空集，融合结果与 fake 数据对不上（首跑 4 条红的根因）。
  - `tests/unit/test_retrieval_search.py`：新增 `test_search_hybrid_routes_keyword_to_second_repo`，
    断言关键词没落到向量仓库上（串了 session 时 fake 上看不出来，生产是随机 `InvalidRequestError`）。
- 已执行（真实输出）：`uv run ruff check src tests` → `All checks passed!`；
  `uv run ruff format --check src tests` → `110 files already formatted`；
  `PYKNORA_DATABASE_URL=postgresql+asyncpg://postgres:dev@127.0.0.1:5434/pyknora uv run pytest -q`
  → **303 passed, exit 0**。
- 未验证：真 PG 下的并行收益未压测（理论上两路各一次往返，延迟应减半）；**连接数**——每个检索
  请求从 1 个 session 变成 2 个，默认 `pool_size=5 / max_overflow=10` 下高并发会比之前更早耗尽，
  是否调大或复用连接未评估。
- 下一步：#6（`claim()` 无 `job_type` 过滤）等用户裁决；审查里的两条 P1（JWT 缺 `exp` 即被接受、
  `documents.py:121` 先全量读内存再比 50MB 上限）仍未修。

## 本轮：成员管理与权限自省补全（2026-10-29，成员4 域，经用户「你写一份成员管理修复计划，然后执行」授权代做）

- 背景：用户问「目前的权限模块是不是残缺的」。实测遍历 9 个 v1 router 的路由表得 **23 条 API 路由 / 19 条挂 `require_*` 依赖**，缺的不是判权机制（`role_grants` 已接入）而是三块**功能性**能力：① 成员管理几乎没做（只有 `PATCH` 改角色，不能加人/移人）；② 所有权无法转移（`workspaces.owner_principal_id` 只有建区时写一次）；③ 没有任何力量阻止"新端点忘挂权限依赖"。
- 流程：先落盘计划书 `docs/plans/成员管理修复计划.md`（T1–T7 验收断言 + 三条语义约定 + 明确不做清单），再按 T1→T7 执行。**无新迁移**：成员表、角色表、`role_grants` 已具备所需列与约束，缺的只是端点与仓库方法。
- 改动（6 文件，均本轮新增/修改）：
  - `src/pyknora/db/repos/principals.py`：+`delete_membership(session, workspace_id, membership_id) -> bool`、+`transfer_ownership(...)`——后者在**同一事务**里做三条 UPDATE（`workspaces.owner_principal_id`、原 owner 降 `admin`、目标升 `owner`），且升级那条带 `WHERE role <> 'owner'` 守卫，防止把唯一 owner 覆盖成别人。
  - `src/pyknora/api/v1/workspaces.py`：+`MemberAdd` / `MemberPage` / `MemberPermissions` / `OwnerTransfer` 四个模型；+6 个端点（`GET`/`POST /workspaces/{ws_id}/members`、`DELETE /workspaces/{ws_id}/members/{membership_id}`、`PUT /workspaces/{ws_id}/owner`、`GET /workspaces/{ws_id}/members/{membership_id}/permissions`）；`GET /workspaces/{ws_id}/permissions` 保持"自省不要求管理权"。
  - `src/pyknora/api/v1/auth.py`：+`MeResponse` + `GET /auth/me`（返回 `principal_id`、`display_name`、当前工作区、`role`、`permissions`），客户端不必再猜自己能干什么。
  - `tests/api/test_api_members.py`（新增，17 用例 + 1 组 parametrize）：覆盖列人/加人/重复 409/未知 principal 404/owner 不可指派/未知角色 422/guest 越权 403/owner 不可删 422/自己退 204/退别人 404/跨区 404/转移所有权三条写入一致/非 owner 转移 403/转移给非成员 422/`/auth/me` 角色与权限计数/guest 权限数 7 且无写权限。
  - `tests/unit/test_route_permission_coverage.py`（新增，5 用例）：遍历 9 个 router 的路由，断言除白名单外**全部**挂 `require_*` 依赖；同时断言白名单无幽灵条目、router 全挂上、路由数 ≥20 且挂依赖数 ≥15——后三条是防"测试自己假绿"。
  - `docs/plans/成员管理修复计划.md`（新增）：计划与验收记录。
- 三条越权语义（写进代码注释与测试，全仓一致）：
  1. 路径 `ws_id` ≠ `scope.workspace_id` → **404**（403 等于承认那一行存在）；
  2. 身份为真但权限点不够 → **403**（`missing permission: <perm>`）；
  3. 会破坏工作区不变量 → **422**（移除 owner、把 owner 指派给别人、把所有权转给非成员）。
- 过程中修的两个真缺陷（都是"从没被调用过所以没人发现"）：
  - `MembershipDetail` 有两个必填字段（`workspace_id` / `user_id`）而在 `/members` 列表处构造时漏传 `workspace_id`、还多传了不存在的 `created_at` —— Pydantic 会直接 `ValidationError`。原先没人调用这条路径，测试一上手才炸。
  - `DELETE /members/{id}` 一度给整个端点挂了 `require_permission(WORKSPACE_MANAGE_MEMBERS)`，导致 `member`/`guest` **无法主动退出**（他们本来就没有 manage_members）。改成函数体内 `_can_leave_self` 判权：管别人要权限点，退自己只要身份一致。
- 已执行（真实输出）：
  - `uv run ruff check src tests` → `All checks passed!`；`uv run ruff format src tests` → `2 files reformatted, 106 files left unchanged`。
  - `uv run pytest -q tests/unit/test_route_permission_coverage.py tests/api/test_api_members.py` → **22 passed**。
  - 全量 `uv run pytest -q`（真库 5434，迁移 `0001`–`0006` + `0008` 已应用）→ **297 passed / exit 0**（基线 274 + 本轮 23）。
- 未验证 / 有意未做：
  - `GET /workspaces/{ws}/members` **不分页**：假设单区成员数 ≤ 数百（表上有 `workspace_memberships(workspace_id)` 索引支撑）。成员数上千时要改 keyset 分页。
  - **不做邀请制**：加人要求对方 `principal_id` 已存在（先注册），刻意不做"按 email 邀请"，否则这个接口就成了账号枚举面。
  - `roles` 仍是**全局表**（无 `workspace_id`），跨租户自定义角色不支持——ADR-0007 的有意取舍，本轮未改。
  - `/auth/me` 每次请求查一次 membership（未加缓存）：与撤销权限立即生效同向取舍。
  - 所有权转移后**旧 owner 的 token 不失效**（token 里没存角色，角色是从库里现查的，所以下个请求就按 `admin` 判——这是有意的，但无专门用例断言）。
- 下一步：无（成员管理这一层收口）。

## 本轮：中文 FTS 修好（2026-10-29，成员2 域，经用户「现在先不做评测集合，把中文 FTS 修好或砍掉」授权）

- 背景：用户问「当前的项目手写实现你觉得怎样」，逐条核查后给出的第一条真问题就是**中文全文检索实际查不出**。证据（本机实跑）：`to_tsvector('simple','银狐锚点报销标准')` 产出 `'银狐锚点报销标准':1`——**整段汉字被当成一个 token**；因此 `@@ plainto_tsquery('simple','银狐')` = `f`，只有把整句话原样输入才 = `t`。后果不是报错而是**静默返回 0 行**，混合检索（`search_hybrid`）里 FTS 那一路等于恒空，RRF 融合效果比纯向量还差。用户给出的两个选项是 A（装 `pg_bigm`/`zhparser`）与 B（承认 FTS 不可用、降级成纯向量并诚实标注），并要求本轮动手、不做评测集。
- 决策：**选 A 但不装扩展**——自己写 bigram 分词函数，复用现有 `chunks.tsvector` 列与 GIN 索引。理由（实测）：`pg_bigm` 与 `zhparser` **不在 `pg_available_extensions`**，要用得自建 Docker 镜像编译（把一个"改一列口径"变成"所有人生成环境"）；`pg_trgm` 虽可用且 `ILIKE '%银狐%'` 能命中，但它是模糊相似度不是确定性命中、2 字中文查询的三元组索引选择性差。自写 `pyknora_cjk_bigram()` 只需两个 IMMUTABLE SQL 函数，`chunks` 当时 0 行 → 回填成本为零。
- 关键设计（两处都经实测才定）：
  - **分词口径**：汉字取相邻 2 字（`银狐 狐锚 锚点 点报 报销 销标 标准`），英文数字整词小写，单字保留，标点/空白当分隔符丢弃。副作用是 `v2.1` 会被切成 `v2` + `1`。
  - **AND→OR 两级降级**：只用 AND 的话，"一线城市住宿报销上限是多少钱"这种问句 match 不到任何文档（实测 f）；只用 OR 又会把相关性拉平。所以先用 AND（所有 bigram 同时命中，精确），不足 `limit` 才补 OR（宽松）。两级结果按 `chunk_id` 去重并保持 `ts_rank_cd` 降序。实测 `银狐报销标准`（不连续）对文档 `银狐锚点报销标准` 是 AND=f / OR=t，正是这条设计的存在理由。
  - **注入面**：查询串里的 `& | ! : * (` 在分词阶段被当分隔符丢掉，实测 `a & b | !c :* (d)` → 词元只剩 `a b c d`；tsquery 用绑定参数拼。
  - **性能**：512 字中文块 × 1000 次 = 10ms（10 微秒/chunk），10 万 chunk 约 1 秒，可忽略。
- 起始 HEAD：本轮开始时工作区已被并发会话改动（成员4 域），`git status` 非干净；本轮只改下列文件，未触碰并发会话在改的文件。
- 改动文件（6 改 + 1 新增）：
  - 新增 `migrations/0007_cjk_bigram_fts.sql`：`pyknora_cjk_bigram(text)`（plpgsql，IMMUTABLE PARALLEL SAFE STRICT）+ `pyknora_cjk_tsquery(text, join_op)`（sql，返回 NULL 而非空串，因为 `to_tsquery('simple','')` 会抛语法错）+ 一条 `WHERE tsvector IS DISTINCT FROM …` 的幂等回填。**号段**：本文件 21:48 落盘，与并发成员的角色改动同时出现 `0007`，按先到先得保留 `0007`，对方让号为 `0008`（见其条目）。
  - `src/pyknora/db/repos/reads.py`：`search_keyword` 改为两阶段 AND→OR（AND 命中不足 limit 才跑 OR，按 `chunk_id` 去重保序），两条 SQL 用 `_keyword_stmt(join_op)` 缓存成 `TextClause`（`join_op` 白名单 `and`/`or`，避免 f-string 拼 SQL）。
  - `src/pyknora/workers/ingest_worker.py`、`src/pyknora/services/migration/reindex.py`、`src/pyknora/services/migration/snapshot.py`：三处写 `tsvector` 的 SQL 统一改为 `to_tsvector('simple', pyknora_cjk_bigram(content))`（与 worker 同一句的注释一并更新）。
  - `scripts/seed_for_m3.py`：造数时同口径；原先注释里"汉字会被切成一个 token，成员3 拿半截词搜会 0 命中"的警告改为"已修"。
  - `tests/api/test_api_query.py`、`tests/integration/test_search_repo.py`：夹具插入 `tsvector` 的写法同步（原先靠 `_seed_one` 里直接 `UPDATE … to_tsvector('simple', content)` 补列，现在随 INSERT 一次写好）。用例**不是"新增 6 条"**：`tests/integration/test_search_repo.py` 从 5 条扩到 **12** 条，其中关键词 7 条（`matches_full_chinese_token` / `matches_partial_chinese_word` / `falls_back_to_or_for_scattered_terms` / `single_char_does_not_match` / `ignores_tsquery_metacharacters` / `punctuation_only_returns_empty` / `filters_workspace_in_sql`）；`tests/api/test_api_query.py` 仍是 6 条，只是把中文命中断言并进了 `test_query_returns_fused_hits_and_uses_trusted_scope` 与 `test_query_returns_hits_from_real_postgres`。
  - `docs/plans/m2_contract_draft.md`：升 v2，新增 §3.5 `ChunkRepo.search_keyword` 契约小节，§2 的 `tsvector` 列说明改为新口径，§8 提醒成员3 删掉旧的 `plainto_tsquery` 写法。
- 已执行：迁移逐文件 `docker exec -i pyknora-pg psql -v ON_ERROR_STOP=1 -f -` → 8 个文件全 `EXIT=0`；`uv run --frozen ruff check src tests scripts` → `All checks passed!`（EXIT=0）；`uv run --frozen pytest -q` → **274 passed / exit 0**（unit 105 / contract 20 / api 96 / integration 53），其中 `tests/integration/test_search_repo.py` 单独跑 **12 passed**。
- 代码审查后追加修复（用户 `/receiving-code-review 检查当前代码`）：审出**一处我自己引入、外部可触发的 500**——`to_tsquery` 是递归下降解析，词元数决定递归深度；实测 1000 字→999 词元/13.6ms、5000 字→4999 词元/200ms、**20000 字→`stack depth limit exceeded`（PG 报错，经 API 即 500）**。旧实现 `plainto_tsquery('simple', …)` 把整段汉字当 1 个词元所以从不崩，这是 bigram 打开的新风险面；`QueryRequest.query` 无 `max_length`，故封顶落在仓库层：`reads.py` 新增 `_MAX_KEYWORD_QUERY_CHARS = 256` 并 `query_text[:_MAX_KEYWORD_QUERY_CHARS]`（截断只影响关键词这一路，向量路仍用完整问题），新增用例 `test_keyword_search_truncates_overlong_query`（5000 字不 500、仍返回命中）。同时纠了一处注释谎报：0007 迁移头注释称本机"`pg_bigm`/`zhparser` 查不到"，实测是**能看到名但 `installed_version = NULL`**（从未 `CREATE EXTENSION`），结论不变但表述要准。
- **自查自纠（同一轮内发现我自己上一版写错）**：上一版我在 `snapshot.py` 的 `_TSVECTOR_SQL` 注释里写"`FORBIDDEN_COLUMNS` 不含 `tsvector`，快照会照抄源库"——**这是错的**。`snapshot.py:40` 的 `FORBIDDEN_COLUMNS = frozenset({"embedding", "tsvector"})` 明确含 `tsvector`，且 `tests/unit/test_migration_snapshot.py::test_build_snapshot_rejects_tsvector_column` 就在断言导出时拒绝该列。已改正，并把"为什么 `IS NULL` 是对的"重新论证：导出不搬该列 → 重放后必为 NULL → 全量重算；留 `IS NULL` 还让该句对"部分行已有词元"的库幂等。
- 成员5 域复核（用户「成员5复核」）：`reindex.py` / `snapshot.py` 属成员5 目录，我只改了其中写 `tsvector` 的语句，故按"改了别人目录要复核"的纪律做了一轮针对性审查，结论与产出见下条。
- 复核产出的两条新用例（都验证过"不是假绿"：把口径临时退回裸 `content`，两条都会红，恢复后转绿）：
  - `tests/unit/test_tsvector_encoding_sites.py`（新增，2 用例，不需要库）：扫描 `src/ scripts/ migrations/ tests/` 里**真正给 `tsvector` 列赋值**的行，要求每行都含 `pyknora_cjk_bigram`；另有 `test_write_sites_are_not_empty` 断言四个写入点仍在扫描范围内，防"规则失效后全绿"。
  - `tests/integration/test_migration_tsvector.py`（新增，2 用例）：直接 import 并执行 `snapshot._TSVECTOR_SQL`（不是复制一份 SQL），先插 `tsvector IS NULL` 的 chunk 复刻快照重放现场，跑完要求"报销"/"住宿"这类二字词能被 `search_keyword` 命中；另一用例钉住 `WHERE build_id IN :builds` 真的收窄。
  - 复核的结论是**没有功能性缺陷**：三处写入 + 快照恢复的 SQL 都已是新口径，`reindex` 的 workspace/build 谓词与快照的 build 谓词都正确；缺陷只在我上一版写错的注释，已修。
- 审查后复验（真实输出）：`uv run --frozen pytest tests/integration/test_search_repo.py` → **13 passed**；全量 `uv run --frozen pytest` → **297 passed / exit 0**（junitxml 复核 tests=297 failures=0 errors=0；297 含并发会话新增的 22 条成员管理用例）；`ruff check` → `All checks passed!`；`ruff format --check` → `116 files already formatted`。
- 审查后追加修复之二（2026-10-30，经用户「确认」授权越域改成员3 的 `src/pyknora/schemas/query.py`）：`QueryRequest.query` 加 `Field(min_length=1, max_length=1000)` 作为第二道封顶（仓库层仍保留 256，防止绕过 schema 的内部调用方）；上限取 1000 而非 256 是为不砍掉正常长问句；`min_length=1` 拒绝空问题（空问题会走到 embedding 但语义为空，返回无关片段比 422 更糟）。新增用例 `tests/api/test_api_query.py::test_query_rejects_overlong_and_empty_query`（1001 字与空串均 422，且 `repo.query_vector is None` 证明没进检索）。契约同步在 `docs/plans/m2_contract_draft.md` §3.5。
- 审查未修的已知项（已记录，非本轮范围）：`query.py` 依赖具体类 `SqlChunkSearchRepo` 而非 `ChunkSearchRepo` 协议（分层耦合，成员3 域）；KB 不存在返回空列表而非 404（与 I8 不泄漏存在性一致，保留）；`search_hybrid` 里关键词那路异常仍会让整次融合失败（无单路降级）。
- 未验证：① **没有任何评测数据**——本轮确定的是"一个能跑且可解释的默认值"，不是"已证明更优的分词方案"，PRD `:601` 要求的 Recall@5/MRR/NDCG 与 100 条标注问答一条都没做（用户明确本轮不做评测集）；② 单汉字查询返回空（bigram 最短有效长度 2 汉字），要单字召回得再叠 `ILIKE`/`pg_trgm`，未做；③ 纯英文查询的口径（整词匹配，无词干/同义词）未与 BM25 对照；④ `tsvector` 体积变大对写入放大与磁盘的影响未测；⑤ 停用词表未做，「的」「了」也进 bigram 索引。
- 环境边界（不是仓库缺陷，但会挡住别人）：`docker` 从 **PowerShell 直接调用可以**，从 **Python 子进程**调用被拒（`permission denied while trying to connect to the Docker API at npipe:////./pipe/dockerDesktopLinuxEngine`），试过不捕获 stdout、只捕获 stderr、`subprocess.run` 三种写法都一样 → 所以 `scripts/migrate.py` 在本机跑不通，迁移改用 pwsh 循环逐文件 `docker exec`。已写进下方"未验证"之外的自查记录，未改 `migrate.py` 代码（它设计上就该走 docker CLI）。
- 下一步：把用户拍板的两条接上——(a) 中文分词口径要不要进 `docs/tdd/06` 的正式决策（现在只在本状态文件与本轮迁移注释里）；(b) 评测集仍欠着，等做的时候这条 FTS 是第一个被量化的对象。另：`search_vector` 仍是死代码、`top_k` 仍无上限、`candidate_k` 仍从不传、citation 校验仍缺失——本轮都没动。



- 背景：用户连问「`role_grants` 空壳、`is_public` 死字段是干什么的」→「实现路径文件里面是怎么定的」→「那等于说表的逻辑从开始已经错了对吗」→「说人话」→「补」。逐条拆解后的结论是：**表没建错，能跑**；五处口径问题里只有一处是建表时就该避免的（`is_public` 列），而**最实质的一处是「角色集合被定义了三遍」**：`0003` 的 `CHECK (role IN ('owner','admin','member','guest'))`、`roles.role_key`、`workspaces.py` 的 `AssignableRole = Literal[...]`。用户回「补」= 授权修这一条。
- 决策：**角色改为数据驱动**，详见 ADR-0007（提议状态）。`workspace_memberships.role` 摘掉列举式 `CHECK`，换成格式约束 `CHECK (role ~ '^[a-z][a-z0-9_]{0,63}$')` + 外键 `→ roles(role_key) ON UPDATE CASCADE ON DELETE RESTRICT`。`roles` 成为唯一真相源；`permissions.py` 的 `ROLE_KEYS` 改名 `BUILTIN_ROLES`，语义从"角色闭集"收窄为"内置角色"；新增 `OWNER_ROLE`。
- 起始 HEAD `b85a663`；`git status --short` 本轮开始时干净。
- 改动文件（5 改 + 3 新增）：
  - 新增 `migrations/0008_roles_are_data.sql`：三步全程 `DO $$ ... IF NOT EXISTS ... $$` 幂等——(a) 摘 `workspace_memberships_role_check`；(b) 加 `workspace_memberships_role_format_check`；(c) 加 `workspace_memberships_role_fkey`。**号段**：本文件最初编为 `0007`，但工作区里同时出现并发成员新建的 `0007_cjk_bigram_fts.sql`（21:48，早于本文件 21:51），按先到先得让号为 `0008`；两者互不依赖、可任意顺序应用。`0009+` 归位。
  - 新增 `docs/adr/0007_Roles_Are_Data.md`（提议状态、含"为什么仍然保护 owner"与"未做的事"）。
  - `src/pyknora/db/repos/principals.py`：追加 `list_roles()` / `role_exists()` / `create_role()`（已存在返回 `False`，不覆盖）/ `delete_role()` / `count_role_members()`。
  - `src/pyknora/services/auth/permissions.py`：`BUILTIN_ROLES = frozenset(SEED)`、`ROLE_KEYS` 保留为别名、新增 `OWNER_ROLE = "owner"`。
  - `src/pyknora/api/v1/workspaces.py`：删 `AssignableRole`/`Literal` → `role: str` + `_ROLE_KEY_RE`；`update_member_role` 增两条 422（不可设成 `owner`、角色必须存在）；`list_role_catalog` 改由 `repo.list_roles()` 驱动（不再按常量枚举）；`replace_role_permissions` 用 `repo.role_exists` + `OWNER_ROLE`；新增 `POST /workspaces/{ws}/roles`（201，重复 409）与 `DELETE /workspaces/{ws}/roles/{role_key}`（204；在用 409 且 `details.members` 给出人数；`owner` 422）。
  - `tests/api/conftest.py` 两处夹具修复（**新外键逼出来的**）：① `seed_role_catalog` 不再 `DELETE FROM roles` 全删，改为"先把挂在自建角色上的成员退回 `member` → 删非内置角色 → 清 `role_grants` → 内置四角色 `INSERT ... ON CONFLICT DO UPDATE`"；② `seed_test_identity` 把 `seed_role_catalog` 调用提到最前（外键要求写成员前角色已存在）。
  - `tests/api/test_api_role_grants.py` 追加 **6 条**：新建 `auditor` 无需迁移即可指派并按新矩阵判权 / 指派不存在的角色 422 / `role_key` 非法 422（`Auditor`、`1auditor`、含空格、65 字符）/ 重复建角色 409（不覆盖）/ 在用角色删除 409 且换角色后 204 并消失于目录 / `owner` 不可重建不可删。
- 已执行：`uv run --frozen pytest -q tests/api/test_api_role_grants.py` → **14 passed**；`ruff check` → `All checks passed!`；`ruff format --check` → `114 files already formatted`；全量 `pytest -q`（真库 5434，`0001`–`0006` + `0008` 已应用）→ **274 passed / exit 0**（unit 105 / contract 20 / api 96 / integration 53）。
- 未验证：`roles` 仍是全局表（无 `workspace_id`），**跨租户自定义角色不支持**——多租户场景下 A 工作区新建的角色会对 B 工作区可见（可指派），这是本轮有意不解决的问题，已写进 ADR-0007「未做的事」；`0008` 只在 superuser 下应用过，未在 `pyknora_app` 非特权角色下验 RLS 交互；角色重命名（`ON UPDATE CASCADE` 那条路径）没有测试覆盖。
- 下一步：`0008` 号段由成员5 追认；`is_public` 仍未动（维持 ADR-0006 的口径）。**另注**：工作区里还有并发成员的在途改动（`migrations/0007_cjk_bigram_fts.sql` 与 `db/repos/reads.py`、`workers/ingest_worker.py`、`services/migration/{snapshot,reindex}.py`、`scripts/seed_for_m3.py`、`tests/{api/test_api_query.py,integration/test_search_repo.py}`），**不在本轮提交范围内**。

## 本轮：`role_grants` 落地（2026-10-29，lead 代修成员4 域，经用户「好吧你落地role_grants」授权）

- 背景：用户先问「`role_grants` 空壳、`is_public` 死字段是干什么的」→ 我答「不是废弃，是**预留未接入**：表和列都建好了，但系统里没有任何代码读写」并说明真正接线要动成员4 的目录 → 用户回「好吧你落地role_grants」= 授权跨域实现。
- 决策：**运行期判权链改为 `workspace_memberships.role` → `roles` → `role_grants` → 权限点集合**，`api/v1/workspaces.py` 的 `_ADMIN_ROLES` 硬编码集合删除。权限**每请求查一次、不跨请求缓存**（与 P0-2 同原则：撤权必须在下一个请求生效，集进 JWT 或进程缓存都是"吊销延迟"这类看不见的错）。判权失败统一 `AppError(PERMISSION_DENIED)` → 403，消息点名缺哪个权限点（`missing permission: document.write`），便于排障也不泄漏"存在但无权"的对象信息。
- 起始 HEAD `b99391a`；`git status --short` 本轮开始时干净。
- 改动文件（12 改 + 4 新增）：
  - 新增 `migrations/0006_role_grants.sql`：补 `owner/admin/member/guest` 四个角色（`ON CONFLICT (role_key) DO UPDATE` 自愈——此前 `0003` 建过 `member`，但测试夹具 `TRUNCATE roles` 会把它删掉，不自愈则 `UPDATE` 影响 0 行、`role_grants` 挂空）+ 49 行权限矩阵（15/15/12/7）+ `COMMENT ON` 说明。**号段说明**：`0004` 归成员5、`0005` 是合并时占用（待成员5 追认），本轮占用 `0006`，`0007+` 仍归各成员原号段。
  - 新增 `src/pyknora/services/auth/permissions.py`（99 行）：15 个权限点常量 + `ALL_PERMISSIONS` / `ADMIN_ONLY` / `READ_ONLY` / `SEED` / `ROLE_KEYS` / `is_known_permission()`。**有意收紧**：`guest` 从"没人用"变成真只读（原任何成员都能上传）；`member` 保留 `index.rebuild`（与接入前行为一致，避免顺手改变重建索引的权限面）。
  - `src/pyknora/db/repos/principals.py`：新增 `list_permissions_for_role` / `list_role_grants` / `add_role_grant` / `remove_role_grant`（均幂等，返回 `bool`）。
  - `src/pyknora/services/auth/membership.py`：新增 `load_permissions()`，签名**关键字限定** `(session, *, principal_id, workspace_id)`。
  - `src/pyknora/api/deps.py`：新增 `get_permissions()` 与 `require_permission(permission)` 依赖工厂（403）。
  - 路由接线：`router` 级——`query`(query.run) / `chat`(chat.run) / `lineage`(lineage.read) / `admin`(index.rebuild)；端点级——`knowledge_bases`(列表/详情=x read、建/改=write、删=delete)、`documents`(上传=document.write、列表/详情=document.read、切片=chunk.read、删除=document.delete)、`jobs`(读取=job.read、取消=job.cancel)、`workspaces`(改成员角色=workspace.manage_members，**语义仍是 404 不泄漏成员关系**)。
  - 新增 3 个管理端点：`GET /workspaces/{ws}/permissions`（自省，不要求管理权——谁都能看自己有什么）、`GET /workspaces/{ws}/roles`（`workspace.manage_members`）、`PUT /workspaces/{ws}/roles/{role_key}/permissions`（`workspace.manage_roles`，**整组替换**；`owner` 不可替换）。
- 顺手修的真 bug（**新用例逼出来的**）：`src/pyknora/api/deps.py` 里写成 `load_permissions(session, scope.workspace_id, scope.principal_id)`，而签名是 `(session, principal_id, workspace_id)`——两个参数都是 `UUID`，传反不报错，稳定返回空集 → 所有走权限依赖的接口一律 403。修法：调用点改关键字传参，并把 `load_permissions` 签名改成关键字限定，让同类错误在类型层面无法再发生（这条已写进它的 docstring）。
- 新增测试：`tests/unit/test_permissions.py` **8 条**（常量自洽 / `SEED` 矩阵 / `is_known_permission`）、`tests/api/test_api_role_grants.py` **9 条**（member 默认能上传 / guest 只读且点名缺 `document.write` / 库层撤 `document.write` 后同一 token 下一次请求 403 / 端点受管且整组替换生效并二次读目录确认落库 / 未知权限点 422 / `owner` 不可替换 422 / member 不能给自己所在的角色提权 / member 可读自己的权限）；`tests/api/conftest.py` 补 `seed_role_catalog()`（每用例 `DELETE` + 批量 `INSERT` 一次写完，与 `roles` 表同源，避免 50 次往返）。
- 已执行：`uv run --frozen ruff check src tests scripts` → `All checks passed!`；`ruff format --check` → `114 files already formatted`；`uv run --frozen pytest -q`（真库 5434）→ **263 passed / exit 0**（unit 105 / contract 20 / api 90 / integration 48）。
- 未验证：`PUT` 的真实并发（两个 admin 同时替换同一角色 → 后写覆盖前写，无锁）；权限矩阵调整对既有业务用例的影响只覆盖到上传与建库两条；`0006` 未在 `pyknora_app`（非 superuser）角色下单独验 RLS 交互。
- 下一步：`0006` 提交后由成员5 追认号段；`is_public` 仍是预留字段（本轮未动，理由见 ADR-0006「实现期补充」末段）。

## 本轮：P0–P4 安全与一致性收口（2026-10-29，lead 代修成员4 域，经用户「p0-p4全部解决」授权）

- 背景：用户先问「权限部分怎么做的」→ 再要求「排优先级」→ 排查中确认 **P0-1（JWT 密钥无生产护栏）比原先列的四个缺口都严重**：`jwt_secret()` 回落硬编码默认值、无任何环境校验，等于任何拿到仓库的人都能伪造任意 `sub` / `workspace_id` 的令牌。用户回「p0-p4全部解决」= 授权跨域修复（已在上一条优先级表里说明这会动成员4 的目录）。
- 起始 HEAD `145fb87`；`git status --short` 本轮开始时干净。
- 改动文件（9 个源码/配置 + 3 份文档）：
  - `src/pyknora/core/settings.py`（重写）：新增 `env: str = "dev"`、`jwt_secret: str = ""`；`DEV_JWT_SECRET` / `DEV_CURSOR_SECRET` 提为公开常量；`_INSECURE_ENVS = {"dev","test","local"}`；`@model_validator(mode="after")` 在非 dev 环境拒绝空密钥与开发默认密钥。
  - `src/pyknora/services/auth/jwt.py`：`DEFAULT_SECRET` 改为从 settings 导入 `DEV_JWT_SECRET`（单一事实源）；`jwt_secret()` 回落链 `secret or settings.jwt_secret or DEV_JWT_SECRET`。
  - `src/pyknora/db/repos/principals.py`：`find_membership()` 加 `JOIN PrincipalRow` + `PrincipalRow.status == "active"`。
  - `src/pyknora/api/main.py`：新增 `_UNPRIVILEGED_ENVS`、`_is_rls_bypassed(session)`、`_warn_if_db_role_bypasses_rls(state, settings)`，在 `lifespan` 内调用且吞掉一切异常（告警逻辑绝不能让服务起不来）。
  - `src/pyknora/models/tables.py`：`is_public` 列注释补「预留：公开 KB 可见性，当前所有查询不读」。
  - `src/pyknora/services/auth/membership.py`、`src/pyknora/api/v1/workspaces.py`：注释写明 `role_grants` 未被消费、`_ADMIN_ROLES` 是闭集、`load_membership` 每请求一次查询是刻意反缓存。
  - `tests/unit/test_security_guards.py`（新，9 条）、`tests/api/test_auth_revocation.py`（新，2 条）。
  - `.env.example`（新增 `PYKNORA_ENV` / `PYKNORA_JWT_SECRET` / 生产禁 owner 连库注记）、`README.md`（快速开始第 1 步）、`docs/adr/0006_Identity_Roles_RLS.md`（追加「实现期补充」段，状态仍 `提议`）。
- 已执行（真实输出）：
  - `uv run --frozen ruff check`（9 个改动文件）→ `All checks passed!`；`ruff format --check` → `9 files already formatted`。
  - `pytest -q tests/unit tests/contract` → 117 passed。
  - 全量 `uv run --frozen pytest -q`（真库 `pyknora-pg-p0` @5434）→ **247 passed, exit 0**。
  - `pytest --collect-only -q` 逐目录计数 → unit **97** / contract 20 / api **82** / integration 48 = **247**。
- 未验证：① `env=prod` 下的真实启动（本机无生产配置，只有单测覆盖 validator 分支）；② `_is_rls_bypassed` 只在 superuser 与普通角色两态实测，`pyknora_app` 角色未单独验；③ 真实 embedder / 真实 LLM 端到端仍缺（沿用历史未验证项）。
- 下一步：`git push`（本地领先多个提交，用户未表态）；规则层 RLS 的 CI 验证（当前只有 `test_rls.py` 在 `postgres` 超级用户下跑，跑不出 RLS 的真实效果）。

## 本轮：文档事实校正——错误码 16 → 17（2026-10-29，lead，经用户「修改」确认）

- 背景：重写 `README.md` 时逐行数 `src/pyknora/core/errors.py` 的 `ErrorCode` 成员，实际 **17 个**（`UNAUTHENTICATED` … `INTERNAL_ERROR`），而 `AGENTS.md` §2 与 `docs/项目结构说明.md` 两处都写"16 个"。用户 m02798「修改」= 批准改 `AGENTS.md`（§9 要求改前确认、改后记账）。
- 为什么是 17 而不是 16：实现期新增了 4 个 `docs/tdd/07` 原表没有的码——`CONFLICT`（注册/建库撞唯一键，409）、`CURSOR_EXPIRED`（游标签名过期，410）、`DEPENDENCY_UNAVAILABLE`（上游不可用，503 可重试）、`INTERNAL_ERROR`（兜底，500 可重试）；同时原表里的 `RUN_CONFLICT`/`APPROVAL_*`/`BUDGET_EXCEEDED`/`SSRF_BLOCKED`/`BROWSER_*` 等**未实现**（PyKnora 当前范围不含那些能力）。13 + 4 = 17。
- 改动文件：
  - `AGENTS.md` §2 三行：`库结构`（补 0001–0005 合计 16 表 + 视图、ORM 15 个 Row）、`错误码`（16→17，并点名 4 个新增码与登记位置）、`测试`（按实测 88/20/80/48 = 236，取代"C1/C2 已建、C3 待建"的旧描述）。
  - `docs/项目结构说明.md:78` 与 `:263`：`16 个 ErrorCode` → `17 个`。
  - `docs/tdd/07_Data_API_Security.md` §7：新增「实现期新增（**提议**，待用户接受）」小节，逐条写明 4 个码的 HTTP/retryable 与**为什么不能复用已有码**，并注明"表内其余设计期码未实现，别照表数 30 多个"。
- 已执行：`Select-String`/`class ErrorCode` 段统计 → `count=17`；`uv run python -m pytest --collect-only -q tests/unit` → `88 tests collected`（沿用上轮实测，本轮未重跑）。纯文档改动，未动代码。
- 未验证：无新增未验证项。**待用户确认**：`docs/tdd/07` 那 4 个码目前是"提议"状态，接受后才算"已定"。
- 下一步：`git push`（本地领先 16 个提交，用户未表态）；剩下的非任务项见 `docs/项目结构说明.md` §8。

## 本轮：任务 17 迁库演练（2026-10-29，成员5 实现 / lead）

- 任务：进度表任务 **17** ↔ 修复计划 **F9**（P2）；前置 17 ← 5、8、16 已全部满足。起始 HEAD `f4fd18d`（`docs(plan): F9 任务17 施工方案细化到可执行粒度 + 修正过期现状`）；本轮改动**未提交**，`git status --short` 为 ` M scripts/migrate.py` + 5 个新文件 + `.tmp_migration/`。
- 改动文件：
  - `src/pyknora/services/migration/snapshot.py`（新，713 行）：8 张表的导出/重放/校验纯逻辑。`TABLES` = 8 个 `TableSpec`（`principals` / `workspaces` / `knowledge_bases` / `documents` / `document_versions` / `document_blobs` / `index_builds` / `chunks`）、`REPLAY_ORDER`、`SnapshotError`、`TableSpec`、`Check`，以及 `build_snapshot` / `export_metadata` / `replay_metadata` / `verify_metadata` / `render_sql` / `count_embedding_inserts` / `assert_no_embedding`；`FORBIDDEN_COLUMNS = frozenset({"embedding","tsvector"})` 把"搬了向量吗"收敛到一处可查。
  - `scripts/export_metadata.py`（新，77 行）：产物两个文件 `<out>.json`（给 replay 用）+ 同名 `.sql`（给人看/给 psql 用）。计划书 `全员模块实现路径.md:641` 的判据「导出 SQL 里 `embedding` 的 `INSERT` 行数 == 0」由 `count_embedding_inserts()` 判定，非 0 即 **exit 3**。
  - `scripts/replay_metadata.py`（新，71 行）：按 `REPLAY_ORDER` 写入目标库，写完后 `chunks` 无向量、`v_active_chunks` 为空（这是"没搬向量"的可观测证据）；**刻意不提供 `--reindex` 开关**，避免把"重放"和"重建索引"揉成一个动作。
  - `scripts/verify_migration.py`（新，77 行）：按 workspace 比对 KB 集合 / profile 五字段 / documents 集合 / 活动 chunk 边界与正文 / `v_active_chunks` 行数 / 向量 NULL 数；任一不一致非 0 退出，不静默通过。
  - `tests/unit/test_migration_snapshot.py`（新，202 行，**12 条用例**）。
  - **未改**：`services/ingest/publish.py`、`db/repos/reads.py`、`migrations/`（复用任务 16 的 `reindex_knowledge_base()`，不碰发布语义）。
- 已执行（迁移演练，真实输出）：
  - 目标库：**同一个** `pyknora-pg-p0` 容器里 `CREATE DATABASE pyknora_migrate`（不是另起 5435）；`0001_init.sql`～`0005_*.sql` 按号段顺序 `psql -f` 应用完毕。
  - 导出：8 张表 → `.tmp_migration/snapshot.json` + `.tmp_migration/snapshot.sql`；判据行输出「导出 SQL 中涉及 embedding/tsvector 的 INSERT 行数 = 0」。
  - 重放后目标库：**5 个 KB / 7 篇文档 / 31 个切片 / `embedding` 非空 0 行**。
  - 重算前校验：`v_active_chunks` **源 1 / 目标 0**，并报「缺向量 1 行」——`migrate` 后必须先 `reindex`，这条被用数据证出来了。
  - 重算：**4 个存活 KB**（其中两个种子 KB 各 3 篇 15 片），**1 个软删 KB 跳过**。
  - 重算后校验：`uv run python scripts/verify_migration.py --source postgresql+asyncpg://postgres:dev@127.0.0.1:5434/pyknora --target postgresql+asyncpg://postgres:dev@127.0.0.1:5434/pyknora_migrate` → 每个 workspace 的 `knowledge_bases` 集合、KB profile 五字段、`documents` 集合、活动 chunk 边界与正文逐项、`v_active_chunks` 行数、向量无 NULL 全部 `OK` → **结论：全部校验项一致**。
- 本轮文档核对实跑（lead，唯一新跑的验证命令）：`uv run python -m pytest --collect-only -q tests/unit` → `tests/unit/test_migration_snapshot.py: 12`、**`88 tests collected in 1.41s`**（原 76 + 12；全量 **236** = 88 + 契约 20 + API 80 + 集成 48）。**口径**：用例数以这条 `--collect-only` 为准；此前口头流转的「21 条」与磁盘上的 12 个 `test_` 函数不符，一律按实测记。
- 演练暴露的 3 个真问题（不粉饰，详见 `docs/plans/任务17_迁库演练记录.md`）：
  1. `v_active_chunks` **不 join `knowledge_bases`**：KB 被软删后，只要文档没删、build 是 active、向量非空，切片仍会露在视图里（源库那条"幽灵行"）。校验器因此自行加 `JOIN knowledge_bases … AND k.deleted_at IS NULL` 才对齐口径——这是**校验器的口径修正，不是视图改动**（视图属 `0001_init.sql`，不可回改历史号段）。
  2. `reindex_knowledge_base` 对软删 KB 取不到 profile（`repo.get` 过滤 `deleted_at`）→ 已删 KB 无法重算向量。这是**设计边界而非缺陷**；校验器据此分两组断言：存活 KB 必须无 NULL 向量、软删 KB 必须保持 NULL。
  3. 演练数据里两个种子 workspace 只有 `knowledge_bases` 行（`scripts/seed_for_m3.py` 不写 `workspaces`），且 `workspace_memberships` 不在导出 8 表内 → `POST /api/v1/admin/reindex` 依赖 JWT + membership 的 HTTP 路径在演练里走不通，改用进程内 `reindex_knowledge_base()`（scope 取 KB 所属空间），记录里已注明"绕过 HTTP"；HTTP 202 路径仍由 `tests/api/test_api_admin.py` 与烟测库覆盖。
- 边界（刻意不做，不是遗漏）：**不搬向量**（只搬"元信息 + 原文"，跨 PG 版本/跨维度靠目标库重算换可迁移性）；**不搬权限数据**（`workspace_memberships` 不在导出 8 表内）；**不搬 `outbox`**；`mode=export` / `mode=replay` 仍未接进 `/admin/reindex`，仍返回 422 `INVALID_REQUEST`。
- 未验证：① 真实大库（数百文档）的导出/重放耗时与内存占用（当前是单文件快照）；② 并发重放 / 并发重算；③ 跨机器网络传输（演练是同容器内两库）；④ export/replay 的 **HTTP 端点未做**——演练走 CLI 三脚本 + 进程内 reindex。
- 回滚方式：`DROP DATABASE pyknora_migrate` 即可；源库 `pyknora` 全程只读导出，未被写入。
- 下一步：**17/17 任务已全部 `已完成/100%`**；剩余为非任务项，见 `docs/项目结构说明.md` §8（`citations` 表、`tests/contract/test_c3.py`、五份 ADR 仍"提议"、Phase 0 九项 PoC 全 `NOT_STARTED`）。本轮未提交，由 lead 统一提交。

## 本轮：任务 16 重建索引 `/admin/reindex`（2026-10-29，成员5 实现 / lead 复核）

- 任务：进度表任务 **16** ↔ 修复计划 **F8**；起始 HEAD `145fb87`（工作区仍为本轮未提交改动）。
- 改动文件：
  - `src/pyknora/db/repos/index_migrations.py`（新，154 行）`SqlIndexMigrationRepo`：`create/get/list_for_kb/finish/find_succeeded_by_idempotency_key`，全部 SQL 带 `workspace_id` 谓词。
  - `src/pyknora/services/migration/reindex.py`（新，566 行）`reindex_knowledge_base()`：**重建 = 按 KB 现有 profile 对「已发布 build 的切片正文」重算向量 → 新 `index_version` 的 staging build → 走既有 `publish()` 激活**；不重解析、不重切片、不改 `publish` 语义、不加表/列/迁移。
  - `src/pyknora/api/v1/admin.py`（新，113 行）`POST /api/v1/admin/reindex` → **202**；`ReindexRequest{kb_id,mode,idempotency_key}`、`ReindexResponse{migration_id,kb_id,mode,state,documents,chunks,skipped,failed,replayed}`。
  - `src/pyknora/api/main.py`：**仅 +2 行**（一条 import、一条 `include_router`）。
  - `tests/api/test_api_admin.py`（新，438 行，**11 条用例**）。
- 已执行（真实输出）：
  - `uv run ruff check src tests scripts` → `All checks passed!`
  - `uv run ruff format --check src tests scripts` → `104 files already formatted`
  - `$env:PYKNORA_DATABASE_URL='postgresql+asyncpg://postgres:dev@127.0.0.1:5434/pyknora'; uv run pytest tests/api` → **80 passed in 110.73s**，exit=0
  - `uv run pytest tests/api/test_api_admin.py` → **11 passed**；`uv run pytest tests/unit` → **76 passed**
- 终验（同日收尾后，lead 独立复核；三条同时成立才算"跑通"）：
  - 全量：`PYKNORA_DATABASE_URL=…5434/pyknora uv run pytest -q tests/unit tests/contract tests/api tests/integration` → **224 passed**，exit 0（unit 76 + contract 20 + api 80 + integration 48）。
  - 门禁：`uv run python scripts/ci.py --stage all` → `PASS lint/ruff-check 0.1s`、`PASS lint/ruff-format 0.0s`、`PASS test/pytest 7.5s`，total 7.6s。
  - 端到端：真起三进程（假 OpenAI 兼容服务 8011 + 真 API 8010 + 真 Worker）后 `uv run python scripts/smoke_e2e.py --base-url http://127.0.0.1:8010` → **14/14 通过**，exit 0（A1 健康、A2 注册/登录/错密码 401、A3 建库 201+同区重名 409 `CONFLICT`、A4 上传 202→job `succeeded`、A5 `char_start<char_end`、A6 检索命中、A7 `retrieval→token*→done`、A8 lineage 创建者与来源、A9 他人 KB 检索空/直读 404）。
  - 口径注：本章上方的 **213 passed** 是 B0/B2 收尾时那次实跑（当时 `tests/api` 69 条），**224 是终验数**（任务 16 的 11 条 admin 用例落地后 `tests/api` 为 80 条）；两者时点不同，均保留原样。证据汇总见 `docs/plans/项目修复计划.md` §10。
- 11 条用例锁住的四条硬断言：① 新 build active、旧 build retired、`active_index_version` 严格 +1；② 重建前后 `content` + `chunk_ordinal` 逐项相同（重建不重写正文）；③ 他人 workspace 的 kb → **404**（不是 403 / 202）；④ 连调两次每文档仍恰一个 active build，同 `idempotency_key` 重放不刷版本。另含 `mode=export` → 422 且不落记账行、空 KB → 202 且 `documents=0`。
- 未实现：`mode=export`、`mode=replay` **明确返回 422 `INVALID_REQUEST`**，不静默降级。
- 未验证：① 真实大 KB（数百文档）的耗时与连接占用；② reindex 下 `EmbeddingDimensionError` 路径未单独构造用例；③ 并发两个 admin 请求同 KB 的行为。
- 已知风险 5 条：① `job_type='reindex'` 依赖 `ingestion_jobs.job_type` 无 CHECK 约束（将来加枚举需同步）；② 并发无锁，可能撞 `index_builds` 唯一约束表现为 500；③ 整库在一个事务里跑完才回 202，大库会顶网关超时；④ 只要求 `require_kb_access`（读权限即可触发全量重建，成本敏感）；⑤ 每次重建每文档会在 `ingestion_jobs`/`job_attempts` 留一行审计记录，无清理策略。
- 测试口径偏差（记录以免误读）：`knowledge_bases.kb_id` 在库里是**全局主键**，「同一 kb_id 登记两区」只存在于 `FakeKbRepo`；跨区用例实际测的是「B 区自己的 kb_id + `workspace_id` 谓词」这条防线。
- 下一步：任务 **17**（迁库保留索引信息 ↔ F9）；本轮未提交，由 lead 统一提交。

## 本轮：B0 环境对账 + B2 主链路烟测（2026-10-29，lead）

- 任务：**无新任务号**——按 `docs/plans/项目修复计划.md` §4 收尾 **B0 环境对账 + B2 主链路烟测**，并把进度表拉回代码现状。起始 HEAD `145fb87`（`fix(m2): 0005 主键改复合 + lineage 用例补成员关系 + 夹具单条 TRUNCATE`）；本轮改动**未提交**，开工时 `git status --short` 为 **6 M + 4 ??**。
- 改动文件：
  - 脚本：`scripts/seed_for_m3.py`（修 2 个 bug，+70/−19）、`scripts/smoke_e2e.py`（新增，端到端烟测）。
  - 业务（3 个真 bug）：`src/pyknora/services/kb/service.py`、`src/pyknora/services/rag/answer.py`、`src/pyknora/schemas/chat.py`、`src/pyknora/api/v1/chat.py`。
  - 测试（新增）：`tests/api/test_api_chat.py`（4 条）、`tests/unit/test_rag_answer.py`（3 条）。
  - 流程/记录：`AGENTS.md`（4 处，理由见下）、`docs/plans/成员任务进度表.md`（v1.0）、`docs/IMPLEMENTATION_STATUS.md`（本节）、`docs/plans/项目修复计划.md`（F1/F3/F4/F6 打勾 + B0 新发现）。
- B0 环境对账（真实输出）：
  - `migrations/0001–0005` 五条全部 **rc=0**；`information_schema.tables`（public）计数 **17 = 16 表 + `v_active_chunks`**。
  - `scripts/seed_for_m3.py` 修两个 bug 后才跑通（都不是新增需求，是脚本自身缺陷）：
    1. 三张表（`documents`/`index_builds`/`chunks`）之间 **ORM 没有 relationship**，SQLAlchemy unit of work 只按 mapper 名排序，结果 `chunks` 先于 `documents` 插入（外键必然失败）→ 改为按依赖序逐层 `flush()`。
    2. `migrations/0002` 给 `documents` 加了复合外键 `doc_kb_fk → knowledge_bases(workspace_id, kb_id)`，而 seed 的 `kb_id` 是 uuid5 算出来的、父行从未插入 → 新增 `ensure_knowledge_bases()` 先插父行。
  - 修后输出：`seed ok: 30 chunks, workspaces=2, docs=6, builds=6, per_workspace=15, model=test/hashing, dim=512`。
- B2 主链路烟测（真实输出）：
  - 新增 `scripts/smoke_e2e.py`：只用 HTTP、不 import 应用、不碰数据库会话，按修复计划 §0 的 **A1–A9 共 14 项断言**逐条打印 OK/FAIL；真起 API + Worker（`hashing` embedding + 假 OpenAI 兼容服务）→ **14/14 通过，退出码 0**。
  - 顺带修 3 个真 bug（都是烟测/用例逼出来的，不是猜的）：
    1. `services/kb/service.py`：同名建库返回 `IDEMPOTENCY_CONFLICT`（该码的语义是"同一个幂等键换了 payload"），改为 `CONFLICT`——与 `core/errors.py:17-20` 对注册撞唯一键已定的口径一致。
    2. `services/rag/answer.py`：生成器内抛 `AppError` 时 `StreamingResponse` 已经开始发，Starlette 撞 `"response already started"`，客户端只看到 `httpx.RemoteProtocolError`；改为把 `httpx.HTTPError` 降级成 SSE `error` 事件、再补 `usage` 收尾，`schemas/chat.py` 新增 `StreamErrorEvent`，`api/v1/chat.py` 处理 `error` 分支并补 `done`。
    3. `api/v1/chat.py`：在构造 `StreamingResponse` **之前**校验 `settings.chat_api_key`，缺 key 直接 503，不再拖进流里。
  - `uv run python scripts/ci.py --stage all` → `PASS lint/ruff-check 0.0s` / `PASS lint/ruff-format 0.0s` / `PASS test/pytest 9.1s`，exit 0。
  - `uv run pytest -q tests/unit tests/contract tests/api tests/integration` → **213 passed**，exit 0。
- 为什么改 `AGENTS.md`（§9 要求流程基线修改在当轮记录理由）——4 处都是"照旧文档会跑错"的事实纠偏，没有改任何流程条款：
  1. §2 本地库：改为"容器名与端口以 `.env` 的 `PYKNORA_DB_CONTAINER`/`PYKNORA_DATABASE_URL` 为准"，并注明别人机器上常见 `5434` + `pyknora-pg-p0`（先 `docker ps` 再改 `.env`，别改代码默认值）。
  2. §2 测试：`tests/contract/` 从"待建"改为"C1/C2 已建、C3 待建"，`tests/api` 注明"ASGI 打真 PG"。
  3. §6 注意事项：新增"跑测试前先停本地 API 与 Worker"（共用同一个库，`TRUNCATE ... CASCADE` 与 Worker 的 `FOR UPDATE SKIP LOCKED` 相撞会 `deadlock detected`，表现为"随机某条用例 ERROR"）、`uv sync --extra dev` 与 cp936 乱码说明。
  4. §8：坑 1 改写（`get_kb_repo` 未注入时会回落 `SqlKnowledgeBaseRepo(session)`，"直接 uvicorn 上传 503"已过期；`/health` 的 `kb_repo: null` 只是按请求构造）+ 新增坑 9（流式端点里不许抛 `AppError`，降级成 SSE `error` 再补 `done`；建流前能判的提前 503）。
- 进度表口径修正（本轮唯一改状态的地方，详见 `docs/plans/成员任务进度表.md` §9 v1.0）：
  - 任务 **7/13/14/8**：`未开始/0%` → `已完成/100%`。依据是代码与用例早已落地（`services/retrieval/`、`services/rag/`、`services/lineage/`、`api/v1/{query,chat,lineage}.py`，`tests/api/test_api_{query,chat,lineage}.py`、`tests/unit/test_{retrieval_search,rag_answer,lineage}.py`），进度表落后于代码（修复计划 F10），本轮**不是新增交付**；A6/A7/A8 由烟测实跑覆盖。
  - 任务 **10**：曾一度由 `已完成/100%` 下调为 `进行中/50%`（理由：`migrations/0003` 已建 `roles`/`role_grants` 表、`models/tables.py` 有映射，但零读写代码）；**同日经用户裁决走方案 C，恢复 `已完成/100%`**——保留 `role_grants` 为**预留权限点表**（`RoleGrantRow` 映射保留），**不提供 grant/revoke 读写接口**，角色授权以 `workspace_memberships.role` 为准、不做部门模型。判据是计划书 `docs/plans/全员模块实现路径.md:627` 对该任务的完成标准「只建表与一个默认角色，不做多角色」，依据另见 `docs/adr/0006_Identity_Roles_RLS.md:15`。留痕：旧口径未删除，见进度表 §3 口径说明与 §9。
  - 任务 **16/17** 保持 `未开始/0%`（F8/F9，P2，本轮未动）。
- 未验证 / 未做：
  - **`role_grants` 与 `RoleGrantRow` 是预留权限点表，不是已实现的细粒度权限**：本期**没有** `/role-grants` 之类任何 grant/revoke 读写接口（全仓零读写代码），接口层也没有按 `role_grants` 判权的路径；实际授权与可见性只按 `workspace_memberships.role`（owner/editor/viewer）判定，无部门模型、无多角色树、无 RLS 策略挂接。若后续要做细粒度权限，属于新增工作，不是"修 bug"。依据：`docs/adr/0006_Identity_Roles_RLS.md:15`、`docs/plans/全员模块实现路径.md:627`（方案 C，2026-10-29 用户裁决）。
  - **`scripts/migrate.py` 的原生路径在 DSH 沙箱内仍未跑通**：脚本靠 Python 子进程调 docker CLI，沙箱下报 `permission denied ... npipe:////./pipe/dockerDesktopLinuxEngine`。本轮迁移是**绕开脚本**、用 pwsh 管道把 SQL 喂给 `psql` 逐条验证的（0001–0005 全 rc=0）——"脚本自身端到端"仍是未验证项；普通终端不受影响。
  - 烟测用的是 `hashing` embedding + 假 OpenAI 兼容服务；真实 embedder（`BAAI/bge-small-zh-v1.5`）与真实 LLM 的端到端仍未跑（同前几轮）。
  - 未跑 security 阶段（需联网）；未改 4 份 xlsx（进度表以 xlsx 为准，本轮只改汇总视图，**两处口径暂时不一致**）；五份 ADR 仍为"提议"。
  - 未 `git add` / `commit` / `push`（由 lead 统一提交）。
- 下一步：**B1 提交收尾**（`0005` 已随 `145fb87` 入库；剩 F2：`scripts/migrate.py` 的 `--container/--user/--db/--dry-run` 与容器存在性预检未做）→ **B3**（F7 剩余断言、F10 文档纠偏；`docs/项目结构说明.md` 另有工单）→ **B4** 功能补齐（F8 任务 16 `/admin/reindex`、F9 任务 17；F11 任务 10 已裁决为方案 C = 保留预留表、无需施工）。

## 本轮：合并远端 chat 分支 + 修 0005 主键口径（2026-10-29，成员2）

- 任务：**无新任务号**（不做新功能，只收口）。两件事：① 收尾一次未完成的 `merge`（远端成员3 的分支 `8caace1`「新增 chat 对话接口」）；② 修合并后暴露的两条红测试。起始 HEAD `4e1d1f2`（工作区已处于 merge 中间态：15 个文件已 staged、4 个冲突未解），收尾后 HEAD `d134c8f`。
- 4 个冲突的解法：
  - `.gitignore`：取并集（远端放行 `.env.example`，本侧保留 `.tmp_ref/` 与 WSL 检出忽略）。
  - `src/pyknora/api/main.py`：两侧路由都保留；`chat` 按字母序排到 `auth` 之前，否则 ruff I001 红。
  - `src/pyknora/models/tables.py`：两族表并集（本侧 8 张 + 远端 `index_migrations`）。
  - `src/pyknora/services/contracts/reads.py`：4 个新 Protocol 与远端 `DocumentVersionRepo` **同名不同义**（远端按 document 分页列版本，本侧按 version_id 单查）→ 远端原名保留（`list_versions`），本侧改名 `DocumentVersionLookupRepo`（`get_version`），`tests/contract/test_c2.py` 两处引用同步改。
- 修的两条红测试（合并后全量 206 项 = 204 通过 + 2 红）：
  1. `tests/integration/test_index_migrations.py::test_same_migration_id_in_two_workspaces_is_allowed`——`migrations/0004_index_migrations.sql` 把 `migration_id` 写成**单列** PRIMARY KEY，同文件注释与该测试却要求「业务主键在 workspace 内唯一」，两者不可能同时成立（`UniqueViolation: index_migrations_pkey`）。按 I4「不改已合并的历史迁移」，**新开号段** `migrations/0005_index_migrations_pk.sql`：DO 块守护（表不存在则跳过、只在仍是单列主键时动）→ `PRIMARY KEY (workspace_id, migration_id)`，并删掉因此冗余的 `UNIQUE (workspace_id, migration_id)`。ORM `IndexMigrationRow` 同步：`workspace_id` 补 `primary_key=True`，`__table_args__` 去掉 `UniqueConstraint`。
  2. `tests/api/test_api_lineage.py::test_second_contributor_shows_up_with_active_pointer`——`assert 403 == 202` **不是权限缺陷**：`BOB` 是 B 区 owner，在 A 区没有成员关系，`deps.get_scope` 拒绝是对的；错在测试没造「A 区第二个贡献者」。在该用例内显式插 `workspace_memberships (WORKSPACE_A, BOB, 'member')`，**故意不放进全局夹具**——否则等于把 BOB 在 A 区的权限放大，会让跨区用例变成假通过。
- 附带的夹具修复（不是新需求，是上面 5 个 fixture ERROR 的根因）：`tests/api/conftest.py` 与 `tests/integration/conftest.py` 原本**逐表** `TRUNCATE`（16 张表 × 每个用例 = 每个用例 16 次 fsync），在 Windows 的 docker VM 上每张表都要等 `DataFileImmediateSync`，整套跑 12 分钟，且 ACCESS EXCLUSIVE 句子越多越容易撞超时（表现就是 5 个 ERROR、单独跑却全绿）。改成一条 `TRUNCATE TABLE a, b, … CASCADE`。
- 已执行（真实输出；临时真库 `pyknora-pg-p0` 宿主 **5434**，四条迁移 `0001/0002/0003/0004` 重建后 **16 张表 + `v_active_chunks`**，再追加 `0005`）：
  - `uv run --frozen pytest -q` → **206 passed**，exit 0，**162s**（改夹具前是 12 分钟 + 5 个 ERROR）
  - 单跑两条原本红的用例 → `9 passed`（`test_index_migrations.py` 7 + `test_api_lineage.py` 2）
  - `uv run --frozen python scripts/ci.py --stage all` → `PASS lint/ruff-check 0.1s` / `PASS lint/ruff-format 0.0s` / `PASS test/pytest 14.5s`，`GATE_EXIT=0`
  - `psql` 核对 `index_migrations` 约束 → `index_migrations_pkey | PRIMARY KEY (workspace_id, migration_id)`，原 `index_migrations_workspace_id_migration_id_key` 已不存在
- 未验证 / 未做：
  - `0005` 只在「0004 已执行」的路径上实跑过；「0005 先于 0004」的分支只有 `to_regclass` 守护逻辑，**没有实跑**。
  - `0005` 占用的是成员5 的号段（`0004_*` 一族），原因是合并发生在成员2 侧、而红测试挡在门前；已在 SQL 文件头写明缘由，**需成员5 追认**。
  - 远端带来的 `/api/v1/chat` 路由只有 import 级验证（`create_app` 能装配），**没有一条 chat 用例**；流式响应、真实 LLM 调用都未验证。
  - 真实 embedder（`BAAI/bge-small-zh-v1.5`）端到端仍未跑（同前几轮）。
  - 未 push（`ahead origin/master 7`）。


- 任务：成员2 自审打回的两条 P0 缺陷（加固进度表任务 **3/4/5/6** 的防线，不新增任务号）。起始 HEAD `1724a23`，开工时工作区干净。
- 缺陷与修法：
  1. **只校验维度、不校验模型**（`src/pyknora/services/ingest/embedder.py`）：512 维的模型不止一个，原 `check_profile_compat` 只比 `embedding_dim`，两套不兼容的向量空间会被静默混用——不报错，只是检索结果全错。新增 `EmbeddingProfileMismatch`（`details` 带 `field/expected/actual`），`check_profile_compat` 改为**维度与模型名都比**；老 profile 缺字段时仍跳过（兼容）。
  2. **worker 身份伪造**（`src/pyknora/workers/ingest_worker.py`）：原 `Scope(principal_id=job.workspace_id)` 把一个工作区 UUID 当成用户身份。改为从 `job.payload["principal_id"]` 取 UUID；`src/pyknora/services/ingest/documents.py` 三处入队 payload（新建 v1 / 新版本 / purge）补齐该字段；老作业无字段时 `logger.warning` 回落 `workspace_id`，非法值抛 `INVALID_REQUEST`。
  3. **（修 P0 时被测试逼出的第三处）上传入口用错身份来源**（`src/pyknora/services/ingest/documents.py`）：原拿 `settings.embedding_model_key` 与 KB profile 比对，而该字段**只有 local 后端权威**（openai 走 `openai_embedding_model`、hashing 固定 `test/hashing`），非 local 部署下每份上传都会误报。新增 `resolve_embedding_identity(settings)`，返回 `build_embedder` 会产出的 `(model_key, dim)` 但不加载权重；`HashingEmbedder` 的 `test/hashing` 提为 `HASHING_MODEL_KEY` 常量。
- 改动文件：`src/pyknora/services/ingest/embedder.py`、`src/pyknora/services/ingest/documents.py`、`src/pyknora/workers/ingest_worker.py`、`tests/unit/test_profile_compat.py`、`tests/conftest.py`（新增）。
- 已执行（真实输出；连临时真库 `pyknora-pg-p0`，宿主 **5434**，`0001`+`0002`+`0003` 三条迁移在重建库上跑通 → **16 表 + `v_active_chunks`**）：
  - `uv run --frozen pytest -q` → **185 passed**，exit 0（`tests/unit` 61 / `tests/contract` 20 / `tests/api` 61 / `tests/integration` 43）
  - `uv run --frozen python scripts/ci.py --stage all` → `PASS lint/ruff-check 0.3s` / `PASS lint/ruff-format 0.2s` / `PASS test/pytest 13.2s`，`total 13.6s`
  - `ruff check`（本轮 5 个文件）→ `All checks passed!`；`ruff format --check` → 已格式化
- 中间失败与闭环：`X9` 改完立刻让 `tests/integration` **9 条全红**，报 `EmbeddingProfileMismatch: kb wants 'test/hashing', process has 'BAAI/bge-small-zh-v1.5'`——根因即上第 3 条，修复后 43 条集成全绿。这次失败反过来证明新校验有效：它逮住的第一件事就是自己的错。
- 未验证 / 未做：
  - **真实 embedder 端到端仍未跑**（`BAAI/bge-small-zh-v1.5` 需下载权重）；`resolve_embedding_identity` 的 local 分支只有单测覆盖，没有真模型比对。
  - `tests/conftest.py` 把**测试进程**的 embedding 身份钉成 hashing（测试数据本来就用 `test/hashing`，此前只比维度所以没暴露）；生产不受影响，embedder 与 settings 同源。
- 下一步：成员2 P0 已闭环，无遗留；P1（真实 embedder 端到端）与成员3/5 的并行工作不在本轮范围。

## 本轮：成员2 四任务收口（2026-10-29，成员2）

- 任务：进度表任务 **3 / 4 / 5 / 6**（文档增删改查、切片与 Embedding、向量增删改查、更新时同步 chunk 与元数据）。起始 HEAD `5411c96`；开工时 `git status --short` 已含成员2 的 10 项未提交改动（6 M + 4 ??），本轮在其上继续，**未回滚任何既有改动**。
- 改动文件（本轮共 12 项）：
  - 业务（成员2 名下）：`src/pyknora/services/ingest/documents.py`、`src/pyknora/services/ingest/embedder.py`、`src/pyknora/workers/ingest_worker.py`、`src/pyknora/db/repos/reads.py`、`scripts/run_worker.py`（新增，121 行）
  - 契约/模式（**追加式**，未改任何既有签名）：`src/pyknora/services/contracts/reads.py`（+4 Protocol）、`src/pyknora/schemas/document.py`（+`DocumentVersionInfo`）
  - 测试（新增）：`tests/unit/test_profile_compat.py`、`tests/contract/test_c2.py`、`tests/integration/test_read_contracts.py`
  - 共用文件（**纯机械改动**，见"风险"）：`src/pyknora/api/main.py`（import 排序，清 I001）、`src/pyknora/models/tables.py`（`ruff format` 空行规范化）、`scripts/ci.py`（门禁直连 `.venv/Scripts/*.exe`）
- 已实现：
  1. **补上 Worker 可执行入口**：`workers/ingest_worker.py` 此前只有类与 `run_once`、没有 `__main__`，`ingestion_jobs` 入队后无人消费。新增 `scripts/run_worker.py`（`session_scope()` 每轮新事务 + `SqlKnowledgeBaseRepo` + `build_embedder` + `run_once`，支持 `--once/--poll-interval/--worker-id`）。
  2. **embedding 档位改从知识库 profile 读**：新增 `check_profile_compat(profile, *, dim, model_key)`；`process_job` 以 `recipe_model_key/recipe_dim/recipe_chunk_size/recipe_chunk_overlap/recipe_normalize` 写 `index_builds` 与 `PublishManifest`（此前错用全局 `embedder.model_key/dim`，与任务 16「换模型重建索引」直接冲突）；缺字段时 `_kb_field` 回落并 `logger.warning`；上传入口同步校验，档位不符在 202 之前 4xx。
  3. **补下游只读面**（供成员3/5）：`SqlChunkRepo.get_chunks`、`SqlChunkRepo.list_chunks_by_build`（I1 受控例外，靠 `workspace_id + build_id + 文档未删除` 三重谓词）、`SqlIndexBuildRepo.list_builds`、`SqlDocumentRepo.get_version`，配 4 个 Protocol 与 `tests/contract/test_c2.py`。
- 已执行（真实输出）：
  - `uv run --frozen ruff check src tests scripts` → `All checks passed!`；`uv run --frozen ruff format --check src tests scripts` → `86 files already formatted`
  - `uv run python scripts/ci.py --stage all` → `PASS lint/ruff-check 0.1s` / `PASS lint/ruff-format 0.1s` / `PASS test/pytest 7.4s`，`total 7.6s`
  - `uv run --frozen pytest -q tests/unit tests/contract` → **76 passed**；`uv run --frozen pytest -q tests/integration` → **43 passed**；`uv run --frozen pytest -q` → **180 passed**，exit 0（均连临时真库 `pyknora-pg-verify`，宿主 5434）
  - 迁移：`0001`+`0002`+`0003` 在重建库上三条全 `OK`；落地核对 `information_schema.tables`（public）= **16**，视图 = **v_active_chunks**
  - `scripts/run_worker.py --once` → `worker worker-13296 启动 backend=hashing model_key=test/hashing dim=512` + `队列空，--once 退出`，exit 0
- 未验证 / 未做：
  - **真实 embedder 端到端未跑**：`BAAI/bge-small-zh-v1.5` 需下载权重，全部用例走 `hashing` 后端；`S10` 的"断网下 local 后端可用"仍只是代码路径，无实测。
  - 本机 5433 被外部容器 `pyknora-phase0-postgres-1` 占用，`scripts/migrate.py` 的原生路径（容器名 `pyknora-pg`）本轮未跑通；验证改走 5434 临时库 + `docker exec -i ... -f -`（迁移脚本靠 Python 子进程调 docker，在沙箱下报 `permission denied ... npipe:////./pipe/dockerDesktopLinuxEngine`）。
  - 未跑 security 阶段（需联网）；未改 4 份 xlsx（进度表以 xlsx 为准，本轮只改汇总视图）；五份 ADR 仍为"提议"。
- 风险与下一步：
  - 三处共用文件改动**无业务语义**（import 排序 / 空行 / 门禁执行方式），但按 `AGENTS.md` §1.4 属"共用文件"范畴，需评审确认；`scripts/ci.py` 是插件生成物，重新生成会覆盖该项修复，应回灌插件。
  - `ci.py` 修复的根因是环境性的：DSH 沙箱下 `uv run` 初始化 `D:\uv-cache` 被拒（`os error 5`），门禁在第一步就以 exit 2 红掉——把环境问题伪装成代码问题。直连 venv exe 后 7.6s 全绿。
  - 下一步（交给其他成员）：成员3 接 `ChunkLookupRepo`/`ChunkSearchRepo` 写 RAG 编排（任务 13/14）；成员5 开工前先落 `0004` 迁移（`sources`/`citations`/`index_migrations` 编号顺延，`0002` 已被成员1 占用）。

## 本轮：S05 知识库查询（2026-10-28，成员1）

- 任务：进度表任务2 ↔ S05 查询部分；起始 HEAD `76ef94f`，`git status --short` 为空。前置任务1已完成，本轮未推进任务15。
- 改动文件：`src/pyknora/api/v1/knowledge_bases.py`、`db/repos/knowledge_bases.py`、`db/repos/reads.py`、`schemas/kb.py`、`services/contracts/reads.py`、`core/settings.py`；新增 `services/kb/cursor.py`、`tests/contract/{__init__.py,test_c1.py}`、`tests/unit/test_kb_cursor.py`；扩展 `tests/api/test_api_knowledge_bases.py`。
- 已实现：`GET /api/v1/knowledge-bases` 按 `(created_at,kb_id)` 稳定键集分页，只查当前 workspace 且排除软删除行；游标以 HMAC-SHA256 签名并绑定 workspace、principal 和24小时有效期，篡改、跨 Scope 重放、过期统一返回 410 `CURSOR_EXPIRED`；C1 在保留原 `get_embedding_profile` 签名的基础上追加 `get/list`，真实仓储和 Fake 均满足契约。
- 已执行：`uv run ruff check src tests scripts` → `All checks passed!`；`uv run pytest -q tests/unit tests/contract` → 33 passed（31 unit + 2 contract）；`uv run pytest -q tests/api/test_api_knowledge_bases.py` → 6 passed；`uv run pytest -q tests/integration` → 28 passed；`uv run pytest -q` → 101 passed；`uv run python scripts/ci.py --stage all` → ruff check、ruff format check、31个单元测试全部 PASS。
- 未验证 / 未做：未运行联网 security 阶段；未实现成员4的真实 membership/RLS；未推进任务15；未做并发翻页的快照一致性承诺（每页重新按当前 Scope 和未删除状态查询）；未提交。五份ADR仍为“提议”。
- 风险与下一步：`cursor_secret` 当前有开发默认值，生产必须用 `PYKNORA_CURSOR_SECRET` 覆盖；下一阶段应由成员4基于 C1 `get(scope,kb_id)` 实现任务15，非授权继续统一404。

## 本轮：S05 KB 表与 CRUD（2026-10-28，成员1）

- 任务：S05 ↔ 进度表任务1；起始 HEAD `c1cfb7b`，开工时 `git status --short` 为空。本轮只完成任务1，任务2的列表/游标分页、S06配置固化和任务15权限联调未推进。
- 改动文件：新增 `migrations/0002_knowledge_bases.sql`、`src/pyknora/api/v1/knowledge_bases.py`、`src/pyknora/db/repos/knowledge_bases.py`、`src/pyknora/services/kb/service.py`、`tests/api/test_api_knowledge_bases.py`；追加 `models/tables.py` 的 `KnowledgeBaseRow`、`schemas/kb.py` 的版本字段、`api/deps.py` 真实仓储注入、`api/main.py` 路由，以及 API/集成测试真实 KB 种子和复合外键断言。
- 已实现：知识库创建、详情、带 `If-Match` 的改名、软删除；同区活动名称由部分唯一索引约束；删除递增 `generation`/`row_version` 并同事务写 outbox；`documents(workspace_id,kb_id)` 复合外键拒绝跨区引用；未显式注入测试替身时默认使用 `SqlKnowledgeBaseRepo`。
- 已执行：`uv sync --extra dev` → 成功；`uv run python scripts/migrate.py` → `0001`、`0002` 成功，重复执行再次 `migrations ok`；`uv run ruff check src tests scripts` → `All checks passed!`；`uv run pytest -q tests/unit` → 27 passed；`uv run pytest -q tests/integration` → 28 passed；`uv run pytest -q` → 92 passed；`uv run python scripts/ci.py --stage all` → ruff check、ruff format check、27个单元测试全部 PASS。
- 门禁限制：`git config core.hooksPath .githooks` 已设置，但仓库中的 `.githooks/pre-commit` Git模式为 `100644`、磁盘不可执行，`git hook run pre-commit` 报 `cannot find a hook named pre-commit`；未跨阶段修改该公共钩子，改为直接运行其唯一入口 `scripts/ci.py --stage all` 并通过。
- 未验证 / 未做：未运行独立 security 阶段；未实现任务2列表/游标；未扩展共享 `KbRepo` 的 `get/list` 签名；未提交。五份ADR仍为“提议”。本轮期间另出现无关的 `.gitignore` 修改和已暂存 `.idea/*` 删除，均未回滚、未编辑、未纳入本轮交付。
- 下一步：单独推进任务2“知识库查询”与 C1 契约测试；若提交本轮，先由仓库维护者修复 `.githooks/pre-commit` 的可执行位，并只暂存本轮文件。

## 当前轮：成员4 验收闭环（2026-10-29，成员4）

- 任务：进度表任务 **9 / 11 / 10 / 12 / 15**——把 2026-10-28 落地但因 Docker 不可用而卡在 `进行中/75%` 的代码补齐验收，转 `已完成/100%`。
- 环境恢复：`docker ps` 可用；`pgvector/pgvector:pg16` 镜像拉取完成后 `docker run -d --name pyknora-pg -p 5433:5432 -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=pyknora pgvector/pgvector:pg16`，容器 `Up`。
- 改动文件：`scripts/migrate.py`（1 行实质改动 + 注释：`subprocess.run(..., text=True)` → 追加 `encoding="utf-8"`）、`tests/api/test_auth_api.py`（两处 `UUID(membership_id)` → 直接用 asyncpg 返回值）。其余为本轮之前已落盘的文件。
- 已执行（真实输出）：
  - `uv run python scripts/migrate.py` → 退出 0，`migrations ok`；`0001_init.sql` 与 `0003_identity_acl.sql` 均执行（重复执行幂等，第二次全为 `already exists, skipping`）。
  - 库内核对（`docker exec -i pyknora-pg psql ...`）：`SELECT count(*) FROM roles` → **1**；`role_grants` → **3**；`SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='pyknora_app'` → `t→f / f`（非 superuser、非 bypassrls）；`relrowsecurity` 对 `documents`/`chunks`/`outbox` 均为 `t`；`SELECT reloptions FROM pg_class WHERE relname='v_active_chunks'` → `{security_invoker=true}`。
  - `uv run pytest -q tests/api` → **49 passed**（首轮 2 failed，原因：测试用 `UUID(asyncpg 返回值)` 触发 `AttributeError: 'asyncpg.pgproto.pgproto.UUID' object has no attribute 'replace'`；修测试后全绿）。
  - `uv run pytest -q tests/integration` → **33 passed**（含 `test_rls.py` 6 条）。
  - `uv run pytest -q` → **122 passed**；`uv run ruff check src tests scripts` → `All checks passed!`；`uv run ruff format --check src tests scripts` → `68 files already formatted`。
- 未验证：无（本轮要验的三条命令均真跑过）。仍未做：没改 4 份 xlsx（只改汇总视图）；没动成员1 的 `0002`/成员5 的 `0004`（`0003` 对 `knowledge_bases`/`citations`/`index_migrations` 用 `to_regclass` 跳过，建表后需重跑 `0003` 或在其迁移内补策略）；没 `git add`/提交。
- 风险与下一步：① `pyknora_app` 密码是开发占位，生产必须外部注入并删掉迁移里的 `CREATE ROLE` 段；② `deps.get_scope` 每请求多查一次 `workspace_memberships`，压测出热点再加缓存；③ 本轮 `scripts/migrate.py` 是共用文件，改动属缺陷修复（Windows 默认 ANSI 编码把 SQL 里的中文注释编成 GBK 字节，psql 按 UTF-8 校验即报 `invalid byte sequence ... 0xb5`），已在 §9 v0.6 与本节留痕。

## 历史轮：成员4 身份与权限基座（2026-10-28，成员4）

- 任务：进度表任务 **9 / 11 / 10 / 12 / 15**（计划书 S 编号本轮不适用——这几个任务在 `全员模块实现路径.md` 里没有对应 S##，按任务编号交付）。起始 `git status --short` 含既有 `.idea/` 改动（**非本轮，未碰**）；本轮新增/修改文件见下。
- 改动文件：新增 `migrations/0003_identity_acl.sql`、`src/pyknora/db/repos/principals.py`、`src/pyknora/services/auth/{jwt,passwords,membership}.py`、`src/pyknora/api/v1/{auth,workspaces}.py`、`tests/unit/test_auth_a4.py`、`tests/unit/test_auth_passwords.py`、`tests/api/test_auth_api.py`、`tests/integration/test_rls.py`、`docs/adr/0006_Identity_Roles_RLS.md`（状态：提议）；修改 `src/pyknora/api/{deps,main}.py`、`src/pyknora/models/tables.py`（末尾追加 5 个映射）、`src/pyknora/core/errors.py`（加 `CONFLICT` 枚举 + 409 映射）、`tests/api/conftest.py`（身份表入 TRUNCATE 清单 + `seed_test_identity`）、`tests/integration/conftest.py`（身份表入 TRUNCATE 清单）、`docs/adr/README.md`、`pyproject.toml`/`uv.lock`（`uv add "bcrypt>=4.1"` → 5.0.0）、`docs/plans/成员任务进度表.md`（§1/§3/§4.4/§9 = v0.5）。
- 已执行（真实输出）：`uv run ruff check src tests scripts` → `All checks passed!`；`uv run ruff format --check src tests scripts` → `68 files already formatted`；`uv run pytest -q tests/unit` → 40 passed（原点 27 + 本轮 13）；`uv run python -c "create_app().openapi()"` → 9 paths，含 `/api/v1/auth/register`、`/api/v1/auth/login`、`/api/v1/workspaces/{ws_id}/members/{membership_id}`。
- **未验证（关键）**：`tests/api/*`（含新增 `test_auth_api.py`、以及被 `get_scope` 真实现影响的既有文档用例）与 `tests/integration/test_rls.py` **一条都没跑**。原因：本机 Docker 引擎起不来——`docker ps` → `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`；`Start-Process "…\DockerDesktop\Docker Desktop.exe"` 被沙箱拦在日志写入（`C:\Users\1\AppData\Local\Docker\log\host\*.log`），`Test-NetConnection 127.0.0.1 5433` → False。因此 `0003` 迁移**未在真库执行过**，RLS 策略、`\set ON_ERROR_STOP`、`FOREACH` 循环、`security_invoker` 的 SQL 合法性全部只是静态推断。
- 未做：① 没改 4 份 xlsx（本轮只改汇总视图）；② 没动成员1 的 `0002`、成员5 的 `0004`，`0003` 里对 `knowledge_bases`/`citations`/`index_migrations` 的 RLS 用 `to_regclass` 跳过，建表后需**重跑 0003** 或在其迁移里补策略；③ 没提交（`git add`/commit 均未执行）。
- 风险与下一步：① 起 Docker 后依次 `uv run python scripts/migrate.py` → `uv run pytest -q tests/api` → `uv run pytest -q tests/integration`，三绿才可把 5 行改 `已完成/100%`；② `deps.get_scope` 现在**每个请求多查一次 `workspace_memberships`**（且 `set_config` 走同一事务），若压测发现热点再加缓存，别先加；③ `pyknora_app` 的密码是开发占位（与 `postgres:dev` 同级），生产部署必须改由外部注入并删掉迁移里的 `CREATE ROLE` 段。

## 历史轮：数据契约补外键（2026-10-28，lead）

- 触发：用户问"`docs/plans/全员模块实现路径.md` 里有些字段没有关联，是错的吗"。结论：该建的就那几处、实建 7 条；其余"不建"是刻意的，写清理由比补全更重要。
- 事实（改动前）：`migrations/0001_init.sql` 的 9 张表每张都建了 `UNIQUE (workspace_id, <主键>)`，但整份文件 `REFERENCES` 为 **0**——复合唯一键的唯一用途（给复合 FK 当引用目标）从未兑现；`knowledge_bases` 表不在 0001（属成员1 的 0002），所以跨模块那条 FK 当时无处可加。
- 改动文件：`migrations/0001_init.sql`（+7 条复合 FK、顶部外键策略三条、`documents.kb_id` 处留指向 0002 的注释）、`docs/plans/全员模块实现路径.md`（§4.3 标"提议 v2"、四表补 `REFERENCES`、新增"引用列判定表"建/不建两张 + 复核 SQL）、`docs/tdd/07_Data_API_Security.md`（§3 通则下加"落地状态"与两处与原文不同之处）、`docs/plans/成员任务进度表.md`（§9 v0.4，记录成员1 任务1 需在 0002 后 `ALTER TABLE` 补 FK）、`tests/integration/test_schema_constraints.py`（新增 3 条 DDL 级断言）。
- 已执行（真实输出）：临时容器 `pyknora-fkcheck`（pgvector/pgvector:pg16，宿主 5434）→ `psql -v dim=512 -f migrations/0001_init.sql` 退出 0；`SELECT conname FROM pg_constraint WHERE contype='f'` 得 7 行且定义含 `ON DELETE RESTRICT`；两条负数用例被库拒绝（孤儿 chunk、跨区 version，均 `violates foreign key constraint`，psql 退出 3）；`PYKNORA_DATABASE_URL=…:5434/pyknora_fk uv run pytest -q` → 88 passed；`uv run ruff check src tests scripts` → All checks passed；`ruff format --check` → 58 files already formatted；`git hook run pre-commit` → 三阶段 PASS，总 2.4s。
- 未验证 / 未做：① 本机 5433 的 `pyknora-pg` 仍是**旧 schema（无 FK）**，新断言在其上 3 skipped，需重建容器并重跑 `scripts/migrate.py` 才生效；② 未改 4 份 xlsx（进度表声明以 xlsx 为准，本次只改汇总视图与其 §9 记录）；③ `knowledge_bases` 的复合 FK 仍待成员1 的 0002，本轮只写约定；④ 五份 ADR 状态未动，§4.3 仍标"提议（v2）"待全员评审；⑤ 未 `git add`、未提交。
- 下一步：用户确认后重建 5433 容器；成员1 建 0002 时按 §4.3 判定表补 `doc_kb_fk`。

## 历史轮：P0-T01 契约闭合（仅文档，2026-10-27）

- 起始分支 `master`，HEAD `5e7e3998ff553edb7a75123f4bea8539b9453f6a`，`git status --short` 为空；没有既有未提交改动。原 PRD Wiki P1 调整已在当前基线，保留其内容。
- 已读取 AGENTS、PRD、实施状态、Phase 0 评审/风险/计划/Backlog/DAG/验收/指南、相关八份 TDD 与全部五份 ADR；未重新审计参考源码或核实远端最新协议。
- 新增 [07 契约闭合](poc/07_Contract_Closure.md)：C-01–C-10 全部给出字段/命令、唯一逻辑写入者、可信身份传播、提交点、幂等/唯一约束、失败恢复、生产阻断及验证任务；附最小 TDD 修订建议、INV-01–08 和双空间纸面推演。
- 状态汇总：DEFINED 为 C-01/02/06/08/09；NEEDS_POC 为 C-03/04/05/10；BLOCKED 为 C-07。DEFINED 只表示实验契约已定义；生产身份域例外、P0 来源审核 API/角色及 PRD 字段映射仍待产品/安全/架构决策，未修改 PRD/TDD/ADR 内容或状态。
- Checkpoint 独立 saver fencing、原生 interrupt/resume、审批与外部提交、Outbox/Celery、DocReader/BrowserSkill 真协议、索引发布、无 Run Job 身份传播均未运行验证；C-10 延迟 FK/绑定规则也待真实 PG 验证。每项不变量已映射后续现有任务/故障场景，未新增 DAG 依赖。
- 最小同步 PoC README、Backlog、DAG 与执行指南的任务进展；保留历史规划/验收目标，九项 PoC 均 NOT_STARTED。未安装依赖、运行容器、写业务/实验代码、启动 PoC、暂存、提交或推送。
- 已执行 `python -`（通过 PowerShell here-string 输入，仅用 pathlib/re/subprocess/graphlib 等标准库）：10/10 契约必填要素、8/8 不变量测试映射、全仓 AGENTS/docs 的317个本地 Markdown 链接、59个成对 fenced 代码块检查通过；32任务/72边，Backlog 与权威依赖表及 Mermaid 逐边一致、拓扑无环，T02/T09/T14 前置均为已完成的 T01。零静态检查错误。
- 已执行 `git diff --check`（退出0）；`git diff --exit-code HEAD -- AGENTS.md docs/PRD_PyKnora.md docs/tdd docs/adr src pyproject.toml uv.lock references/WeKnora`（退出0）；`git diff --stat`、`git status --short` 并审阅全部文档差异。改动仅新增07文档及最小更新5份已有文档，共6文件，未暂存。新文件也另检查行尾空白（通过），不依赖 git diff 对未跟踪文件的覆盖。
- pytest/ruff/业务集成/安全/性能测试均未运行（文档任务不适用）；没有验证远端链接可达性或执行 Mermaid 渲染，本轮无新增图。纸面推演不能充当运行证据。

## 上一轮：Phase 0 技术验证规划（历史交付）

- 起始 `master` / `feb7b4a30e41a904a48b42d971d4d0844f1cf6a3`。唯一既有工作区改动为 PRD F-14 P0→P1，完整保留、不暂存；当前 PRD SHA256 为 `7DAADD701063FAB7E5DC61F9FFBDD49463397A35BB73DA2F3F01C65C4EB6C1BD`。参考仍为 `662abaf9f944e37689d04c42160034b2cdec6829`、工作树干净。
- 阅读规范/PRD/状态、三份研究、八份TDD、ADR索引和全部五份提议，及现有PoC索引；定向只读复核真实DocReader proto/main/registry和BrowserSkill接入/daemon/构建pin。没有重新全量源码审计。
- 实际新增七份规划：[00 ADR就绪度/一致性](poc/00_ADR_Readiness_Review.md)、[01 风险登记](poc/01_Technical_Risk_Register.md)、[02 九项PoC计划](poc/02_Phase0_PoC_Plan.md)、[03 任务Backlog](poc/03_Phase0_Task_Backlog.md)、[04 依赖DAG](poc/04_Phase0_Dependency_DAG.md)、[05 验收与Exit Gate](poc/05_Phase0_Acceptance.md)、[06 执行指南/下一轮提示词](poc/06_Phase0_Execution_Guide.md)，最小更新PoC README及本状态。
- ADR五份推荐状态均 Proposed：边界原则依据充分，但复合记录含未验证恢复/协议/事务/身份假设；原ADR/TDD内容及状态不变。没有现成已接受决策被擅自变更。
- 一致性评审提出C-01–C-10局部契约缺口：共享UoW中的Job/发布owner、TaskOutcome去重/转移入口、独立锁与saver连接fencing、稳定模型响应intent绑定、BSK真协议/能力、无RunJob主体策略binding、P0 URL审阅API、Outbox枚举/死信、同版本失败重试命令、asset/FK创建环。仅在规划记录最小修订建议，未扩大修改TDD/ADR。
- 风险R-01–R-13及补充R-14–R-17有评分/阻塞/PoC/任务/备选，优先五项为R-11权限传播、R-01单writer、R-02副作用、R-05版本发布、R-04可靠Outbox；R-16内容安全贯穿停线。
- 九项PoC均 NOT_STARTED；本轮明确PASS/PARTIAL/FAIL/BLOCKED/NOT_STARTED、F1–F18故障与G-DOC/G-P0/G-FULL。P0核心准入不与缺BSK设备的全量Phase0混同；有强制项PARTIAL/BLOCKED不得宣称全量PASS。
- 32个单会话任务每项都有PoC/PRD/TDD、目标/依赖/输入/预计文件/步骤/测试/量化验收/注入/复杂度/风险与备选/唯一状态责任域/集成影响/DoD；规划点数共66（不是工期）。第一项P0-T01只写契约闭合文档，下一轮提示词已给出，不在本轮执行。
- 官方能力边界只读复核LangGraph interrupt、Celery Tasks、PG锁、Playwright context；精确依赖/镜像/许可/CVE在T02/T09/T14锁定。本轮尝试打开pinned BrowserSkill upstream树被工具以restricted URL拒绝，未获真上游源码，不推断仓库不存在/协议不兼容；该材料缺口不冒充PoC已启动。

## 本轮规划检查与执行限制

- 最终静态检查通过：PoC目录8文件+本状态共9文件、111个本地链接、32任务、72条依赖；拓扑无环、Backlog/权威表/Mermaid逐边一致、波次满足前置、任务必需要素/九项NOT_STARTED/13项必需风险评分检查通过。另核49处任务→TDD章节引用、10项设计缺口与五份ADR提议原状态；零检查错误。
- 按权重并列最长链：解析线T01→T02→T04→T10→T11→T12，与可靠作业线T01→T02→T03（或T04）→T07→T08→T12同长；之后共同T13→T25→T26→T29→T31，均23点，全量加T32为24点。BrowserSkill材料/真设备等待可能改变日历关键路径，未承诺工期。
- 复用上轮系统TEMP中已存在的Mermaid 11.17.2/Puppeteer与系统Edge headless，仅渲染文档：完整32节点/72边图1/1 parse+SVG render成功，抽看截图无节点裁切。GitHub自身渲染器未运行；未安装新包/修改项目依赖，不是浏览器PoC。
- 本轮所有pytest/ruff/uv sync/docker/Celery命令都只写为未来草案，**均未运行**；只用现有Python标准库/Node做静态文档验证。当前没有真实模型/DB/队列/解析/浏览器/安全/性能/灾备测试结果。
- 静态检查脚本及SVG/截图位于 `C:/Users/unihou/AppData/Local/Temp/pyknora-phase0-plan-check-855088410cb84f10889dedaaa785b76f`，不是长期生产产物；实际命令 `python .../check_plan.py`、`python .../check_crossrefs.py`、`node .../render_plan.mjs <临时目录>`。已执行`git diff --check`（通过）、`git diff --exit-code -- src pyproject.toml uv.lock AGENTS.md docs/tdd docs/adr references/WeKnora`（退出0）、Git改动/参考SHA/参考工作树及PRD hash检查；除原有PRD修改外，仅七份新规划/PoC README/本状态变化，未暂存或提交。

## 本轮本地提交授权（2026-10-27）

- 用户明确要求提交本轮规划，并包含PRD中Wiki的P1调整；提交范围为七份PoC规划、PoC索引、本状态及PRD，共10个文档文件。
- PRD F-14从P0调整为P1已获用户明确确认，随本次提交纳入本地需求基线；上文关于“未提交/待用户处理”的记录为规划时历史状态，不再是当前待办。
- 提交前检查工作树、暂存区、PRD差异与空白；只提交上述文档，不推送远端、不变更ADR状态、不执行PoC。提交SHA通过`git log -1 --oneline`查询，不在提交中自引用。

## 下一轮（只按任务推进）

1. P0-T01 已完成文档交付。下一建议执行 P0-T02，范围以 Backlog 为准；本轮不执行。
2. T01后可并行资料预检T09/T14与环境T02，再按DAG波次执行；前置只有PARTIAL时后续限域且保留原失败，禁止删依赖制造通过。
3. 产品/安全/架构仍需确定身份域例外、来源审核字段/API、合法站点/数据权利、费用/审批核查人、精确发行物许可、质量阈值/TTL/RPO/RTO。Wiki P1 已随前轮获授权的本地提交纳入当前基线，不再待用户提交。

## 上一轮：技术设计（历史交付）

- 起始主仓库 `master` / `7b2a02ff828eb5694d90d5b88947d6694918fdab`，工作树干净；参考 HEAD `662abaf9f944e37689d04c42160034b2cdec6829`，与研究基线一致，前后工作树均干净，没有更新/切换/修改参考源码。
- 已读 AGENTS、PRD、实施状态、全部现有 ADR/TDD/PoC 索引及四份研究输入；仅针对解析 proto/TXT、resume、BrowserSkill daemon/构建、上传 Enqueue、核心/富化边界定向复读源码，不声称重做全量审计。
- 实际新增八份 TDD：[01 架构与统一契约](tdd/01_System_Architecture.md)、[02 Harness](tdd/02_Agent_Harness.md)、[03 Task DAG](tdd/03_Task_Orchestration.md)、[04 Graph](tdd/04_LangGraph_Workflows.md)、[05 Browser](tdd/05_Browser_Adapter.md)、[06 Ingestion](tdd/06_Knowledge_Ingestion.md)、[07 Data/API/Security](tdd/07_Data_API_Security.md)、[08 测试策略](tdd/08_Test_Strategy.md)，更新 TDD 索引。
- 新增 ADR-0001–0005 并更新 [ADR 索引](adr/README.md)：运行时边界、Outbox/版本发布、审批/副作用、解析/浏览器复用、空间/事件/隐私；**均为提议**，没有自动标为已接受。
- 统一 20 项必需标识、run/thread 1:1、状态拥有者、typed port/schema、工具 receipt、预算预留/结算、DAG revision、Job fencing、不可变索引及原子活动指针、最终消息/事件事务；08 逐项规定 F1–F11 的执行顺序、提交点、CAS/唯一约束、恢复触发者和最终断言。
- 官方资料核对：LangGraph interrupt/persistence、Celery Tasks（当时页面为 5.6）、Playwright context、PG 锁/RLS、pgvector、FastAPI lifespan、Pydantic strict、S3 presigned URL、OTel 敏感数据。链接位于对应 TDD，页面核验不等于已安装版本或实测结果；精确依赖锁、CVE/SBOM/许可证核验在后续任务，未改依赖。
- **期间出现的外部工作树编辑**：PRD §6 表格被非本轮工具编辑，F-14 从 P0 改为 P1，另有表格排版修改；已保留。初读冲突历史保留，TDD 按当前工作树 P1 对齐，正式需求基线待评审/提交。PRD diff 不属于本轮作者交付，不回滚、不暂存、不宣称本轮修改。该版本 PRD SHA256：`183B7BF06720BE2C650DD7A2B83B5F87F5AD9ECBEEE843DF1A6F0853449EDD77`。
- 跨文档复核已修正：URL 初次抓取先 Source/fetch Job，批准后才建文档版本；reprocess 以显式处理配置 revision 新建、同配置幂等复用；审批唯一约束仅限有效 pending/approved，过期可新建；asset bytes 与 artifact 逻辑 manifest 区分；PRD §9.9 来源审批字段细化为 review_decision_id 属显式设计提议，不与工具 Approval 混同。

## 技术设计轮验证及限制（历史）

- 使用临时目录 PowerShell/Python 检查脚本检查八份 TDD、五份 ADR、两个索引及本状态，共 **16 文件、65 个本地链接、17 个 Mermaid 块、7 个 Python 草案 AST、2 个 JSON 示例、20 必需 ID、11 类故障覆盖，零错误**；新增文件行尾空白检查通过，7 个定向复核源码路径存在。人工复核状态、接口、错误码与 PRD 映射，修正项见上文；字面覆盖检查不等于证明设计正确。脚本与 npm 验证包仅位于系统 TEMP，没有加入项目依赖或源码。
- 实际使用 Mermaid **11.17.2**、Mermaid CLI 临时依赖及系统 Edge headless 验证 17 个 Mermaid 块：**17/17 parse 与 SVG render 成功**；抽看总体组件图和 ER 图截图，未见节点裁切。GitHub 自身渲染器未运行，无法保证其版本/主题与本地完全一致。
- 已执行 `git diff --check`（通过）、`git diff --name-only`、`git ls-files --others --exclude-standard`、`git diff --exit-code -- src pyproject.toml uv.lock references/WeKnora`（退出 0）、参考 `rev-parse HEAD`/`status --porcelain=v1`（指定 SHA、干净）。仅本轮 16 个文档文件及前述外部 PRD 编辑；未修改业务源码、依赖、锁文件或参考源码，未暂存/提交。
- 实际文档验证命令：`python "$tddCheckDir/check_docs.py"`、`node "$tddCheckDir/render.mjs"`、`python "$tddCheckDir/final_check.py"`；临时目录为 `C:/Users/unihou/AppData/Local/Temp/pyknora-tdd-check-a62beb7f2085408f97570c019a6e0091`。渲染使用临时安装的 mermaid/Mermaid CLI、Puppeteer 与系统 Edge，不是业务 PoC。SVG 和两张抽检截图位于该临时目录，不作为项目生产资产或长期证据存储。
- 单元、集成、端到端、安全、性能、备份恢复均**未运行**。TDD 内命令是未来建议，百分比/时延/并发/TTL 是 PRD 目标或待验证建议，不是通过结果。

## 本地提交记录（2026-10-27）

- 用户明确要求将本轮技术设计提交到本地；提交范围为八份 TDD、五份 ADR、两个索引及本状态文件，共 16 个文档文件。
- 提交前检查工作树、暂存区、差异及空白；原暂存区为空。PRD 当前仍有外部未提交的 F-14 P0→P1 修改，明确排除在此次提交之外，保留工作区内容。
- 参考源码 SHA 仍为 `662abaf9f944e37689d04c42160034b2cdec6829` 且工作树干净；没有业务代码或依赖改动。本次仅本地提交，不推送远端；提交 SHA 通过 `git log -1 --oneline` 查询，避免提交内自引用。

## 技术设计轮未决与下一阶段（历史，当前安排见上）

1. 先评审 ADR/统一模型：空间内 Principal 与 PRD 全局 User 的身份域例外；来源审核/工具审批字段细化；P1 Wiki 当前工作树变更的正式基线。
2. 下一轮仅先形成 PoC 计划与环境/数据/阈值清单；优先 H1/H2（原生 checkpoint/审批/副作用/单 owner）和 H4/H5（五格式/图/定位、Outbox/重复投递/版本激活），H9 安全贯穿，之后 H6 检索基线。
3. H3 DAG 与 H7 真实 BrowserSkill/Playwright 为 P1 前置，H8 Research 端到端依赖它们；不能用 mock 声称真实扩展协议通过。
4. 明确精确依赖版本/镜像、Redis/MinIO/DocReader/BrowserSkill/模型权重许可、维护/CVE、身份认证、首批合法站点、全文/摘要权限、预算计费、审批人和人工核查责任。
5. 单 owner 与原生 saver 连接失效 fencing 尚未实测；若不满足约束必须单活降级。未知外部写没有去重/查询时只能人工核查；不能承诺 exactly-once。FTS/BM25、中文分词、PII 处理、TTL、RPO/RTO/迁移升级门槛均待真实证据。

## 上一轮：源码审计（历史记录）

- 开始时主仓库工作树干净，HEAD `a6220dd`；参考仓库仍为 `main` / `662abaf9f944e37689d04c42160034b2cdec6829`，工作树干净。
- 新增 [01_Source_Architecture.md](research/01_Source_Architecture.md)：Agent、BrowserSkill、DocReader、摄取、RAG/Agent Chat/SSE 的静态调用链、状态和真实路径/符号/行号，区分 A/D/N/U 证据。
- 新增 [02_Architecture_Mapping.md](research/02_Architecture_Mapping.md)：Python 模块建议及复用/重写/替换/延期依据，明确 Harness、Graph、Task DAG、Celery 边界，尚非已接受 ADR。
- 新增 [03_Compatibility_Risks.md](research/03_Compatibility_Risks.md)：协议、数据、队列、许可与风险矩阵，以及 Phase 0 必验证假设/退出条件。
- 重要修正：上下文摘要 checkpoint 与 RunSnapshot 不同；Python DocReader 不覆盖原 Go simple 路径的全部格式；普通上传未验证为统一 Outbox；attempt guard 不等于 PRD 的不可变版本索引激活；官方 ListEngines 描述与当前调用存在漂移。
- BrowserSkill 配套构建清单固定 0.3.1 / `da6bf4eed2dd7256567e152df8c903c87f6598c3`，只核验了 WeKnora 接入层和构建清单，未获取单独 upstream 实现或运行真实扩展。
- 本轮无报告编写硬阻塞；真实测试前置未落实：当前 shell 无 Go 命令，DocReader 依赖/服务、模型、数据库/队列和浏览器设备未验证可用。未运行单元、集成、安全或性能测试；不得解读成通过。
- 保留原 PRD、旧报告、AGENTS、参考源码和 Python 业务代码；本轮预期改动仅三份研究报告与此状态文件。
- 静态验证通过：PowerShell 断言检查 100 个本地链接、90 处源码行号范围、6 个成对的 Mermaid fenced 代码块；另抽查 30 个关键源码符号、参考 SHA/工作树和受保护文件未变化。新增报告无行尾空白，`git diff --check` 通过。未使用 Mermaid 渲染器验证布局，也未运行参考项目业务测试。

## 仓库现状

- 开始时主仓库分支 `master`，HEAD `c9afb8c`，工作树干净；保留原分支。
- 已有 Python 3.12+ / uv_build 项目元数据、空 README、`src/pyknora/__init__.py` 的 Hello World 入口；依赖列表为空，尚无业务 API、工作流或测试套件。
- 已有 PRD、Codex 开发指南、源码架构调研报告；全部保留，不替换已有结论。
- `references/WeKnora` 已存在官方 origin 和完整 Git 元数据，工作树干净。基线 `main` / `662abaf9f944e37689d04c42160034b2cdec6829`；详见 [源码基线](research/00_Source_Baseline.md)。

## 初始化记录（已完成，提交 a6220dd）

- 阅读 PRD、开发指南和已有研究；检查已有文件、主仓库和参考仓库状态。
- 新增根 `AGENTS.md`，约束职责、安全、幂等/版本、测试与每轮交付。
- 建立 `docs/adr/`、`docs/research/`、`docs/tdd/`、`docs/poc/`，用 README 保证目录可被 Git 跟踪并说明用途。
- 记录参考仓库 commit/branch/tree、版本区别、许可证及第三方通知。
- 补充已有 gitlink 缺失的 `.gitmodules`，保留源码 SHA 与忽略规则；未重新克隆、fetch 或更改原源码。

## 初始化验证记录

- `git status --short`、`git ls-files --stage references/WeKnora` 及参考仓库 `rev-parse` / `branch` / `remote` / `describe`：确认初始工作树干净、来源与指针一致。
- 文档路径、相对链接、Markdown fence 成对、规范必需项与基线一致性：PowerShell 断言检查通过（8 个新增文件），原有业务与文档文件无改动，参考源码工作树仍干净。
- `git diff --cached --check`：提交前检查空白错误。
- 单元/集成/性能/安全测试未运行：本轮只改文档与 Git 元数据，尚无业务实现或测试套件。未验证真实 DocReader、BrowserSkill、LangGraph 或 Celery 集成。
- 本轮变更以本地文档初始化提交交付；通过 `git log -1 --oneline` 查询提交，避免在同一提交内自引用 SHA。

## 待办（不在本轮实施）

1. 已交付固定 commit 的三份静态审计报告；后续补充运行与故障注入证据，并继续核验未覆盖分支及外部 BrowserSkill 实现。
2. 已交付 TDD 与 ADR 提议；后续完成评审、PoC 验证及 ADR 接受流程，不把提议当已通过决策。
3. 九项 Phase 0 PoC、32任务Backlog、验收门槛和环境规划已交付；后续按P0-T01及依赖DAG逐项执行，优先验证Checkpoint/审批恢复、DocReader与可靠发布。
4. 按任务依赖逐项实现并记录真实测试证据，之后再进入 P0 业务闭环。

## 阻塞、风险与待澄清

- **本轮无硬阻塞**：源码已在本地，未发生网络获取失败；远端最新状态和全新 submodule 下载未测试。离线恢复方式见源码基线文档。
- **基线差异**：PRD 以 v0.8.3 作参考，实际 clone 为其后 202 个提交；现有报告的 tree SHA 不是本轮 commit，不可默认沿用全部结论。
- **需求冲突历史及当前变化**：初读 PRD §6 F-14 为 P0、其他章节为 P1；本轮期间检测到外部未提交编辑已将 §6 改成 P1，保留该工作并按 P1 设计，正式基线待确认。
- **许可证**：MIT 不覆盖全部第三方；DocReader/OCR、BrowserSkill 协议及分发约束未完成审计。PyKnora 自身许可待确定。
- **集成与容量**：数据库、Redis/Celery、对象存储、模型、浏览器设备、观测栈可用性尚未验证；性能数值与安全验收仍是目标，不是已达成结果。
