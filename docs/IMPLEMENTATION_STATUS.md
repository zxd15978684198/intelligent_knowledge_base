# PyKnora 实施状态

更新日期：2026-10-29（Asia/Shanghai）。**2026-10-29 追加（最新）：审查三条缺陷收口（经用户「要，一并处理审查」授权）——`services/queue/jobs.py` 的 `claim()` 增加 `job_types` 过滤并由 `workers/ingest_worker.py` 显式声明 `SUPPORTED_JOB_TYPES=("ingest","purge")`（原先任何 Worker 都可能领走不认识的类型、判 unknown 后作业直接丢）、`services/auth/jwt.py` 强制 `exp`（原先缺 `exp` 即放行 = 永久令牌，fail-open）、`api/v1/documents.py` 抽出 `_read_bounded()` 按 1MiB 分块读到上限即断（原先整包读进内存才判断 50MB），新增 5 条用例，全量 **308 passed / exit 0**，见下方「本轮：审查三条缺陷收口」**；紧接其后是检索两路真并行（成员3 域，经用户「5要并行」授权）——`services/retrieval/search.py` 的 `search_hybrid` 原先 docstring 写「并行」实则两串行 `await`，改为 `asyncio.gather` 两路；因 `AsyncSession` 不支持并发语句，新增独立 session 依赖 `get_keyword_search_repo()`，`query.py`/`chat.py` 注入透传，另加 1 条「关键词必须走第二个仓库」的单元断言，全量 **303 passed / exit 0**，见下方「本轮：检索两路真并行」**；紧接其后是成员管理与权限自省补全（成员4 域，经用户「你写一份成员管理修复计划，然后执行」授权代做）——补上 6 个端点（`GET`/`POST /workspaces/{ws}/members`、`DELETE .../members/{id}`、`PUT /workspaces/{ws}/owner`、`GET .../members/{id}/permissions`、`GET /auth/me`）、2 个 repo 方法（`delete_membership` / `transfer_ownership`）、22 个新用例（其中 5 个是"新端点忘挂权限"的结构守卫），全量 **297 passed / exit 0**，见下方「本轮：成员管理与权限自省补全」**；再往前是中文全文检索从「查不出」修到「能查出」（成员2 域，经用户「现在先不做评测集合，把中文 FTS 修好或砍掉」授权）——`tsvector` 改为 `to_tsvector('simple', pyknora_cjk_bigram(content))`、查询侧改用 `pyknora_cjk_tsquery()` 并做 AND→OR 两级降级、新增 `ChunkRepo.search_keyword` 原语，新增迁移 `migrations/0007_cjk_bigram_fts.sql`（零新扩展、零新镜像），见下方「本轮：中文 FTS 修好」**；紧接其后是「角色是数据，不是 DDL 枚举」（成员4 域，经用户「补」授权代做）——摘掉 `workspace_memberships.role` 的列举式 `CHECK`，换成格式约束 + 指向 `roles(role_key)` 的外键，新增 `migrations/0008_roles_are_data.sql`、角色 CRUD 两个端点、`RoleCatalog` 改由 `roles` 表驱动，见下方「本轮：角色是数据，不是 DDL 枚举」**；再往前是 `role_grants` 从「预留」变「已接入」（成员4 域，经用户「好吧你落地role_grants」授权代做）——运行期判权链改为 `workspace_memberships.role` → `roles` → `role_grants`，新增 15 个权限点、6 个 `/workspaces/{ws}/roles|permissions` 端点、`api/deps.py` 权限依赖工厂，全量 **263 passed**，见下方「本轮：`role_grants` 落地」**；再往前是 P0–P4 安全与一致性收口（JWT 密钥生产护栏 / 停用账号令牌立即失效 / RLS 部署缺口启动告警 / `role_grants`·`is_public`·权限缓存三处口径注释），见下方「本轮：P0–P4 安全与一致性收口」**；再往前是文档事实校正——错误码 16 → 17（`AGENTS.md` §2、结构说明两处、`docs/tdd/07` §7 登记 4 个新增码为提议），见下方「本轮：文档事实校正」**；再往前是任务 17 迁库演练完成（成员5 实现 / lead 记录）——17/17 任务全部 `已完成`（「本轮：任务 17 迁库演练」）、任务 16 重建索引 `/admin/reindex` 完成（「本轮：任务 16 重建索引」）、B0 环境对账 + B2 主链路烟测（「本轮：B0 环境对账 + B2 主链路烟测」）、合并远端 chat 分支 + 修 `index_migrations` 主键口径（「本轮：合并远端 chat 分支」）；更早的成员2 两条 P0 修复、成员2 四任务收口、成员4 验收闭环、成员1 S05、身份基座落地、数据契约外键、Phase 0 / P0-T01 章节保留为其自身时点的历史记录。

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
