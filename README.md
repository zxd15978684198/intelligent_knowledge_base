# PyKnora

企业知识库：增量索引与权限隔离。

本仓库当前实现范围见 [docs/plans/成员2执行计划.md](docs/plans/成员2执行计划.md)：
**成员2（摄取与索引）的交付面**——文档上传与 hash 三分支、解析切片、
Embedding、原子发布、删除清理，以及对外可见的读契约。

## 快速开始

```bash
# 1. 起一个带 pgvector 的 PostgreSQL
docker run -d --name pyknora-pg -p 5433:5432 \
  -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=pyknora \
  pgvector/pgvector:pg16

# 2. 建表
uv run python scripts/migrate.py

# 3. 造一份给检索同学用的数据（两个 workspace，内容相同）
uv run python scripts/seed_for_m3.py

# 4. 跑测试
uv run pytest
```

## 目录

**整体结构（树形图 + 每个目录的职责与归属）见
[docs/项目结构说明.md](docs/项目结构说明.md)**，新成员从这里开始读。

| 路径 | 内容 |
| --- | --- |
| `migrations/` | 手写数字号段 SQL，不用 alembic（理由见计划书 §0.5） |
| `src/pyknora/core/` | `Settings`、`Scope`、统一错误码 |
| `src/pyknora/api/` | FastAPI 交付面：`deps.py` 是权限唯一入口 |
| `src/pyknora/services/ingest/` | 解析、切片、Embedding、上传、原子发布 |
| `src/pyknora/services/queue/` | PG 队列：入队、`SKIP LOCKED` 取件、租约续期 |
| `src/pyknora/workers/` | 独立 Worker 进程（不是 FastAPI 的后台任务） |
| `src/pyknora/db/repos/` | 读契约实现，全部走 `v_active_chunks` 视图 |
| `docs/plans/` | 实现路径、成员2 执行计划、契约草案 |

## 三条不变量

1. **活动版本由视图保证。** `v_active_chunks` 的 `WHERE d.active_build_id = b.build_id`
   让"读到旧 build 的数据"在结构上不可能，调用方忘了加条件也读不到。
2. **`Scope` 只从 JWT 来。** 请求体与查询串里的身份字段一律不读，
   `scope.owns(None) == False` 是 fail-closed。
3. **发布是一个事务。** 旧 build 退休、新 build 激活、`documents` 三个指针切换、
   job 完成、outbox 事件，全在同一事务里；pgvector 与业务指针同域。

## 已知限制

见 [docs/plans/成员2执行计划.md](docs/plans/成员2执行计划.md) §9。当前最重要的一条：
`knowledge_bases` 表由成员1 负责，本仓库用 `FakeKbRepo` 顶替（数据假、schema 真），
替换成本是 `src/pyknora/api/main.py` 里 `configure()` 传参那一行。
