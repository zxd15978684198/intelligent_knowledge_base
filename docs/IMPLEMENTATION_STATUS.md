# PyKnora 实施状态

更新日期：2026-10-28（Asia/Shanghai）。**2026-10-28 追加：数据契约外键落地，见下方「当前轮」**；其后的 Phase 0 / P0-T01 章节保留为其自身时点的历史记录。

## 当前轮：数据契约补外键（2026-10-28，lead）

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
