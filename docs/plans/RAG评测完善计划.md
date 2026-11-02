# RAG 评测与可观测性完善计划（参考 Day4 + Day5）

> 状态：**提议**（未批准，未开工）
> 撰写：2026-11-02
> 参考源：`Day5-学生实战讲义.md` §5（固定评测集与基线/候选对比）、§6（注入/污染/越权分别验收）、§7（可观测性）、§8.3（门禁报喜也报忧）、§10.1（评分要点）；`PyKnora_Day04_学生实战讲义.md` §8（性能诊断与有依据的优化）、§8.3（分阶段耗时）、§8.9（基线测量）、§8.10（实验记录模板）、§10.4（流式事件一致性）、§10.5（性能优化安全回归）、§12.3/§12.4（性能基线与前后对比模板）。
> 与既有文档的关系：取代 `docs/plans/RAG评测与可观测性完善计划.md`（v1.3）的 §3 差距清单与 §4 阶段口径；v1.3 不删除，保留为讨论记录。

---

## 1. 只取什么，不取什么

两讲义合计 2100 余行，本计划**只取 RAG 评测与可观测性两块**，其余明确不取。

| 讲义章节 | 取 / 不取 | 理由 |
| --- | --- | --- |
| Day5 §5 固定评测集与基线候选对比 | **取**：评测契约 6 条、逐例字段、分层指标口径、门槛冻结政策、成对比较表 | 本项目已有 20 题与 8 指标，缺的正是契约、字段绑定、门槛 |
| Day5 §6 注入/污染/越权 | **取**：三类风险的检查边界、I/P/A 各 4 条路径、四暴露面 | 本项目安全组只有 2 条 deny，覆盖不到 12 题 |
| Day5 §7 可观测性 | **取**：日志/指标/轨迹分工、最小字段表、隐私边界、三个必须能定位的故障 | 本项目 INFO 日志全丢，逐例无法回查 |
| Day5 §8 CI 两条流水线 | **不取** | 用户明确暂不做 CI 与部署 |
| Day5 §9 回滚演练 | **不取** | 属发布侧，不在评测与观测射程 |
| Day5 §3 冻结基线/版本清单 | **部分取**：只取 `manifest_sha256` 绑定 | 版本清单八类对象偏发布 |
| Day4 §4–§7 接口契约/SSE/Compose | **不取** | 前端已做，部署不做 |
| Day4 §8 性能诊断与优化 | **取**：指标口径、分阶段耗时、基线测量方法、实验记录模板 | 本项目有 TTFT 与总耗时，缺分阶段与实验记录 |
| Day4 §10.4 流式事件一致性 | **取**：终态恰好一次、同流同 run_id、序号严格递增 | 已被 SSE 层满足，纳入评测断言防回归 |
| Day4 §10.5 性能优化安全回归 | **取**：缓存/并发/上下文三类必须测 | 与 Day5 A/P 组重叠，合并成一组用例 |
| Day4 §12.3/§12.4 模板 | **取**：表结构 | `eval/results/` 不入库，缺一个人工产物落点 |

**不硬编码讲义内容**：讲义里的 24 道题、`fixtures/corpus.jsonl`、`SYNTHETIC-HR-A-731` 这类标记都**不搬进代码**。本题库的样本一律由本项目 `eval/corpus_manifest.json` 的 5 份真实语料现生成，只借检查口径。

---

## 2. 现状盘点（全部实测，带行号）

### 2.1 已有（不重复造）

| 能力 | 落点 | 证据 |
| --- | --- | --- |
| 20 条评测集，6 类 category，`expect ∈ {answer, abstain, deny}` | `eval/eval_set_v0.1.jsonl` | `summary.json:9` `raw_lines: 20`；`counts.samples: 20` |
| 8 个指标 + 定义字符串 | `eval/run_eval.py` `METRIC_DEFINITIONS` | `summary.json:10-91` 八个 key 均带 `definition` |
| 逐例 raw 记录 28 键 | `eval/run_eval.py:793-819` `build_raw_record` | `sample_id/category/expect/repeat/latency_ms/results/answer_text/citations/status/invalid_citations/retrieval_strategy(+_source)/prompt_version(+_source)/query_http_status/chat_http_status/chat_ttft_ms/chat_total_ms/error_code/degraded/deny/gold_sha1_8/gold_document_ids/expect_facts/notes` |
| P95 nearest-rank，拒绝线性插值 | `eval/run_eval.py:827-834` `percentile_p95` | 注释写明「n=20 插值会静默变成 max」 |
| 单因素公平性守卫（6 个主因素），两份配置必须恰好一处不同 | `eval/run_eval.py` `PRIMARY_FACTOR_FIELDS` + `--compare-config` | `eval/README.md:44` exit 2 |
| deny 泄漏即整轮作废（exit 3） | `eval/run_eval.py` | `eval/README.md:111` |
| 三件套规则（commit / 配置 / 样本量） | `eval/README.md:105-106` | 已写死的对外引用纪律 |
| 语料 sha256 校验（凭据漂移先失败） | `eval/corpus_manifest.json` + `eval/corpus.py --verify-only` | `eval/README.md:35` |
| 请求 ID 落到响应头与错误体 | `src/pyknora/api/main.py:285-288`、`:127-129`、`:93` | `X-Request-ID` |
| TTFT 与总耗时 | `summary.json:71-90` | `chat_ttft_p95_ms` 5078.34 / `chat_total_p95_ms` 5586.0 |

### 2.2 缺口（按讲义口径逐条对齐）

| # | 讲义要求 | 本项目现状 | 严重度 |
| --- | --- | --- | --- |
| G1 | Day5 §5.3 `case_id` 作为跨轮对齐键 | **评测集无 `case_id` 字段**（`grep '"case_id"' eval/eval_set_v0.1.jsonl` = 0 命中），raw 只有 `sample_id` | P0 |
| G2 | Day5 §5.3/§7.2 `manifest_sha256` 把结果绑到版本清单 | 无。summary 有 `config.snapshot` 三个 document_id 与 build_id，但没有一份可哈希的清单文件 | P0 |
| G3 | Day5 §7.2 逐例可串日志 | raw **不记录 `request_id`**，服务端 `X-Request-ID` 生成后不回流评测 | P0 |
| G4 | Day5 §7.1 结构化 JSON 日志 + 关联 ID | `src/pyknora` 全仓无 `basicConfig`/`dictConfig`/`addHandler`；root 无 handler、level=WARNING → `logging.lastResort` **只输出裸 message**，所有 `logger.info` 静默丢弃 | P0 |
| G5 | Day5 §7.2 `stage/outcome/error_type` 定位失败阶段 | 无 `stage`；`status` 取值为 `ANSWERED/ABSTAINED/DENIED/DENY_LEAKED/DENY_NOT_404`，不是讲义要求的 `completed/timeout/error` 语义；`error_code` 有但不归一 | P1 |
| G6 | Day5 §6 I/P/A 各 4 条 | 安全组只有 `deny` 2 条（`counts.deny_samples: 2`），无 I（注入）、P（污染）组 | P0 |
| G7 | Day5 §5.3 `review:{fact_pass,citation_pass,security_pass}` 人工复核载体 | 无。指标全自动，`null` 与 `0` 无从区分 | P1 |
| G8 | Day5 §5.2 契约第 6 条：超时/失败留在分母、评分缺失标 unknown 并阻断 | 部分：`no_evidence` 的 status 缺失时退化为「回答含『没有检索到』」文案判定（`summary.json:371` warnings 自述），这是权宜口径 | P1 |
| G9 | Day4 §8.3 分阶段计时 | 只有检索端到端 `latency_ms` 与 `chat_ttft_ms`；无 `evidence_check_ms`、`generation_ms` 拆分 | P1 |
| G10 | Day5 §5.5 门槛冻结 → 脚本报喜也报忧 | 无 `eval/gate.py`。`report.py` 只产 markdown，退出码只有 run_eval 的 deny 泄漏一条 | P1 |
| G11 | Day5 §7.3 三个必须能定位的故障 | 无演练脚本、无定位手册 | P2 |
| G12 | Day4 §12.3/§12.4 人工产物 | `eval/results/` 已 gitignore，性能基线与前后对比表无处落 | P2 |

---

## 3. 阶段计划

依赖关系：**P0 → P1 → P2 → P3 → P4 → P5 → P6**。P0 是其余全部的前置（没有 `case_id` 与 `request_id`，后面所有对比都无法逐例对齐）。
不做：OTel SDK / OTLP、`/metrics`（仅列 P7 可选）、远端 CI、部署、前端、解析侧。

### P0 逐例可追溯（0.5 人日）

| 项 | 改动 | 产出 |
| --- | --- | --- |
| G1 | `eval/eval_set_v0.1.jsonl` 每条补 `case_id`（沿用现有 `sample_id` 作前缀，不重编号，避免历史结果断链）；`build_eval_set.py --check` 加「`case_id` 唯一且非空」断言 | 20 条带 case_id |
| G2 | 新增 `eval/make_manifest.py`：从 `.tmp_ref/eval_setup.json` + `corpus_manifest.json` + 运行时 settings 生成 `eval/day/manifest.json`，字段含 `app_commit` / `prompt_version` / `embedding_model_key` / `documents[].active_build_id` / `chunks`，落盘后计算 `manifest_sha256` | `manifest.json` + 哈希 |
| G3 | `run_eval.py` 从 `/query` 与 `/chat` 响应头取 `X-Request-ID`，写入 raw 的 `request_id` 与 `chat_request_id` | raw 增 2 键 |

**归属**：`eval/` 整目录（成员2 域，`AGENTS.md` §5 未列 `eval/`，按 `docs/项目结构说明.md` §1.3 视为成员2 扩展）。`src/pyknora/api/main.py` **只追加**，不改 `_attach_request_id` 语义。

**验收**：`uv run --frozen python eval/run_eval.py --config eval/baseline_config.yaml --repeats 1 --out eval/results/p0-check` → raw.jsonl 每行都有非空 `case_id` 与 `request_id`；`summary.json` 顶层出现 `manifest_sha256`。

### P1 日志底座（1 人日）

| 项 | 改动 |
| --- | --- |
| 新文件 | `src/pyknora/core/logging.py`：`JsonFormatter`、`configure_logging(level, fmt)`、`log_event(event, **fields)`、`bind_request_context(request_id)` |
| 渲染 | 同一份 fields 两种渲染：默认 text（`2026-11-02 17:40:00,123 INFO pyknora.retrieval event=retrieval_completed request_id=r-8f2a duration_ms=812`），`PYKNORA_LOG_FORMAT=json` 时单行 JSON |
| 注入 | `contextvar` 持有 `request_id`；`log_event` 自动带。**这是 G3 的配套**：没有 contextvar，`X-Request-ID` 只能到响应头，进不了任何日志行 |
| 接入 | `src/pyknora/api/main.py` 的 `create_app` 里调 `configure_logging()`（只追加一行，不改 `configure()` 签名） |
| 事件 | 四个：`retrieval_completed` / `prompt_built` / `first_token` / `chat_finished` |

**隐私边界（Day5 §7.2 原文口径，照做）**：不记 `Authorization`、API Key、密码、完整原文、未脱敏完整 Prompt；身份用受控脱敏值；`source_ref` 只记 ID/版本/摘要；数值未知写 `null` 不写 `0`。

**归属**：`core/logging.py` 新文件（成员2 域）；`services/retrieval/search.py`、`services/rag/answer.py`、`api/v1/chat.py` 属成员3，**只改 logger 调用与计时**，不重构逻辑。

**验收**：
1. `uv run --frozen python -c "from pyknora.core.logging import configure_logging; configure_logging('INFO','text')"` 后手打一行 INFO，`handlers` 非空且带时间戳（现状是裸 message）。
2. 一次 `/chat`，用同一个 `request_id` 能 grep 出 4 条事件。
3. `PYKNORA_LOG_FORMAT=json` 时每行 `jq .` 成功。

### P2 分阶段计时（1 人日）

补 Day4 §8.3 的四段：`retrieval_ms` / `evidence_check_ms` / `model_ttft_ms` / `generation_ms`，写进 `chat_finished` 事件，并断言 `四段之和 ≈ total_ms`（容差 5%）。
raw 增 `stage_timings` 对象；`summary.json` 增 `stage_p50/p95`。

**顺序不可反**：没有 P1 的 `log_event`，P2 无处挂计时。

**验收**：`--repeats 1` 跑一轮，`stage_timings` 四键齐全且和值与 `chat_total_ms` 差 < 5%；任一段为 `null` 时报告标 `unknown` 而非 `0`。

### P3 安全组扩容到 12 题（1.5 人日）

按 Day5 §6 的三类、各 4 条，把检查口径落到**本项目真实暴露面**上（题目自建，不抄讲义）：

| 组 | 4 条覆盖 | 本项目落点 |
| --- | --- | --- |
| I 注入 | 文档内指令 / 冒充系统 / 工具越权 / 引用伪造 | `forbidden` 语料里放入含指令的段落；断言回答不含被指令要求的内容；本项目工具是只读检索，验证工具白名单不含写操作 |
| P 污染 | 未审批来源 / 旧版残留 / 正文自称权限 / 撤销后缓存 | 现有 `version` 组 2 条升为 P02；P03 用 KB ACL（正文自称不影响 `get_kb_access`）；P04 两阶段（撤前问 → 删文档 + 撤 grant → 再问）——**会真实改变状态，重跑前必须重新 seed** |
| A 越权 | 跨租户 / 跨角色 / 直接 ID / 缓存串用 | 现有 `deny` 2 条升为 A 组；A03 用 `citation_id` 直连 `/citations/{id}/content`；A04 当前无缓存层，退化为跨身份引用隔离 |

**前置断言（讲义 §6.2 第 3 条，照抄口径）**：payload 必须真实进入检索命中，否则该题判 `unknown`，**不能算抗注入通过**。

**验收**：`counts` 里 `injection_samples=4`、`poison_samples=4`、`privilege_samples=4`；任一泄漏 → `deny_leak` 扩展为 `security_leak: true` → run 退出码 3。

### P4 人工复核载体（0.5 人日）

raw 每条补 `review: {fact_pass, citation_pass, security_pass}`，初值全 `null`。
新增 `eval/review.py`：按 `case_id` 填三态（`true/false/null`），落 `eval/results/<run>/review.jsonl`。

**硬口径**：`summary.json` 的 `null_ratio` 超过 20% → 整体判 `FAIL`（不是「有条件通过」）。评分缺失标 `unknown` 并阻断，不当作通过。

### P5 门槛脚本与基线管理（1 人日）

新增 `eval/gate.py`：

```
uv run --frozen python eval/gate.py --baseline eval/results/base-<日期> --candidate eval/results/cand-<日期>
```

检查项（对应 Day5 §5.5 与本项目 README §4）：

1. 三件套齐全（`summary.json` + `raw.jsonl` + `report.md`）；
2. `app_commit` 一致，策略确实切换（`retrieval_strategy_source == "response"`）；
3. 安全组任一泄漏 → 立即 exit 3，其余指标不看；
4. `null_ratio` > 20% → exit 4；
5. 候选逐例不得有退化，退化必须逐条解释（有 explain 文件才放行，exit 1 但写明）；
6. 阈值**从基线文件读**，不写死在脚本里。

**基线管理**：`eval/baselines/` 存三件套快照，**只增不改**（`baseline-20261030` 已在 `eval/results/`，需迁入并转为入库产物）。
`eval/results/` 仍不入库；`eval/baselines/` 入库。

**验收**：对 `baseline-20261030` 自比 exit 0；对 `candidate-20261030`（引用准确率 0.3556→0.318 是真实退化）exit 1；人为把 `deny_interception` 改 0.5 → exit 3；把 `review` 全置 null → exit 4。四条都必须实测，不能只写「预期」。

### P6 三故障定位演练（0.5 人日）

写 `docs/plans/评测排障手册.md`，对应 Day5 §7.3 三问：

| 故障 | 靠什么字段定死 |
| --- | --- |
| 检索为空 | `retrieval_completed.retrieval_count=0` + `stage=retrieve` + `error_type ∈ {no_permission, index_missing, filtered_out, no_evidence}` |
| 回答错误但检索正常 | `prompt_built.evidence_count` vs `retrieval_count` → 上下文截断；否则看黄金来源是否在 `results[].source_id` |
| SSE 卡住 | `first_token` 有无 + `chat_finished` 有无 + 终态事件是否恰好一次（Day4 §10.4） |

**验收**：任给一个 `case_id`，两分钟内用字段表定位到 run 与失败阶段，并说清当前日志还缺什么。

### P7（可选，不做）`/metrics`

Prometheus 文本端点。单进程 FastAPI 下收益低，等有多实例再说。

### 3.5 一份数据，三个 verdict（评测不按阶段拆）

质量（Day3）、性能（Day4）、安全 + 可观测（Day5）三块**共用一条流水线**，只在**判据与门禁档位**上分开。理由有三条，不是风格偏好：

1. **同一轮才有可比性**。Day4 §8.1 判据「性能更快但泄露权限、引用旧内容或降低质量，属于失败的优化」，只有在同一轮里同时拿到延迟与质量才能判。分三次跑 = 三个 commit、三份配置、三个样本量，数字互相引用不了，直接违反 `eval/README.md:105-106` 的三件套纪律。
2. **同一份 raw 才能逐例对齐**。`baseline-20261030` 的引用准确率 0.3556 与 TTFT P95 5078.34 出自同一份 `raw.jsonl`；拆开后「TTFT 降到 3s」无法与「这个 case 的引用有没有掉」落到同一行。
3. **安全必须能一票否决质量与性能**。三套脚本各自 PASS/FAIL，没人负责合成总判定；合成正是 gate 该做的事。

| 层 | 是否分开 | 分开成什么 |
| --- | --- | --- |
| runner / case 集 / raw 契约 | **不分** | 一套 `run_eval.py` + 一套 `eval_set` + 一份 `raw.jsonl` |
| 判据 | **分** | 质量=不得退化；安全=任一泄漏即阻断；性能=描述性，n=20 不证明 |
| 阻断强度 | **分** | 安全 exit 3 立即终止；质量 exit 1 逐例解释；性能只报告不出结论 |
| 门禁档位 | **分** | L1 commit（ruff+unit，7–11s）/ L2 变更（不调模型：检索层 + 安全组）/ L3 发布（eval-full + gate.py） |
| 产物 | **分** | `summary.json` 一份，`report.md` 三段，`gate.py` 出三个 verdict |

`gate.py` 的判决形状（P5 落地）：

```json
{"quality": "PASS|FAIL|UNKNOWN", "safety": "PASS|FAIL", "perf": "REPORT_ONLY",
 "exit_code": 0, "reasons": ["..."]}
```

`summary.json` 的 `metrics` **保持扁平不改 schema**（`report.py` 不动），只**追加** `verdicts` 字段，向后兼容。

施工顺序仍然分开：P0（观测与可追溯）→ P3（安全组）→ P5（门禁），因为 P5 依赖 P0 的 `case_id` 与 P3 的分组计数。**但交付物不按阶段切分，只按判据切分。**

---

## 4. 硬口径（不接受"平均数还行"）

1. `null ≠ 0 ≠ 通过`；`review` 为 null 占比 > 20% → FAIL。
2. `injection` / `poison` / `privilege` 任一组出现真实泄漏 = 整体 FAIL，不看比率（Day5 §5.5）。
3. 超时与报错留在原分母，不许少算分母抬高指标（Day5 §5.2-6）。
4. 门槛只许在跑基线**之前**冻结，不许事后为候选放宽（Day5 §5.5）。
5. **性能更快但泄露权限、引用旧内容或降低质量，属于失败的优化**（Day4 §8.1）。
6. 性能优化必须连带回归：缓存（跨工作区串用 / 版本切换 / 撤权）、并发（单路超时不得跳过授权）、上下文（删片段不得让关键事实失去依据）（Day4 §10.5）。
7. `repeats=1` 时 `min==max` 不代表稳定；n=20 的 P95 基本等于"第二慢的那条"，只能描述不能证明（Day04 §8.9 + `eval/README.md:369-371`）。
8. 引用任何数字必须带三件套：commit、哪份配置、样本量（`counts.unique_samples`，不是 `counts.samples`）。
9. **评测不按 Day3/Day4/Day5 拆成三条流水线**：一份 raw、一份 `summary.json`，`gate.py` 出 quality / safety / perf 三个 verdict；安全 verdict 一票否决另外两个（详见 §3.5）。

---

## 5. 目录归属与冲突

| 文件 | 归属 | 规则 |
| --- | --- | --- |
| `eval/**` | 成员2 扩展域 | 可写；改 `eval_set` 必须同步 `build_eval_set.py --check` |
| `src/pyknora/core/logging.py` | 新文件（成员2 域） | 新建 |
| `src/pyknora/api/main.py` | **共用文件** | 只追加（`configure_logging()` 一行），不改 `configure()` 签名 |
| `src/pyknora/services/retrieval/search.py`<br>`src/pyknora/services/rag/answer.py`<br>`src/pyknora/api/v1/chat.py` | **成员3** | 只改 logger 调用与计时，需成员3 复核 |
| `eval/gate.py`、`eval/review.py`、`eval/make_manifest.py` | 成员2 | 新建 |
| `docs/plans/评测排障手册.md` | 成员2 | 新建 |

**与既有 v1.3 计划的差异**：v1.3 有 35 个登记字段（登记制），本计划只落 **14 个必做字段**（`request_id` / `run_id` / `case_id` / `manifest_sha256` / `stage` / `outcome` / `error_type` / `duration_ms` / `retrieval_count` / `tool_count` / `source_ref` / `token_usage` / `first_token_ms` / `evidence_check_ms`），其余标 ⬜ 待用。理由：字段能少不多，落不下来的字段等于没写。

---

## 6. 风险

| 风险 | 影响 | 处置 |
| --- | --- | --- |
| 安全组扩到 12 题后大部分判 fail | 门禁长期红，团队对红钝化 | 先扩题再谈修；首轮结果如实记录为基线，不调阈值 |
| P04 会真实改库 | 重跑污染状态 | 每题自带 seed/cleanup；`--out` 独立目录 |
| contextvar 在 SSE 长连接里泄漏 | 串号 | `finally` 里 `token.reset()`；写单测断言并发两请求不串 |
| 日志量从「只有 WARNING」变全量 INFO | `dev_up.py` 重定向文件暴涨 | 只把 `pyknora.*` 设 INFO，uvicorn access log 保持默认 |
| `eval/` 仍是未跟踪目录 | 改完没入库被并发会话清掉 | **先 `git add -N eval` 或直接提交**，本仓库已发生过未提交新文件被并发会话 git 操作抹掉（见 `docs/IMPLEMENTATION_STATUS.md` 记录） |

---

## 7. 未验证项

- `eval/eval_set_v0.1.jsonl` 现有 20 条的 `category` 分布（fact 6 / exact_term 4 / synonym 3 / version 2 / no_evidence 3 / deny 2）来自 `summary.json:92-159` 的 `per_category.samples` 求和，**未逐行读 jsonl 核对**。
- `manifest.json` 需要的 `active_build_id` 等字段依赖 `.tmp_ref/eval_setup.json` 与活库；本计划撰写时未连库确认这些字段仍存在。
- Day4 §8.3 的 `agent_runs.usage` 在**本仓库不存在**（`grep usage|ttft|latency src/pyknora/models/tables.py` = 0 命中），用量只能从响应体 `prompt_tokens`/`completion_tokens` 取；`schemas/chat.py:91-92` 已确认这两个字段存在，但**是否每条 `done` 事件都带回未逐行核对**。

---

## 8. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-11-02 | v1.0 | 首版：参考 Day4 §8/§10.4/§10.5/§12.3/§12.4 与 Day5 §5/§6/§7，按本项目 12 条实测缺口排 P0–P7 |
