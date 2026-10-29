# PyKnora 实施状态

更新日期：2026-10-29（Asia/Shanghai）。**2026-10-29 追加（最新）：任务 16 重建索引 `/admin/reindex` 完成（成员5 实现 / lead 复核），见下方「本轮：任务 16 重建索引」**；再往前是 B0 环境对账 + B2 主链路烟测（「本轮：B0 环境对账 + B2 主链路烟测」）、合并远端 chat 分支 + 修 `index_migrations` 主键口径（「本轮：合并远端 chat 分支」）；更早的成员2 两条 P0 修复、成员2 四任务收口、成员4 验收闭环、成员1 S05、身份基座落地、数据契约外键、Phase 0 / P0-T01 章节保留为其自身时点的历史记录。

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
