# PyKnora

企业知识库：增量索引与权限隔离。PDF/Markdown 上传 → 解析切片 → Embedding → 原子发布 →
混合检索（向量 + 全文）→ 带引用问答（SSE 流式），全链路按 workspace 隔离。

**当前状态**：17 个任务（5 人分工）**15 已完成 / 2 进行中**——任务 13、14 记 `进行中/75%`，
Day03 增量待 api 级验收（回调理由见进度表 §3「v1.23 口径说明」）。
测试规模 **1158** 条（unit 889 / contract 20 / api 170 / integration 79，2026-11-09 `--collect-only -q` 实测求和）。
**本轮真跑过哪些、哪些未验证**见 [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) 顶部当轮段——
本行不写"全绿"：api 与 integration 每例 `TRUNCATE` 全表，在装着评测语料的库上跑会清掉语料。
进度以 [docs/plans/成员任务进度表.md](docs/plans/成员任务进度表.md) 为准。

## 目录

**整体结构（树形图 + 每个目录的职责与归属）见
[docs/项目结构说明.md](docs/项目结构说明.md)**，新成员从这里开始读。

| 路径 | 内容 |
| --- | --- |
| `migrations/` | 手写数字号段 SQL（0001–0016），不用 alembic；**只追加不改历史** |
| `src/pyknora/core/` | `Settings`、`Scope`（身份只从 JWT 来）、19 个统一错误码 |
| `src/pyknora/api/` | FastAPI 交付面；`deps.py` 是权限唯一入口，`main.py` 是应用工厂 |
| `src/pyknora/api/v1/` | 9 组路由：`auth`/`workspaces`/`knowledge_bases`/`documents`/`jobs`/`query`/`chat`/`lineage`/`admin` |
| `src/pyknora/services/ingest/` | 解析、切片、Embedding（含缓存）、上传、原子发布 |
| `src/pyknora/services/{retrieval,rag}/` | 混合检索（RRF 融合）与带引用问答 |
| `src/pyknora/services/{kb,auth}/` | 知识库 CRUD、注册/登录与令牌 |
| `src/pyknora/services/{lineage,migration}/` | 血缘事件、索引重建与迁库导出/重放/校验 |
| `src/pyknora/services/queue/` | PG 队列：入队、`SKIP LOCKED` 取件、租约续期 |
| `src/pyknora/workers/` | 独立 Worker 进程（不是 FastAPI 的后台任务） |
| `src/pyknora/db/repos/` | 仓储实现，读路径全部走 `v_active_chunks` 视图 |
| `scripts/` | 迁移、造数、烟测、门禁、迁库三件套 |
| `tests/` | `unit`（无库）/ `contract` / `api`（ASGI 打真库）/ `integration`（真 PG） |
| `docs/` | 见文末「文档地图」 |

## 快速开始

前置：Python 3.12、[uv](https://docs.astral.sh/uv/)、Docker、Node 20+ 与 pnpm（只有前端需要）。

### 一条命令起全套（推荐）

```bash
uv sync --extra dev --extra local-embed   # 首次
uv run python scripts/dev_up.py           # 库体检 → API:8010 → Worker → 前端:3000
```

启动后打开 **http://127.0.0.1:3000/login**。脚本会自己探活，成功才打印地址表；
Ctrl-C 一起停。它会顺带解决三件以前靠人记住的事：

- **库端口错位**：配置写 5433、机器上库在 5434（容器 `pyknora-pg-p0`）时，`/health`
  照样 200，但登录/列表会 500。脚本探测到错位就对子进程注入 `PYKNORA_DATABASE_URL`，
  并在屏幕上告警，不改你的配置文件；
- **离线 embedding 卡启动**：默认给子进程加 `HF_HUB_OFFLINE=1`，否则 `transformers`
  会对每个文件重试 5 次，实测启动卡 2 分钟以上，现象是"端口一直连不上"；
- **前端代理地址**：自动注入 `API_PROXY_TARGET`，Next 的 rewrite 指向本次启动的 API。

常用参数：

```bash
uv run python scripts/dev_up.py --check        # 只体检，不启动任何进程
uv run python scripts/dev_up.py --no-web       # 只起后端（前端自己 pnpm dev）
uv run python scripts/dev_up.py --start-db     # 容器没起时顺手 docker start
uv run python scripts/dev_up.py --api-port 8011 --web-port 3001
```

脚本是幂等的：已在跑的 API/前端只探活复用，不重复起进程，可以反复执行。

### Docker Compose 一键起栈（可复现环境，给评审与交付用）

本机日常开发用上面的 `dev_up.py`；要一个"别人 clone 下来就能起"的环境，用 compose：

```bash
cp .env.example .env                  # 首次；生产另需 PYKNORA_ENV=prod 与非默认 JWT 密钥
docker compose up --build --wait      # 起 db → 迁移 → api → worker → web，等到健康
docker compose ps -a                  # migrate 必须是 Exited (0)；Exited (1) 就是迁移失败
docker compose down                   # 停栈，保留数据卷
docker compose down -v                # 连数据一起删，只在确认要重建时用
```

起栈顺序由 healthcheck 门控，不是靠 `sleep` 猜：`db` 健康 → `migrate` 一次性按 `0001–0014`
顺序灌 SQL（`-v ON_ERROR_STOP=1 -v dim=$PYKNORA_EMBEDDING_DIM`）后退出 → `api`/`worker`
等它 `service_completed_successfully` → `web` 等 `api` 健康。api 与 worker 共用同一个镜像，
只差 `command`。

| 宿主端口 | 服务 | 说明 |
| --- | --- | --- |
| 5435 | db | 故意避开本机常见的 5433/5434（那两个常是别人的容器），可用 `PYKNORA_COMPOSE_DB_PORT` 改 |
| 8010 | api | OpenAPI 在 `/docs`，健康检查在 `/health` |
| 3000 | web | 登录页 `/login` |

几个容易踩的点：

- **容器内走服务名**：`postgresql+asyncpg://postgres:dev@db:5432/pyknora`，不是 `127.0.0.1`
  ——每个容器有自己的回环地址，照抄本机配置会连不上；
- **对话上游是可选项**：`PYKNORA_CHAT_BASE_URL` / `_MODEL` / `_API_KEY` 不填时，`/chat`
  在建流之前直接 503，而不是在 SSE 流里报错断开；
- **模型缓存在命名卷**里（`HF_HOME=/data/hf`），重建镜像不必重下 torch；
  只想跑远端 embedding 时用 `--build-arg INSTALL_LOCAL_EMBED=false` 把本地那套依赖摘掉；
- **镜像本身**：三阶段构建（runtime 里没有 uv）、非 root（uid/gid 10001）、不装 curl
  （健康检查用解释器自探）、依赖层与代码层分开 COPY 以保住缓存；
- 回滚、排障与版本策略见 [docs/部署与回滚.md](docs/部署与回滚.md) 与 `scripts/rollback.ps1`。

### 打开接口文档

| 地址 | 内容 |
| --- | --- |
| http://127.0.0.1:3000/docs | **Swagger UI**（可交互调试，能直接填 Bearer 令牌试接口） |
| http://127.0.0.1:3000/api-docs | 中文说明书（31 个动作分组、SSE 四帧、17 个错误码、16 个权限点）。**该页内容落后于后端**：SSE 缺 `refusal` 帧、错误码缺 `PARSE_TIMEOUT`/`CSRF_FAILED`（后端 19 个）、认证段仍写「无刷新端点 / 退出只是前端丢弃令牌」而 M3/M4 已落 Cookie 会话 + `/auth/refresh` + 退出撤销全族。以 `docs/tdd/07` 与代码为准，待修 |
| http://127.0.0.1:8010/docs | 与第一条同一个页面，绕过前端直连后端时的入口 |

Swagger 的 JS/CSS 由后端自托管在 `src/pyknora/static/swagger/`（`swagger-ui-dist` 5.29.0，
约 1.6 MB），**不走 CDN**——之前 `/docs` 一片空白就是因为页面去取
`cdn.jsdelivr.net`，在国内网络下是 `ERR_CONNECTION_RESET`，控制台只剩
`SwaggerUIBundle is not defined`。现在断网也能打开。

### 分步启动（需要单独控制每个进程时）

```bash
# 0. 启用仓库自带提交门禁（只做一次；绕过它提交会被评审打回）
git config core.hooksPath .githooks

# 1. 起一个带 pgvector 的 PostgreSQL（宿主 5433）
#    本地用 postgres 超管即可；生产必须改用**非表 owner** 角色（如 0003 建的 pyknora_app），
#    否则 RLS 兜底不生效——见 .env.example 与 docs/adr/0006。同理生产必须设
#    PYKNORA_ENV=prod 并提供非默认的 PYKNORA_JWT_SECRET，否则服务拒绝启动（故意的）。
docker run -d --name pyknora-pg -p 5433:5432 \
  -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=pyknora \
  pgvector/pgvector:pg16

# 2. 依赖
#    --extra dev           → pytest / ruff（跑测试与门禁必需）
#    --extra local-embed   → sentence-transformers（默认 embedding 后端 local 需要；
#                            只用 hashing 后端可省，但那样检索是假向量）
uv sync --extra dev --extra local-embed

# 3. 建表（0001–0006）
uv run python scripts/migrate.py

# 4. 造数：两个 workspace、内容相同，用来验证跨区隔离
uv run python scripts/seed_for_m3.py

# 5. 起 API 与 Worker（两个终端；建表/造数之后再起）
uv run uvicorn pyknora.api.main:create_app --factory --port 8010
uv run python scripts/run_worker.py

# 6. 起前端（第三个终端；API_PROXY_TARGET 不写就默认 8010）
cd web && pnpm install && pnpm dev        # http://127.0.0.1:3000

# 7. 测试
uv run pytest -q tests/unit      # 不需要数据库
cd web && pnpm typecheck && pnpm test     # 前端类型与单测
```

> **端口与库名以 `.env` 为准**：`PYKNORA_DATABASE_URL`、`PYKNORA_DB_CONTAINER`。
> 本机若已有别的 pgvector 容器（例如跑在 5434），先 `docker ps` 确认再改 `.env`，
> 不要改代码里的默认值。默认嵌入后端是 `local`（`BAAI/bge-small-zh-v1.5`, dim 512），
> 无外网时可用 `PYKNORA_EMBEDDING_BACKEND=hashing` 跑通全链路。


### 配置

配置入口是 `.env`（复制 `.env.example` 改名）。逐项带注释的完整清单就是 **`.env.example`**；
下表是同一套变量的索引，方便查默认值。**25 项 = `Settings` 的 24 个字段 + `PYKNORA_JWT_SECRET`
（后者不在 `Settings` 里，由 `services/auth/jwt.py:37` 直接读环境变量）。全部有默认值，不配也能跑。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| **数据库** | | |
| `PYKNORA_DATABASE_URL` | `...@127.0.0.1:5433/pyknora` | asyncpg DSN |
| `PYKNORA_DB_CONTAINER` | `pyknora-pg` | `migrate.py` 走 `docker exec` 时进哪个容器 |
| **Embedding** | | |
| `PYKNORA_EMBEDDING_BACKEND` | `local` | `local` / `openai` / `hashing`；写错直接 `INVALID_REQUEST` |
| `PYKNORA_EMBEDDING_MODEL_KEY` | `BAAI/bge-small-zh-v1.5` | 只有 `local` 后端以它为准 |
| `PYKNORA_EMBEDDING_DIM` | `512` | **硬约束**：维度不符抛 `EmbeddingDimensionError`，绝不截断 |
| `PYKNORA_EMBEDDING_BATCH_SIZE` | `8` | 一次编码多少条 |
| `PYKNORA_EMBEDDING_TIMEOUT_S` | `30` | 单次编码超时 |
| `PYKNORA_HUGGINGFACE_ENDPOINT` | `https://huggingface.co` | 权重源 |
| `PYKNORA_HUGGINGFACE_MIRROR` | `https://hf-mirror.com` | 镜像源（国内） |
| `PYKNORA_OPENAI_BASE_URL` | `https://api.openai.com/v1` | `openai` 后端 |
| `PYKNORA_OPENAI_API_KEY` | 空 | `openai` 后端必需 |
| `PYKNORA_OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | 注意它是 1536 维，要同步改 `DIM` |
| **问答（chat）** | | |
| `PYKNORA_CHAT_BASE_URL` | `https://api.openai.com/v1` | 自动拼 `/chat/completions` |
| `PYKNORA_CHAT_API_KEY` | 空 | **空则 `/chat` 直接 503**；也是这条链路的开关 |
| `PYKNORA_CHAT_MODEL` | `gpt-4o-mini` | 原样放进请求体 `model` |
| `PYKNORA_CHAT_TIMEOUT_S` | `60` | |
| `PYKNORA_CHAT_MAX_CONTEXT_CHUNKS` | `6` | 拼进提示词的切片条数上限 |
| **切片与队列** | | |
| `PYKNORA_CHUNK_SIZE` | `512` | 切片字符数 |
| `PYKNORA_CHUNK_OVERLAP` | `64` | 相邻切片重叠 |
| `PYKNORA_MAX_UPLOAD_BYTES` | `52428800` | 单文件 50 MiB |
| `PYKNORA_LEASE_SECONDS` | `90` | 作业租约；超时未续租可被其他 worker 抢 |
| `PYKNORA_MAX_ATTEMPTS` | `3` | 作业重试上限 |
| **分页游标** | | |
| `PYKNORA_CURSOR_SECRET` | `dev-only-insecure-cursor-secret` | 生产必换；换了旧游标全部失效（410） |
| `PYKNORA_CURSOR_TTL_SECONDS` | `86400` | 游标有效期 |
| **鉴权** | | |
| `PYKNORA_JWT_SECRET` | `dev-only-insecure-secret` | HS256 签名密钥，生产必换；**不在 `Settings` 里** |

> `PYKNORA_JWT_SECRET` 之所以特殊：`jwt.py` 刻意在**调用时**读环境变量（而不是 `import` 时
> 快照），这样测试能用 `monkeypatch.setenv` 换密钥。代价是它绕过了 `Settings`，所以
> `settings` 对象上看不到它——这也是它当初没被写进 `.env.example` 的原因。
> 令牌有效期目前固定 3600 秒（`jwt.py` 的 `DEFAULT_TTL_SECONDS`），尚未做成配置项。


## API 一览

全部挂在 `/api/v1` 下；身份一律来自 JWT，请求体/查询串里的身份字段不读。

| 路由组 | 端点 | 说明 |
| --- | --- | --- |
| 认证 | `POST /auth/register`、`POST /auth/login` | 返回访问令牌 |
| 工作区 | `PATCH /workspaces/{ws_id}/members/{membership_id}` | 成员角色调整 |
| 知识库 | `POST`/`GET /knowledge-bases`、`GET`/`PATCH`/`DELETE /knowledge-bases/{kb_id}` | 删除是软删 |
| 文档 | `POST /knowledge-bases/{kb_id}/documents` | 上传，按 content_hash 三分支：新建 200 / 重建 202 / 幂等重放 |
| | `GET`/`DELETE /documents/{document_id}`、`GET /documents/{document_id}/chunks` | 详情、软删、切片分页 |
| 作业 | `GET /jobs/{job_id}`、`POST /jobs/{job_id}/cancel` | 索引作业状态机 |
| 检索 | `POST /knowledge-bases/{kb_id}/query` | 混合检索（向量 + 全文，RRF） |
| 问答 | `POST /knowledge-bases/{kb_id}/chat` | SSE：`retrieval` → `token`* → `done`（含 citations） |
| 血缘 | `GET /documents/{document_id}/lineage` | 版本与 build 血缘 |
| 管理 | `POST /admin/reindex` | 索引重建，`202`；目前只支持 `mode=reindex` |

## 命令

| 命令 | 用途 |
| --- | --- |
| `uv run ruff check src tests scripts` | 静态检查（**别查全仓**，`.tmp_ref/` 有历史噪音） |
| `uv run ruff format --check src tests scripts` | 格式检查 |
| `uv run pytest -q tests/unit` | 889 个，不需要数据库 |
| `uv run pytest -q tests/contract` | 20 个，跨模块契约 |
| `uv run pytest -q tests/api` | 170 个，ASGI 打真库 |
| `uv run pytest -q tests/integration` | 79 个，需真 PG |
| `uv run python scripts/ci.py --profile commit` | 提交门禁三步（lock + lint + manifest，0.1s） |
| `uv run python scripts/ci.py --stage all` | 推送门禁八步（= `--profile default`，约 50s） |
| `uv run python scripts/ci.py --profile full` | 全 13 阶段（含 integration/audit/container） |
| `uv run python scripts/ci.py --stage audit` | 依赖漏洞审计（pip-audit），需联网 |
| `uv run python scripts/smoke_e2e.py --base-url http://127.0.0.1:8010` | 端到端 14 项断言 |

> 跑全量测试前先停掉本地 API 与 Worker：它们与用例共用同一个库，
> 会让 `TRUNCATE` 抢锁，表现为随机某条用例 `deadlock detected`。

### 脚本参数

| 脚本 | 参数 | 用途 |
| --- | --- | --- |
| `scripts/migrate.py` | `--database`、`--container` | 建表/补迁移；迁库演练时指向空库 |
| `scripts/run_worker.py` | `--once`、`--poll-interval`、`--worker-id` | Worker 循环；`--once` 处理一件就退，便于定位 |
| `scripts/export_metadata.py` | `--dsn`、`--out`、`--workspace` | 导出快照（`.json` + 同名 `.sql`）；只导指定 workspace 时用 `--workspace` |
| `scripts/replay_metadata.py` | `--dsn`、`--in`、`--on-conflict` | 目标库重放；冲突策略默认 `error` |
| `scripts/verify_migration.py` | `--source`、`--target`、`--workspace` | 逐项校验；不一致非 0 退出，可直接当门禁用 |
| `scripts/smoke_e2e.py` | `--base-url`、`--job-timeout`、`--model-key`、`--dim`、`--chunk-size`、`--chunk-overlap`、`--keep` | 端到端 14 项；`--model-key` 必须与 API 的 embedder 一致 |
| `scripts/ci.py` | `--stage`、`--list`、`--stop-after`、`--json`、`--check`、`--root`、`--timeout`、`--with` | 本地门禁；`--list` 只打印计划不执行 |

## 迁库（任务17）

迁库保留的是**元信息与原文**，向量在目标库重算——不是把 pgvector 字节搬过去
（同大版本/同维度/同 operator class 任一不符就全废）。

```bash
# 1. 导出（产物 <out>.json + <out>.sql，SQL 里不含 embedding/tsvector 列）
uv run python scripts/export_metadata.py --out .tmp_migration/snapshot.json

# 2. 目标库重放（先父后子；冲突默认报错）
uv run python scripts/replay_metadata.py --snapshot .tmp_migration/snapshot.json

# 3. 逐项校验（KB / 文档 / 切片边界 / 活跃 build 指针 / 向量已重算）
uv run python scripts/verify_migration.py --source <源DSN> --target <目标DSN>
```

完整演练数据与两处发现见
[docs/plans/任务17_迁库演练记录.md](docs/plans/任务17_迁库演练记录.md)。

## 五条不变量

1. **活动版本由视图保证。** `v_active_chunks` 的 `WHERE d.active_build_id = b.build_id`
   让"读到旧 build 的数据"在结构上不可能，调用方忘了加条件也读不到。
   注意：该视图不 join `knowledge_bases`，KB 层软删要自己补谓词。
2. **`Scope` 只从 JWT 来。** 请求体与查询串里的身份字段一律不读，
   `scope.owns(None) == False` 是 fail-closed。
3. **发布是一个事务。** 旧 build 退休、新 build 激活、`documents` 三个指针切换、
   job 完成、outbox 事件，全在同一事务里；pgvector 与业务指针同域。
4. **迁移只追加。** 已合并的号段不改历史文件，新改动新建 `000N_*.sql`。
5. **向量维度不匹配必须抛错，绝不截断**（`EmbeddingDimensionError`）。

## 文档地图

| 文档 | 什么时候读 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | 开工前必读：流程纪律、目录归属、验收命令、9 个已知坑 |
| [docs/项目结构说明.md](docs/项目结构说明.md) | 目录树、谁负责哪个目录、共用文件冲突规则 |
| [docs/plans/全员模块实现路径.md](docs/plans/全员模块实现路径.md) | 五个模块边界、S01–S16 施工步骤、契约 C1–C6 |
| [docs/plans/成员任务进度表.md](docs/plans/成员任务进度表.md) | 17 个任务的编号/负责人/状态（**进度以此表为准**） |
| [docs/plans/项目修复计划.md](docs/plans/项目修复计划.md) | F1–F11 修复卡与终验记录 |
| [docs/plans/任务17_迁库演练记录.md](docs/plans/任务17_迁库演练记录.md) | 迁库怎么跑、S16 每条判据的证据 |
| [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) | 每轮改了什么、真实命令与输出、未验证项 |
| [docs/adr/](docs/adr/README.md)、[docs/tdd/](docs/tdd/README.md) | 技术决策（注意：ADR 仍是"提议"状态） |

## 已知限制

- **不用 Redis / Celery / alembic / lxml**：队列是 PG 表 + `SKIP LOCKED`，迁移是手写 SQL。
- `/admin/reindex` 只实现 `mode=reindex`；`export` / `replay` 目前只能走脚本（接口返回 422）。
- 迁库未做大库压测（演练样本 31 个切片），也未跨 PG 大版本验证。
- `role_grants` 表建了但暂未接入授权判定（当前角色模型够用）。
- `pyknora` 命令行只打印 greeting，不要当入口；入口是 `uvicorn ... --factory` 与 `scripts/run_worker.py`。
