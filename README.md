# PyKnora

企业知识库：增量索引与权限隔离。PDF/Markdown 上传 → 解析切片 → Embedding → 原子发布 →
混合检索（向量 + 全文）→ 带引用问答（SSE 流式），全链路按 workspace 隔离。

**当前状态全绿**：17 个任务（5 人分工）全部完成，`236` 个用例通过
（unit 88 / contract 20 / api 80 / integration 48），端到端烟测 14/14，迁库演练通过。
进度以 [docs/plans/成员任务进度表.md](docs/plans/成员任务进度表.md) 为准。

## 目录

**整体结构（树形图 + 每个目录的职责与归属）见
[docs/项目结构说明.md](docs/项目结构说明.md)**，新成员从这里开始读。

| 路径 | 内容 |
| --- | --- |
| `migrations/` | 手写数字号段 SQL（0001–0005），不用 alembic；**只追加不改历史** |
| `src/pyknora/core/` | `Settings`、`Scope`（身份只从 JWT 来）、17 个统一错误码 |
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

前置：Python 3.12、[uv](https://docs.astral.sh/uv/)、Docker。

```bash
# 1. 起一个带 pgvector 的 PostgreSQL（宿主 5433）
docker run -d --name pyknora-pg -p 5433:5432 \
  -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=pyknora \
  pgvector/pgvector:pg16

# 2. 依赖（注意 --extra dev 才有 pytest/ruff）
uv sync --extra dev

# 3. 建表（0001–0005）
uv run python scripts/migrate.py

# 4. 造数：两个 workspace、内容相同，用来验证跨区隔离
uv run python scripts/seed_for_m3.py

# 5. 起 API 与 Worker（两个终端；建表/造数之后再起）
uv run uvicorn pyknora.api.main:create_app --factory --port 8010
uv run python scripts/run_worker.py

# 6. 测试
uv run pytest -q tests/unit      # 不需要数据库
```

> **端口与库名以 `.env` 为准**：`PYKNORA_DATABASE_URL`、`PYKNORA_DB_CONTAINER`。
> 本机若已有别的 pgvector 容器（例如跑在 5434），先 `docker ps` 确认再改 `.env`，
> 不要改代码里的默认值。默认嵌入后端是 `local`（`BAAI/bge-small-zh-v1.5`, dim 512），
> 无外网时可用 `PYKNORA_EMBEDDING_BACKEND=hashing` 跑通全链路。

常见环境变量（完整清单见 `src/pyknora/core/settings.py`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PYKNORA_DATABASE_URL` | `...@127.0.0.1:5433/pyknora` | asyncpg DSN |
| `PYKNORA_DB_CONTAINER` | `pyknora-pg` | `migrate.py` 用 `docker exec` 时进哪个容器 |
| `PYKNORA_EMBEDDING_BACKEND` | `local` | `local` / `hashing` |
| `PYKNORA_CHAT_API_KEY` | 空 | 空则 `/chat` 直接 503（流式端点不抛栈，降级成 SSE `error` 事件） |

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
| `uv run pytest -q tests/unit` | 88 个，不需要数据库 |
| `uv run pytest -q tests/contract` | 20 个，跨模块契约 |
| `uv run pytest -q tests/api` | 80 个，ASGI 打真库 |
| `uv run pytest -q tests/integration` | 48 个，需真 PG |
| `uv run python scripts/ci.py --stage all` | 本地门禁（lint + test；**不含** security） |
| `uv run python scripts/ci.py --stage security` | 依赖漏洞审计，需联网，单独跑 |
| `uv run python scripts/smoke_e2e.py --base-url http://127.0.0.1:8010` | 端到端 14 项断言 |

> 跑全量测试前先停掉本地 API 与 Worker：它们与用例共用同一个库，
> 会让 `TRUNCATE` 抢锁，表现为随机某条用例 `deadlock detected`。

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
