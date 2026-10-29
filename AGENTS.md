# AGENTS.md · PyKnora 协作与进度规范

> 适用于在本仓库工作的所有 Agent（Codex / Claude Code / 其他）与人类成员。
> **分工**：本文件管**流程与纪律**；`docs/PRD_PyKnora.md`、`docs/tdd/`、`docs/adr/` 管**技术口径**。
> 两者冲突时，技术口径以**最新 ADR** 为准，流程以本文件为准，并把冲突记进 `docs/IMPLEMENTATION_STATUS.md`。
> 仓库现状见 [docs/项目结构说明.md](docs/项目结构说明.md)，五分钟能看懂目录与归属。

---

## 0. 四条硬规则

1. **每完成一个阶段任务，必须更新进度文件**（见 §3，含清单与模板）。没更新 = 这个任务不算完成。
2. **先读再写**（见 §1）。未明确要求时，不重构、不删已有功能、不改参考源码、不动别人的目录。
3. **证据先于结论**（见 §6）。没跑过的命令不写"通过"；没做过的验证写"未验证"。
4. **每次提交前必须过本地门禁**（见 §6.1）。钩子已在 `git commit` 时自动执行；门禁未通过不许提交。
   跳过只能用 `git commit --no-verify`，且必须在提交说明里写明原因；**连续两次跳过视为违规**。

违反 1、3、4 的交付，一律按未完成处理并回退。

---

## 1. 开工前必读（按顺序，约 15 分钟）

| 顺序 | 文档 | 读它做什么 |
| --- | --- | --- |
| 1 | [docs/项目结构说明.md](docs/项目结构说明.md) | 目录树、每个目录谁负责、共用文件冲突规则、8 个已知坑 |
| 2 | [docs/plans/全员模块实现路径.md](docs/plans/全员模块实现路径.md) | 五个模块的边界、S01–S16 施工步骤、契约 C1–C6、验收断言 A1–A6 |
| 3 | [docs/plans/成员任务进度表.md](docs/plans/成员任务进度表.md) | 17 个任务的编号/负责人/前置/当前状态（**进度以此表为准**） |
| 4 | 自己模块的计划文档（成员2：[docs/plans/成员2执行计划.md](docs/plans/成员2执行计划.md)） | 半天颗粒度日程与文件骨架 |
| 5 | [docs/adr/](docs/adr/README.md) 与 [docs/tdd/](docs/tdd/README.md) | 技术决策——**注意：五份 ADR 全部仍是"提议"状态** |
| 6 | [docs/PRD_PyKnora.md](docs/PRD_PyKnora.md) | 需求口径；优先级不得静默改动 |

只读、只分析、只规划的任务，也先跑一遍 `git status --short`，保护用户未提交的工作。

---

## 2. 项目事实（别猜、别外推）

| 项 | 事实 |
| --- | --- |
| 语言与包管理 | Python 3.12，`uv`（依赖只写在 `pyproject.toml`，锁文件 `uv.lock` 勿手改） |
| Web | FastAPI + pydantic v2；应用工厂 `pyknora.api.main:create_app` |
| 数据 | PostgreSQL 16 + `pgvector`；SQLAlchemy 2 async + `asyncpg` |
| 明确**不用** | Redis、Celery、alembic、lxml（队列=PG 表 + `SKIP LOCKED`，迁移=手写数字号段 SQL） |
| 本地库 | 容器名与端口以 `.env` 的 `PYKNORA_DB_CONTAINER` / `PYKNORA_DATABASE_URL` 为准；契约默认 `pyknora-pg`，宿主端口 **5433**，库名/用户名 `postgres`，密码 `dev`。**别人的机器上常见 5434 + `pyknora-pg-p0`，先 `docker ps` 再改 `.env`，别改代码默认值** |
| 库结构 | `migrations/0001_init.sql` 建 **9 张表 + 1 个视图**；`0001–0005` 合计 **16 张表 + `v_active_chunks`**；ORM 只映射 15 个 Row（`outbox` 走原生 SQL、`index_migrations` 走 repo 内的 SQLAlchemy Core） |
| 错误码 | `src/pyknora/core/errors.py` 的 **17 个** `ErrorCode`；响应体统一 `{"error":{code,message,retryable,details},"request_id"}`。其中 `CONFLICT`/`CURSOR_EXPIRED`/`DEPENDENCY_UNAVAILABLE`/`INTERNAL_ERROR` 是实现期新增、`docs/tdd/07` 原表未列，已在该文 §7 登记为**提议** |
| 测试 | `tests/unit`（88，不需要数据库）、`tests/contract`（20）、`tests/api`（80，ASGI 打真 PG）、`tests/integration`（48，需真 PG）；合计 236 |
| 本地门禁 | `.githooks/pre-commit` → `scripts/ci.py --stage all`；激活命令 `git config core.hooksPath .githooks`（见 §6.1） |

**两套编号，别混**：`S01–S16` 是实现步骤（计划书），`1–17` 是课件任务编号（进度表/xlsx）。写进度时两个都写清楚。

---

## 3. 进度更新协议（本文件的核心）

### 3.1 什么算"一个阶段任务"

满足任一即为一个阶段任务，**一次会话只推进一个**：

- 计划书里的一个施工步骤 `S##`（S01–S16）；
- 进度表里的一个任务编号（1–17）；
- 一个跨模块契约 C1–C6 的落地。

跨多个阶段（"把成员1 到成员5 都做完"）必须由用户明确要求，且逐个阶段交付、逐次更新。

### 3.2 完成后必须更新的文件

| # | 文件 | 改哪一节 | 改什么 | 何时 |
| --- | --- | --- | --- | --- |
| 1 | `docs/plans/成员任务进度表.md` | 文首「最近更新」+ §1 进度总览 + §3 主看板 + §9 更新记录 | 状态、进度百分比、总览计数、一行变更记录 | **每次必改** |
| 2 | `docs/IMPLEMENTATION_STATUS.md` | **顶部新增「本轮：<任务>」段** | 任务号/起始 HEAD/改动文件/命令与真实结果/未验证项/下一步 | **每次必改** |
| 3 | 自己的模块计划（成员2：`docs/plans/成员2执行计划.md`） | 对应 `S##` 条目标记 | 完成标记 + 与原计划的偏差 | 成员2 必改 |
| 4 | `docs/adr/` 或 `docs/tdd/` | 新建或修订 | 表结构、错误码、跨模块契约、可见性规则有任何变化时 | 改契约/改表时**必改**，且状态只写"提议"直到用户接受 |
| 5 | `README.md` | 快速开始 / 目录表 | 出现新命令、新入口、新目录时 | 按需 |
| 6 | `docs/plans/*.xlsx` | 对应表 | 进度表的源头（§0 声明 xlsx 为准） | 改了任务/排期才动；改完同步回 §3 |

**只改自己那一行**：主看板里别人的任务、别人的状态，一律不动。

### 3.3 唯一合法取值

- 状态：`未开始` / `进行中` / `已完成` / `阻塞`
- 进度：`0` / `25` / `50` / `75` / `100`（五档，不写 37% 这种假精度）

**「已完成」必须四件事同时成立**：

1. 代码或文档已落盘，`git status` 里能看到；
2. §6 的验收命令真跑过，输出贴进状态文件；
3. 主看板 `状态=已完成` 且 `进度=100`（两者不许自相矛盾）；
4. 没有把"未验证"藏在心里——写进状态文件的"未验证项"。

状态改 `阻塞` 时：必须在进度表 §8.1 追加一行（阻塞原因 + 谁能解 + 预计解除时间）。

### 3.4 可复制的模板

**（a）主看板行（只改后两列）**

```markdown
| 4 | 文档切片与 Embedding | 成员2 | W3 | 3 | 成员3 | 切片流水线 + 向量写入 | 已完成 | 100% |
```

**（b）`IMPLEMENTATION_STATUS.md` 顶部段**

```markdown
## 本轮：S09 解析与切片（2026-10-29，成员2）

- 任务：S09 ↔ 进度表任务 4；起始 HEAD `<sha>`，`git status` 起始为干净/仅本轮改动。
- 改动文件：`src/pyknora/services/ingest/parser.py`、`splitter.py`、`tests/unit/test_parser_splitter.py`。
- 已执行：`uv run ruff check src tests scripts` → All checks passed；`uv run pytest -q tests/unit` → 27 passed。
- 未验证：3 页 PDF 的真实 `page_no` 抽取（需 `tests/integration` + docker PG）；本地无 psql。
- 下一步：S10 embedding 双后端；不跨阶段。
```

**（c）提交信息**

```
feat(m2): S09 解析与切片（任务4）— 3 页 PDF chunk≥3，char_start<char_end
```

### 3.5 进度纪律（会被评审打回的做法）

- 把"没跑"写成"通过"，或把 PoC 的 `NOT_STARTED` 改成 `PASS`；
- 状态还是 `进行中` 却把进度写成 `100%`，或反之；
- 只改 `成员任务进度表.md` 而不更新 `IMPLEMENTATION_STATUS.md`（两份必须同步）；
- 在别人的任务行上改状态；
- 只改 md 不改 xlsx（或反过来），导致两处口径不一致；
- 把"提议"的技术决策写成"已定"。

---

## 4. 代码红线（评审直接打回）

| # | 不变量 | 落地位置 |
| --- | --- | --- |
| I1 | 活动版本由视图保证：读 chunk **必须**走 `v_active_chunks`，不许绕开写 SQL | `migrations/0001_init.sql:201-234` |
| I2 | `Scope` 只从 JWT 来；`scope.owns(None) == False`；请求体/查询串里的身份字段一律不读 | `src/pyknora/core/scope.py`、`src/pyknora/api/deps.py` |
| I3 | 发布是**一个事务**：旧 build 退休 + 新 build 激活 + `documents` 三指针 + job 完成 + outbox | `src/pyknora/services/ingest/publish.py` |
| I4 | 迁移只追加：已合并的号段不改历史文件，新改动新建 `000N_*.sql` | `migrations/` |
| I5 | 不动别人的目录（切片见结构说明 §1.3）；共用文件只允许**追加**（§1.4） | `docs/项目结构说明.md` |
| I6 | 切片 `content` 禁止内部空白折叠（`char_start`/`char_end` 必须能拼回原文） | `src/pyknora/services/ingest/splitter.py` |
| I7 | 向量维度不匹配必须抛错，**绝不截断** | `src/pyknora/services/ingest/embedder.py` |
| I8 | 错误一律走 `AppError`，错误码从 `core/errors.py` 取；404 不泄漏"存在但无权" | `src/pyknora/core/errors.py` |

> 追加式修改的具体写法：新路由在 `src/pyknora/api/main.py` 里 `include_router`；新表映射在
> `src/pyknora/models/tables.py` 末尾追加；契约 `src/pyknora/services/contracts/reads.py` 改签名 = 全员评审。

---

## 5. 目录归属速查

| 成员 | 自己的目录（可写） | 共用文件（只追加/需评审） |
| --- | --- | --- |
| 成员1 | `src/pyknora/api/v1/knowledge_bases.py`、`services/kb/`、`db/repos/knowledge_bases.py`、`migrations/0002_*` | — |
| 成员2 | `api/v1/{documents,jobs}.py`、`services/ingest/`、`services/queue/`、`workers/`、`db/repos/reads.py`、`migrations/0001_*` | `models/tables.py`（追加） |
| 成员3 | `api/v1/{query,chat}.py`、`services/retrieval/`、`services/rag/`、`schemas/{query,chat}.py` | `db/repos/reads.py`（加谓词） |
| 成员4 | `api/v1/{auth,workspaces}.py`、`services/auth/`、`db/repos/principals.py`、`migrations/0003_*` | `api/deps.py`（★权限唯一入口） |
| 成员5 | `api/v1/{lineage,admin}.py`、`services/{lineage,migration}/`、`db/repos/{citations,index_migrations}.py`、`migrations/0004_*` | `services/ingest/publish.py`（复用，不改语义） |

完整切片与"不许碰"清单见 `docs/项目结构说明.md` §1.3、§1.4。

---

## 6. 命令与验收（照抄，别自创）

```bash
# 依赖
uv sync --extra dev

# 起库（宿主要映射到 .env 里 PYKNORA_DATABASE_URL 写的那个端口）
docker run -d --name pyknora-pg -p 5433:5432 \
  -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=pyknora pgvector/pgvector:pg16

# 建表 + 造数（迁移靠 docker exec，容器名默认 pyknora-pg）
uv run python scripts/migrate.py
uv run python scripts/seed_for_m3.py

# 验收（由快到慢）
uv run ruff check src tests scripts     # 必须 All checks passed
uv run pytest -q tests/unit             # 纯函数，不需要数据库
uv run pytest -q tests/api              # 需真 PG：走 ASGI，不真起 HTTP
uv run pytest -q tests/integration      # 需真 PG（每例 TRUNCATE）
uv run pytest -q                        # 全量
```

注意事项：

- `uv run ruff check .` 会因未入库的 `.tmp_ref/` 报 47 个错，**只查 `src tests scripts`**；
- 本机没有 `psql` 客户端，但迁移脚本依赖 `docker exec`，所以**必须有 docker**；
- 集成测试连的是 `PYKNORA_DATABASE_URL`（`PYKNORA_` 前缀），不存在 `--psql-url` 参数；
- **跑测试前先停掉本地 API 与 Worker**：它们和用例共用同一个库，`TRUNCATE ... CASCADE` 要拿
  `ACCESS EXCLUSIVE`，和 Worker 的 `FOR UPDATE SKIP LOCKED` 撞上会直接 `deadlock detected`，
  表现为"随机某条用例 ERROR"。这不是用例的 bug；
- **先 `uv sync --extra dev`**：本地 embedding 走 `sentence-transformers` 时才需要 `--extra local-embed`，
  测试与 `seed_for_m3.py` 用 `test/hashing`，不下载模型；
- 控制台输出中文乱码是 cp936 显示问题，不是数据坏了；需要看输出时先
  `$env:PYTHONIOENCODING='utf-8'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)`；
- 交付时贴**命令 + 真实输出**，不贴"预期结果"。

### 6.1 本地门禁（提交时自动跑）

门禁只有一处定义：`scripts/ci.py`。`--stage all` = `ruff check` → `ruff format --check` → `pytest -q tests/unit`，
按"便宜的先跑"排序，任一步失败即停。

```bash
git config core.hooksPath .githooks    # 克隆后执行一次：激活钩子
git hook run pre-commit                # 手动跑一次（等价于提交时那次；实测 7.2s）
uv run python scripts/ci.py --list     # 只看计划，不执行
uv run python scripts/ci.py --stage lint,test   # 只跑部分阶段
```

- 钩子在 `.githooks/pre-commit`（入库），`git commit` 前自动执行，未通过即中止提交；
- **不用 prek / pre-commit 管钩子**：它们先 stash 未暂存改动，`pyproject.toml` 会被还原成 HEAD 版本
  （`dependencies = []`、没有 `[tool.ruff]` 表），门禁就跑在错误的项目元数据上——已实测出现假失败（13 个假错）；
- 钩子必须 LF 换行，`.gitattributes` 已锁 `.githooks/*` 与 `*.sh` 为 `eol=lf`；Windows 上是 CRLF 会报 `bad interpreter`；
- 门禁**不跑集成测试**（需真 PG），集成层按本节手工跑，后续再接云端 CI。

### 6.2 安全检查（不进提交门禁）

`--stage all` **故意不含 security**：pip-audit 要联网比对 OSV 数据库，结论随远端变化，
挂到 pre-commit 上等于让网络抖动决定你能不能提交。所以它单独一刀，人工/CI 跑：

```bash
uv run python scripts/ci.py --stage security    # 依赖漏洞：uv run --with pip-audit pip-audit
uv run --with detect-secrets detect-secrets scan --no-verify src scripts migrations tests   # 硬编码密钥
```

- 2026-10-28 实测：pip-audit → `No known vulnerabilities found`（exit 0）；detect-secrets → `results: {}`；
- `pip-audit` 不在项目依赖里，必须走 `uv run --with pip-audit`，直接 `uv run pip-audit` 会 `program not found`；
- detect-secrets 是**报告式**（有命中也 exit 0），要当门禁用得先 `detect-secrets scan > .secrets.baseline`，
  再把 `detect-secrets-hook --baseline` 接进来——这属于团队策略，别偷偷加；
- 改依赖锁文件（`uv.lock`）、升级镜像、或每月固定日，跑一次 `--stage security`。

---

## 7. 提交与交付

提交前依次确认：

1. 本地门禁已通过——`git commit` 时钩子会自动跑；想先自查就 `git hook run pre-commit`
   （= `ruff check` + `ruff format --check` + `pytest -q tests/unit`，约 7s）；
2. `git status --short` 只含本轮文件（别把 `.idea/`、`.venv/`、`.tmp_ref/` 带进去）；
3. 只 `git add` 本轮文件，不 `commit --amend` 别人的提交，不改写历史；
4. 进度文件已按 §3 更新（这是提交的一部分，不是附带）。

交付话术必须包含四段：**改了什么 / 命令与输出 / 没做什么（含未验证项） / 风险与下一步**。

---

## 8. 已知坑（摘自结构说明 §7）

1. 直接 `uvicorn` 起服务**已经能上传**：`api/deps.py:117-131` 的 `get_kb_repo` 在没注入进程级仓库时回落成 `SqlKnowledgeBaseRepo(session)`。真正的坑是 `/health` 里 `kb_repo: null` —— 那只说明"按请求构造"，不是缺陷，别照旧文档去追 503；
2. `v_active_chunks` 视图**不校验向量维度**，维度校验在 `SqlChunkSearchRepo.search_similar`；
3. 切片 `content` 只 strip 首尾、禁止内部空白折叠，否则引用拼不回原文；
4. `FakeKbRepo` 第一版收下 `Scope` 就丢掉，导致跨区测试**永远通过**——写新 repo 时别重复这个错误；
5. 集成测试的异步夹具必须是 `function` 作用域，并在结束时 `dispose_engine()`，否则连接数会打爆；
6. `models/tables.py` 的 docstring 说"七张表"，实际 8 张（历史遗留，别照着数）；
7. `db/repos/reads.py` 反向依赖 `services.ingest.embedder.EmbeddingDimensionError`（已知违例）；
8. `pyknora` 命令行目前只打印 `Hello from pyknora!`，不要当入口用；
9. **流式端点里不许抛 `AppError`**：`StreamingResponse` 一旦开始发就改不了状态码，
   生成器里抛异常会让 Starlette 撞 `"response already started"`，客户端只看到
   `httpx.RemoteProtocolError`（连错误体都没有）。失败要降级成 SSE 的 `error` 事件
   再补 `done` 收尾。参照 `services/rag/answer.py:86-96` 与 `tests/api/test_api_chat.py`；
   建流**之前**能判的（没配 key、没注入 embedder）必须提前抛成 503，别拖进流里。

---

## 9. 本文件的修改

`AGENTS.md` 是流程基线：**修改需用户确认**，并在 `docs/IMPLEMENTATION_STATUS.md` 当轮记录"为什么改"。
技术事实（§2）变更时，同步更新 `docs/项目结构说明.md`，避免两处口径不一致。
