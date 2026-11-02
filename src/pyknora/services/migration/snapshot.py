"""任务17：迁库保留索引元信息——导出 / 重放 / 校验的纯逻辑。

**为什么只搬"元信息 + 原文"，不搬向量**（计划书 `全员模块实现路径.md:211-213`）：
字节级搬 pgvector 数据要求目标库同 PG 版本、同维度、同 operator class，任何一项
不符就全废；而按 `index_builds` 里记录的 profile 在目标库重算一次，换来的是
跨版本、跨维度都可迁移。代价是重算 embedding 的耗时与 API 费用，这是刻意付的。

本模块只做三件事，都不碰发布语义：

1. `export_metadata()`：按 workspace 取出元信息与原文，**向量列与派生列一律不取**
   （`chunks.embedding`、`chunks.tsvector`、`embedding_cache.*`）。
2. `replay_metadata()`：把快照按外键顺序写进目标库，chunk 行**没有向量**。
   此时 `v_active_chunks` 一条也不会露出（视图要求 `embedding IS NOT NULL`），
   这正是"没搬向量"的可观测证据；补向量必须走 `POST /admin/reindex`（任务16）。
3. `verify_metadata()`：比对源/目标的 profile、版本、chunk 边界与活动行数。

格式契约：快照是一个 JSON 对象，顶层键 `{"format","version","exported_at","source",
"tables":{"<表名>":[行...]}}`。行是"列名 → 值"的字典，UUID 存字符串、时间存
ISO 8601、`bytea` 存 base64。**任何一张表都不许出现 `embedding` 键**——这一点由
`assert_no_embedding()` 强制，不靠人肉检查。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

FORMAT_NAME = "pyknora-metadata-snapshot"
FORMAT_VERSION = 1

#: 向量与派生列：导出时**必须**排除。写成一个常量而不是散在各处，
#: 是为了让"搬了向量吗"这件事只有一处可查。
FORBIDDEN_COLUMNS: frozenset[str] = frozenset({"embedding", "tsvector"})


@dataclass(frozen=True, slots=True)
class TableSpec:
    """一张表的导出规格。`order_by` 只影响快照内行的顺序（可 diff）。"""

    name: str
    columns: tuple[str, ...]
    order_by: str
    #: 有外键指向它，重放时必须先写。顺序见 `REPLAY_ORDER`。
    filtered: bool = True


TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        "principals",
        (
            "principal_id",
            "username",
            "email",
            "password_hash",
            "display_name",
            "status",
            "created_at",
            "updated_at",
        ),
        "principal_id",
    ),
    TableSpec(
        "workspaces",
        ("workspace_id", "name", "owner_principal_id", "is_public", "created_at"),
        "workspace_id",
    ),
    # 成员关系必须跟着走：目标库没有它，`get_scope` → `load_membership` 会 fail-closed，
    # 迁完之后**所有人都是 403**，而迁移校验完全看不出来（旧版就是这个状态）。
    # 角色矩阵（roles / role_grants）由目标库的迁移种子重建，不需要搬；
    # 只有"谁属于哪个区、是什么角色"没有任何别的来源。
    TableSpec(
        "workspace_memberships",
        ("membership_id", "workspace_id", "user_id", "role", "created_at"),
        "membership_id",
    ),
    TableSpec(
        "knowledge_bases",
        (
            "kb_id",
            "workspace_id",
            "name",
            "embedding_model_key",
            "embedding_dim",
            "chunk_size",
            "chunk_overlap",
            "normalize",
            "active_index_build_id",
            "row_version",
            "generation",
            "deleted_at",
            "created_at",
            "updated_at",
        ),
        "kb_id",
    ),
    TableSpec(
        "documents",
        (
            "document_id",
            "workspace_id",
            "kb_id",
            "desired_document_version_id",
            "active_document_version_id",
            "active_build_id",
            "active_index_version",
            "generation",
            "row_version",
            "deleted_at",
            "created_at",
        ),
        "document_id",
    ),
    TableSpec(
        "document_versions",
        (
            "document_version_id",
            "workspace_id",
            "document_id",
            "content_hash",
            "byte_size",
            "object_key",
            "parse_status",
            "created_by_principal_id",
            "ingestion_source",
            "created_at",
        ),
        "document_version_id",
    ),
    TableSpec(
        "document_blobs",
        ("workspace_id", "document_version_id", "content"),
        "document_version_id",
    ),
    TableSpec(
        "index_builds",
        (
            "build_id",
            "workspace_id",
            "document_id",
            "index_version",
            "attempt_id",
            "embedding_model_key",
            "embedding_dim",
            "chunk_size",
            "chunk_overlap",
            "normalize",
            "chunk_count",
            "state",
            "created_at",
            "activated_at",
            "retired_at",
        ),
        "build_id",
    ),
    # chunks 的列在这里**逐个列举**，不写 `*`：写 `*` 的话，将来有人给表加一列
    # 向量，导出会静默带上，而"导出里没有 embedding"的验收断言就失效了。
    TableSpec(
        "chunks",
        (
            "chunk_id",
            "workspace_id",
            "document_id",
            "build_id",
            "index_version",
            "attempt_id",
            "chunk_ordinal",
            "content",
            "content_hash",
            "page_no",
            # 0013 新增（P2-D2）：跨页切片的结束页。漏掉它的话，迁库/恢复之后
            # 引用只能回指起始页——这正是加这一列要修的问题，不能在这里丢。
            "page_end_no",
            "heading_path",
            "char_start",
            "char_end",
            "token_count",
            "locator_ref",
            # 0014 新增（ADR 0008 D2/D2a）：格式无关的定位。漏掉它，迁库/恢复之后
            # 所有引用只能退回 `locator_ref` 那个偏移句柄，页码/工作表/幻灯片号全丢。
            # 它是 jsonb，`render_sql` 走 `_JSON_COLUMNS` 渲染成 `'...'::jsonb`。
            "locator",
        ),
        "chunk_id",
    ),
)

#: 重放顺序 = 外键依赖顺序。principals/workspaces 先于 knowledge_bases，
#: documents 先于 versions/builds，builds 先于 chunks，blobs 依赖 versions。
REPLAY_ORDER: tuple[str, ...] = (
    "principals",
    "workspaces",
    # 成员关系紧跟 workspaces：它同时外键依赖 workspaces 与 principals，
    # 而 principals/workspaces 已经在这之前写入。
    "workspace_memberships",
    "knowledge_bases",
    "documents",
    "document_versions",
    "document_blobs",
    "index_builds",
    "chunks",
)

_SPEC_BY_NAME: dict[str, TableSpec] = {spec.name: spec for spec in TABLES}

#: 重放后补一次 tsvector：它可由 content 完全重算，属于派生列，
#: 让 PG 自己算（与 seed / worker 同一口径），不在 Python 里拼。
#: 口径必须是 pyknora_cjk_bigram：迁库后如果 tsvector 用回裸 content，
#: 中文检索会在新库上静默失效，而快照本身看起来完整无误。
#:
#: **部署顺序约束**：本句依赖 `migrations/0007_cjk_bigram_fts.sql` 建的两个函数。
#: 目标库必须先跑完迁移再恢复快照，否则这里会因函数不存在而报错。
#: `WHERE ... tsvector IS NULL` 是正确的：`FORBIDDEN_COLUMNS`（本文件 `:40`）**含**
#: `tsvector`，导出时拒绝搬这一列，所以重放后该列必为 NULL，等于全量重算；
#: 留着 `IS NULL` 还让本句对"部分行已有词元"的库幂等（只补缺，不重算已有）。
#:
#: 本句的行为有真库用例兜底：`tests/integration/test_migration_tsvector.py`
#: 会先插一条 `tsvector IS NULL` 的 chunk，跑完本句后要求"报销"/"住宿"这类
#: 二字词能搜到——口径退回裸 content 时它必红。
_TSVECTOR_SQL = text(
    "UPDATE chunks SET tsvector = to_tsvector('simple', pyknora_cjk_bigram(content)) "
    "WHERE build_id IN :builds AND tsvector IS NULL"
)


class SnapshotError(RuntimeError):
    """快照内容不合约（有向量、缺表、目标库已有同名行）。"""


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _row_to_json(row: Mapping[str, Any], *, table: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in FORBIDDEN_COLUMNS:
            # 防御：SQL 里已经没取这两列，走到这里说明有人改了列清单。
            raise SnapshotError(f"{table}: 列 '{key}' 属于向量/派生列，禁止导出")
        if table == "document_blobs" and key == "content":
            out[key] = base64.b64encode(bytes(value)).decode("ascii")
            continue
        if isinstance(value, UUID):
            out[key] = str(value)
            continue
        out[key] = _iso(value)
    return out


def assert_no_embedding(snapshot: Mapping[str, Any]) -> None:
    """断言快照里没有任何向量/派生列。失败即抛，不返回布尔值。

    做成抛错而不是返回 True/False：调用方只会在"忘了检查"时漏掉布尔返回值，
    而异常无法被静默忽略。`--allow-vectors` 这类开关刻意不提供。

    只查**列名**，不查正文内容：切片正文里合法地可能出现 "embedding" 这个英文词
    （本项目文档就在讲向量），按字符串搜正文会误报，反而逼人不看结论就跳过校验。
    """
    for table, rows in _tables(snapshot).items():
        if table == "embedding_cache":
            raise SnapshotError("快照不允许包含 embedding_cache（纯向量缓存）")
        for index, row in enumerate(rows):
            leaked = sorted(FORBIDDEN_COLUMNS.intersection(row))
            if leaked:
                raise SnapshotError(
                    f"{table}[{index}] 含有禁止导出的列 {leaked}；"
                    "迁库只搬元信息与原文，向量必须由目标库重算"
                )


def _tables(snapshot: Mapping[str, Any]) -> Mapping[str, Sequence[Mapping[str, Any]]]:
    tables = snapshot.get("tables")
    if not isinstance(tables, Mapping):
        raise SnapshotError("快照缺少 'tables' 对象")
    return tables  # type: ignore[return-value]


def build_snapshot(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    source: Mapping[str, Any] | None = None,
    exported_at: datetime | None = None,
) -> dict[str, Any]:
    """组装成快照对象，并对行做类型归一（UUID → str、时间 → ISO）。"""
    unknown = sorted(set(tables) - set(_SPEC_BY_NAME))
    if unknown:
        raise SnapshotError(f"未知的表 {unknown}；先加到 TABLES 里再导出")
    payload = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "exported_at": (exported_at or datetime.now(UTC)).isoformat(),
        "source": dict(source or {}),
        "tables": {
            name: [_row_to_json(row, table=name) for row in rows] for name, rows in tables.items()
        },
    }
    assert_no_embedding(payload)
    return payload


async def export_metadata(
    session: AsyncSession,
    *,
    workspace_ids: Sequence[UUID] | None = None,
) -> dict[str, Any]:
    """从源库导出元信息与原文。`workspace_ids=None` 表示全库。

    只导"未软删"的行：软删的文档/版本/构建在目标库里没有重放价值，
    留着反而会让"活动行数一致"的断言变得含糊。
    """

    async def fetch(spec: TableSpec, where: str, params: dict[str, Any]) -> list[Any]:
        columns = ", ".join(spec.columns)
        sql = text(f"SELECT {columns} FROM {spec.name} WHERE {where} ORDER BY {spec.order_by}")
        for key, value in params.items():
            if isinstance(value, (list, tuple)):
                sql = sql.bindparams(bindparam(key, expanding=True))
        rows = (await session.execute(sql, params)).mappings().all()
        return [dict(row) for row in rows]

    async def fetch_principals(ids: set[Any]) -> list[dict[str, Any]]:
        """按 id 取 principals 行。两条导出分支（有文档 / 没文档）都要用。

        `workspace_memberships.user_id` 与 `workspaces.owner_principal_id` 都是
        `principals` 的真外键，所以这两类 id 必须导出；`created_by_principal_id`
        没有外键，缺了不阻塞重放，但补上更完整。
        """
        if not ids:
            return []
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT principal_id, username, email, password_hash, display_name, "
                        "status, created_at, updated_at FROM principals "
                        "WHERE principal_id IN :ids ORDER BY principal_id"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": sorted(ids)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    ws_filter = ""
    ws_params: dict[str, Any] = {}
    if workspace_ids is not None:
        ws_filter = " AND workspace_id IN :workspaces"
        ws_params["workspaces"] = [str(value) for value in workspace_ids]

    workspaces = await fetch(_SPEC_BY_NAME["workspaces"], "true", {})
    if workspace_ids is not None:
        wanted = {str(value) for value in workspace_ids}
        workspaces = [row for row in workspaces if row["workspace_id"] in wanted]

    # 成员关系跟着 workspace 走：`workspace_memberships.user_id` 有外键指向
    # `principals`，所以下面收集 principals 时必须把成员也算进去，否则重放会在
    # memberships 处撞外键（owner 之外的成员恰好都不是 created_by）。
    memberships = await fetch(_SPEC_BY_NAME["workspace_memberships"], "true" + ws_filter, ws_params)

    # KB 不过滤 deleted_at：`documents.kb_id` 有真外键指向它，删掉 KB 行不会连带删
    # 文档行（`documents_kb_fk` 是 RESTRICT），所以"已软删的 KB + 未删的文档"是库里
    # **真实存在**的状态（烟测删库后就会留下）。漏导这一行，重放会在 documents 处
    # 撞外键。文档反过来必须过滤：软删的文档没有重放价值。
    kbs = await fetch(_SPEC_BY_NAME["knowledge_bases"], "true" + ws_filter, ws_params)
    documents = await fetch(_SPEC_BY_NAME["documents"], "deleted_at IS NULL" + ws_filter, ws_params)
    if not documents:
        # 没有文档就没有版本/切片，但仍然要把空的 KB 和**成员关系**搬过去：
        # "KB 存在"与"人还在这个区里"是两条独立的事实，缺了成员关系迁完就是全员 403。
        # 成员关系有 principals 外键，所以这条分支也必须导 owner + 成员这两类 principals。
        base_ids = {row["owner_principal_id"] for row in workspaces}
        base_ids |= {row["user_id"] for row in memberships}
        return build_snapshot(
            {
                "principals": await fetch_principals(base_ids),
                "workspaces": workspaces,
                "workspace_memberships": memberships,
                "knowledge_bases": kbs,
                "documents": [],
                "document_versions": [],
                "document_blobs": [],
                "index_builds": [],
                "chunks": [],
            },
            source={"workspaces": [str(row["workspace_id"]) for row in workspaces]},
        )

    version_columns = ", ".join(_SPEC_BY_NAME["document_versions"].columns)
    versions = (
        (
            await session.execute(
                text(
                    f"SELECT {version_columns} FROM document_versions "
                    "WHERE document_id IN :docs ORDER BY document_version_id"
                ).bindparams(bindparam("docs", expanding=True)),
                {"docs": [row["document_id"] for row in documents]},
            )
        )
        .mappings()
        .all()
    )
    version_rows = [dict(row) for row in versions]

    blob_columns = ", ".join(_SPEC_BY_NAME["document_blobs"].columns)
    blobs = (
        (
            await session.execute(
                text(
                    f"SELECT {blob_columns} FROM document_blobs "
                    "WHERE document_version_id IN :versions ORDER BY document_version_id"
                ).bindparams(bindparam("versions", expanding=True)),
                {"versions": [row["document_version_id"] for row in version_rows]},
            )
        )
        .mappings()
        .all()
    )
    blob_rows = [dict(row) for row in blobs]

    build_columns = ", ".join(_SPEC_BY_NAME["index_builds"].columns)
    builds = (
        (
            await session.execute(
                text(
                    f"SELECT {build_columns} FROM index_builds "
                    "WHERE document_id IN :docs ORDER BY build_id"
                ).bindparams(bindparam("docs", expanding=True)),
                {"docs": [row["document_id"] for row in documents]},
            )
        )
        .mappings()
        .all()
    )
    build_rows = [dict(row) for row in builds]

    chunk_columns = ", ".join(_SPEC_BY_NAME["chunks"].columns)
    chunks: list[Any] = []
    if build_rows:
        chunks = (
            (
                await session.execute(
                    text(
                        f"SELECT {chunk_columns} FROM chunks "
                        "WHERE build_id IN :builds ORDER BY chunk_id"
                    ).bindparams(bindparam("builds", expanding=True)),
                    {"builds": [row["build_id"] for row in build_rows]},
                )
            )
            .mappings()
            .all()
        )
    chunk_rows = [dict(row) for row in chunks]

    # principals：只导被这些 workspace 与版本引用到的行（库里的其他用户不归本次迁移管）。
    principal_ids = {row["owner_principal_id"] for row in workspaces}
    principal_ids |= {row["created_by_principal_id"] for row in version_rows}
    # 成员也必须有 principals 行：`workspace_memberships.user_id` 是真外键，
    # 而"成员不是 owner、也没创建过版本"是最常见的情况（被邀请进来的普通成员）。
    principal_ids |= {row["user_id"] for row in memberships}
    principal_rows = await fetch_principals(principal_ids)

    # principles 只需"被引用"就导：owner 必须有真行（workspaces 有外键），
    # created_by 没有外键（0003 之前的历史行允许指向不存在的用户），缺了也不阻塞。
    exported_principals = {row["principal_id"] for row in principal_rows}
    missing_owners = sorted({row["owner_principal_id"] for row in workspaces} - exported_principals)
    if missing_owners:
        raise SnapshotError(
            f"源库缺少 principals 行 {missing_owners}，但 workspaces 引用了它们；"
            "先修数据再导出（否则目标库 workspaces 的外键会拒绝写入）"
        )

    return build_snapshot(
        {
            "principals": principal_rows,
            "workspaces": workspaces,
            "workspace_memberships": memberships,
            "knowledge_bases": kbs,
            "documents": documents,
            "document_versions": version_rows,
            "document_blobs": blob_rows,
            "index_builds": build_rows,
            "chunks": chunk_rows,
        },
        source={"workspaces": [str(row["workspace_id"]) for row in workspaces]},
    )


def _decode_rows(name: str, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    spec = _SPEC_BY_NAME[name]
    decoded: list[dict[str, Any]] = []
    for row in rows:
        out: dict[str, Any] = {}
        for column in spec.columns:
            if column not in row:
                raise SnapshotError(f"{name}: 快照缺列 '{column}'（导出版本不一致？）")
            value = row[column]
            if name == "document_blobs" and column == "content":
                out[column] = base64.b64decode(str(value))
            elif isinstance(value, str) and column.endswith("_at"):
                out[column] = datetime.fromisoformat(value)
            else:
                out[column] = value
        decoded.append(out)
    return decoded


async def replay_metadata(
    session: AsyncSession,
    snapshot: Mapping[str, Any],
    *,
    on_conflict: str = "error",
) -> dict[str, int]:
    """把快照写进目标库。返回每张表实际写入的行数。

    `on_conflict="error"` 是默认值：目标库已有同名主键就报错并整体回滚。
    静默覆盖会让"我迁的是哪份数据"变得不可回答，而且覆盖半个库比失败更难查。
    `on_conflict="skip"` 只在明确知道目标库是空的、只需补漏时用。
    """
    if on_conflict not in {"error", "skip"}:
        raise SnapshotError(f"on_conflict 只能是 error / skip，收到 {on_conflict!r}")
    assert_no_embedding(snapshot)

    tables = _tables(snapshot)
    written: dict[str, int] = {}
    for name in REPLAY_ORDER:
        rows = _decode_rows(name, tables.get(name, []))
        spec = _SPEC_BY_NAME[name]
        inserted = 0
        for row in rows:
            columns = ", ".join(spec.columns)
            placeholders = ", ".join(f":{column}" for column in spec.columns)
            clause = " ON CONFLICT DO NOTHING" if on_conflict == "skip" else ""
            statement = text(f"INSERT INTO {name} ({columns}) VALUES ({placeholders}){clause}")
            params = {column: row[column] for column in spec.columns}
            if name == "document_blobs":
                statement = text(
                    f"INSERT INTO {name} ({columns}) VALUES "
                    f"(:workspace_id, :document_version_id, decode(:content, 'base64')){clause}"
                )
                params = {
                    "workspace_id": row["workspace_id"],
                    "document_version_id": row["document_version_id"],
                    "content": base64.b64encode(row["content"]).decode("ascii"),
                }
            result = await session.execute(statement, params)
            inserted += int(getattr(result, "rowcount", 0) or 0)
        written[name] = inserted

    build_ids = [row["build_id"] for row in tables.get("index_builds", [])]
    if build_ids:
        # tsvector 是 content 的派生列，让 PG 按同一口径重算（不搬、不拼）。
        await session.execute(
            _TSVECTOR_SQL.bindparams(bindparam("builds", expanding=True)),
            {"builds": build_ids},
        )
    return written


# ------------------------------------------------- 导出为 SQL（给人看的那一份）
# 计划书 `:641`/`:571` 的判据字面是"导出 SQL 里 `embedding` 的 `INSERT` 行数 == 0"。
# JSON 快照是给程序用的，人肉核对要的是 SQL 文本，因此同时产出一份 .sql：
# 判据可以直接 `grep -c 'INSERT INTO'` / 搜 `embedding` 复核，不依赖脚本自证。

_TIMESTAMP_COLUMNS = frozenset(
    {"created_at", "updated_at", "activated_at", "retired_at", "deleted_at"}
)


def _sql_literal(column: str, value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if column in _TIMESTAMP_COLUMNS:
        return f"'{value}'::timestamptz"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


#: 快照里被编码成 base64 的二进制列。`_row_to_json` 编码，`_decode_rows` 解码，
#: `render_sql` 必须还原成 `decode(..., 'base64')`——三处必须一致。
_BYTEA_COLUMNS: frozenset[tuple[str, str]] = frozenset({("document_blobs", "content")})

#: jsonb 列：值在快照里是普通 JSON 对象（dict/list），`render_sql` 必须渲染成合法
#: JSON 文本再 `::jsonb` 转型，不能走 `_sql_literal` 的 `str(value)`。
#: 目前只有 0014 加的 `chunks.locator`——它是本文件里第一个 jsonb 列，所以这条路径
#: 以前从未被走过：`str({"kind": "para"})` 产出 `{'kind': 'para'}`（单引号 + Python 的
#: `True`），PG 解析 jsonb 会直接报错，等于"导出的 SQL 一执行就失败"。
_JSON_COLUMNS: frozenset[tuple[str, str]] = frozenset({("chunks", "locator")})


def _json_literal(value: Any) -> str:
    """把一个 jsonb 值渲染成 SQL 字面量：`'<json>'::jsonb`。

    `ensure_ascii=False`：中文工作表名（`"销售数据"`）保持可读，不转义成 `\\uXXXX`；
    转义后虽然仍是合法 JSON，但人肉审阅 `render_sql` 产物的意义就没有了。
    单引号按 SQL 规则翻倍，JSON 字符串里的 `'` 才不会截断字面量。
    """
    return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"


def _sql_value(table: str, column: str, value: Any) -> str:
    """渲染一个列值。二进制列与 jsonb 列都不能走 `_sql_literal` 的字符串形状。

    `document_blobs.content` 在 JSON 里是 base64 字符串（JSON 没有二进制类型）。
    如果直接渲染成 `'BASE64...'` 再 INSERT 进 bytea 列，PG 会按 bytea 的 escape
    格式把它当**字面文本**存进去——语句不报错，但恢复后的 blob 是 base64 字符串，
    不是原字节。JSON 重放路径一直有 `decode(:content,'base64')`，SQL 路径没有，
    这个缺口就是这样来的：两条路径对同一份快照做出不同结果。

    jsonb 同理：`chunks.locator` 是 dict，必须 `json.dumps` + `::jsonb`
    （见 `_JSON_COLUMNS` 的注释）。
    """
    if (table, column) in _BYTEA_COLUMNS:
        return "NULL" if value is None else f"decode('{value}', 'base64')"
    if (table, column) in _JSON_COLUMNS:
        return "NULL" if value is None else _json_literal(value)
    return _sql_literal(column, value)


def render_sql(snapshot: Mapping[str, Any], *, target_dsn_hint: str = "") -> str:
    """把快照渲染成可人工审阅、可 psql 执行的 INSERT 脚本。"""
    lines = [
        "-- PyKnora 迁库快照（任务17：只含元信息与原文，不含向量）",
        f"-- 生成时间：{snapshot.get('exported_at')}",
        f"-- 来源：{snapshot.get('source')}",
        f"-- 目标（提示，不含口令）：{target_dsn_hint}",
        "-- 本文件里不应出现任何 embedding / tsvector 列；出现即合约被破坏。",
        "BEGIN;",
    ]
    tables = _tables(snapshot)
    for name in REPLAY_ORDER:
        rows = tables.get(name, [])
        if not rows:
            continue
        spec = _SPEC_BY_NAME[name]
        columns = ", ".join(spec.columns)
        lines.append(f"\n-- {name}: {len(rows)} 行")
        for row in rows:
            values = ", ".join(_sql_value(name, column, row.get(column)) for column in spec.columns)
            lines.append(f"INSERT INTO {name} ({columns}) VALUES ({values});")
    lines.append("\nCOMMIT;")
    return "\n".join(lines) + "\n"


def count_embedding_inserts(sql_text: str) -> int:
    """统计 SQL 文本里"INSERT 的**列清单**涉及向量列"的语句数。计划书要求这个值是 0。

    只看列清单、不看整行：切片正文里合法地可能出现 "embedding" 这个词，
    按整行搜会把"讲向量的文档"误判成"搬了向量"，判据就失效了。
    """
    offenders = 0
    for line in sql_text.splitlines():
        stripped = line.lstrip()
        if not stripped.upper().startswith("INSERT INTO"):
            continue
        open_paren = stripped.find("(")
        close_paren = stripped.find(")", open_paren + 1)
        if open_paren < 0 or close_paren < 0:
            continue
        columns = {
            column.strip().strip('"').lower()
            for column in stripped[open_paren + 1 : close_paren].split(",")
        }
        if columns & FORBIDDEN_COLUMNS:
            offenders += 1
    return offenders


# ---------------------------------------------------------------- 校验


@dataclass(frozen=True, slots=True)
class Check:
    """一条校验结果。`ok=False` 时 `detail` 必须能定位到具体文档。"""

    name: str
    ok: bool
    detail: str


_PROFILE_FIELDS = (
    "embedding_model_key",
    "embedding_dim",
    "chunk_size",
    "chunk_overlap",
    "normalize",
)

_ACTIVE_BUILD_SQL = text(
    "SELECT document_id, kb_id, active_build_id, active_index_version "
    "FROM documents WHERE workspace_id = :workspace_id AND deleted_at IS NULL"
)

_BOUNDARY_SQL = text(
    "SELECT chunk_ordinal, char_start, char_end, content FROM chunks "
    "WHERE build_id = :build_id ORDER BY chunk_ordinal"
)

_LATEST_BUILD_SQL = text(
    "SELECT build_id, index_version FROM index_builds "
    "WHERE document_id = :document_id ORDER BY index_version DESC LIMIT 1"
)

_KB_SQL = text(
    "SELECT kb_id, workspace_id, name, embedding_model_key, embedding_dim, "
    "chunk_size, chunk_overlap, normalize, deleted_at FROM knowledge_bases "
    "WHERE workspace_id = :workspace_id"
)

# `v_active_chunks` 不 join `knowledge_bases`：KB 被软删后，它的切片仍会露在视图里
# （只要文档没删、build 是 active、向量非空）。迁移不给已删 KB 重算向量，所以计数
# 必须按"KB 未删"过滤，否则源库那条幽灵行会让校验必然失败。
_ACTIVE_CHUNK_COUNT_SQL = text(
    "SELECT count(*) AS n FROM v_active_chunks v "
    "JOIN knowledge_bases k ON k.kb_id = v.kb_id AND k.workspace_id = v.workspace_id "
    "WHERE v.workspace_id = :workspace_id AND k.deleted_at IS NULL"
)

_MISSING_VECTOR_SQL = text(
    "SELECT count(*) AS n FROM chunks WHERE build_id IN :builds AND embedding IS NULL"
)

_ALL_NULL_VECTOR_SQL = text(
    "SELECT count(*) AS n FROM chunks WHERE build_id IN :builds AND embedding IS NOT NULL"
)

_MEMBERSHIP_SQL = text(
    "SELECT membership_id, user_id, role FROM workspace_memberships "
    "WHERE workspace_id = :workspace_id ORDER BY membership_id"
)

_VERSION_COUNT_SQL = text(
    "SELECT count(*) AS n FROM document_versions WHERE workspace_id = :workspace_id"
)

#: blob 的"行数 + 字节总数"。字节数是抓"二进制被写成 base64 文本"的关键：
#: base64 的长度是原文的 4/3 倍，行数完全一样，只有字节数会露馅。
_BLOB_DIGEST_SQL = text(
    "SELECT count(*) AS n, coalesce(sum(octet_length(content)), 0) AS total "
    "FROM document_blobs WHERE workspace_id = :workspace_id"
)


async def _boundaries(session: AsyncSession, build_id: Any) -> list[tuple[Any, ...]]:
    rows = (await session.execute(_BOUNDARY_SQL, {"build_id": build_id})).all()
    return [(row[0], row[1], row[2], row[3]) for row in rows]


async def _first_mismatch_ordinal(
    left: Sequence[tuple[Any, ...]], right: Sequence[tuple[Any, ...]]
) -> Any:
    """首个边界不一致的 ordinal；完全一致返回 'n/a'。"""
    for a, b in zip(left, right, strict=False):
        if a != b:
            return a[0]
    return "n/a"


async def verify_metadata(
    source: AsyncSession,
    target: AsyncSession,
    *,
    workspace_id: UUID,
) -> list[Check]:
    """比对源/目标同属一个 workspace 的迁移结果。

    刻意不比对 `index_version`：目标库走一次 reindex 之后版本号必然 +1，
    那是设计出来的差异（新旧 embedding 永不混在同一个 build 里），不是错误。
    真正要比的是"切片边界与正文一致""profile 一致""目标库的向量是重算出来的"。
    """
    checks: list[Check] = []

    src_kbs = (await source.execute(_KB_SQL, {"workspace_id": str(workspace_id)})).mappings().all()
    dst_kbs = (await target.execute(_KB_SQL, {"workspace_id": str(workspace_id)})).mappings().all()
    src_by_kb = {row["kb_id"]: row for row in src_kbs}
    dst_by_kb = {row["kb_id"]: row for row in dst_kbs}
    checks.append(
        Check(
            "knowledge_bases 集合一致",
            src_by_kb.keys() == dst_by_kb.keys(),
            f"源 {sorted(map(str, src_by_kb))} / 目标 {sorted(map(str, dst_by_kb))}",
        )
    )
    for kb_id, src_kb in src_by_kb.items():
        dst_kb = dst_by_kb.get(kb_id)
        if dst_kb is None:
            continue
        diff = {
            field: (src_kb[field], dst_kb[field])
            for field in _PROFILE_FIELDS
            if src_kb[field] != dst_kb[field]
        }
        checks.append(
            Check(
                f"KB profile 五字段一致（{kb_id}）",
                not diff,
                "一致" if not diff else f"不一致 {diff}",
            )
        )

    src_docs = (
        (await source.execute(_ACTIVE_BUILD_SQL, {"workspace_id": str(workspace_id)}))
        .mappings()
        .all()
    )
    dst_docs = {
        row["document_id"]: row
        for row in (await target.execute(_ACTIVE_BUILD_SQL, {"workspace_id": str(workspace_id)}))
        .mappings()
        .all()
    }
    checks.append(
        Check(
            "documents 集合一致",
            {row["document_id"] for row in src_docs} == set(dst_docs),
            f"源 {len(src_docs)} 篇 / 目标 {len(dst_docs)} 篇",
        )
    )

    boundary_failures: list[str] = []
    for src_doc in src_docs:
        dst_doc = dst_docs.get(src_doc["document_id"])
        if dst_doc is None:
            continue
        src_bounds = await _boundaries(source, src_doc["active_build_id"])
        latest = (
            (await target.execute(_LATEST_BUILD_SQL, {"document_id": str(src_doc["document_id"])}))
            .mappings()
            .first()
        )
        if latest is None:
            boundary_failures.append(f"{src_doc['document_id']}: 目标库没有 build")
            continue
        dst_bounds = await _boundaries(target, latest["build_id"])
        if src_bounds != dst_bounds:
            first = await _first_mismatch_ordinal(src_bounds, dst_bounds)
            boundary_failures.append(
                f"{src_doc['document_id']}: 源 {len(src_bounds)} 片 / 目标 {len(dst_bounds)} 片，"
                f"首个差异 ordinal={first}"
            )
    checks.append(
        Check(
            "活动 chunk 边界与正文逐项一致",
            not boundary_failures,
            "全部一致" if not boundary_failures else "; ".join(boundary_failures),
        )
    )

    src_count = (
        await source.execute(_ACTIVE_CHUNK_COUNT_SQL, {"workspace_id": str(workspace_id)})
    ).scalar_one()
    dst_count = (
        await target.execute(_ACTIVE_CHUNK_COUNT_SQL, {"workspace_id": str(workspace_id)})
    ).scalar_one()
    checks.append(
        Check(
            "v_active_chunks 行数一致",
            int(src_count) == int(dst_count),
            f"源 {src_count} / 目标 {dst_count}",
        )
    )

    # 软删的 KB 不参与向量重算（`reindex_knowledge_base` 按 `deleted_at` 取不到 profile，
    # 这是设计边界而不是缺陷），所以向量相关校验只看"目标库里仍然存活的 KB"。
    live_kb_ids = {kb_id for kb_id, row in dst_by_kb.items() if row["deleted_at"] is None}
    live_builds = [
        row["active_build_id"]
        for row in dst_docs.values()
        if row["kb_id"] in live_kb_ids and row["active_build_id"]
    ]
    if live_builds:
        missing = (
            await target.execute(
                _MISSING_VECTOR_SQL.bindparams(bindparam("builds", expanding=True)),
                {"builds": [str(value) for value in live_builds]},
            )
        ).scalar_one()
        checks.append(
            Check(
                "存活 KB 的活动 build 向量已重算（无 NULL）",
                int(missing) == 0,
                f"缺向量 {missing} 行" if missing else "无缺失",
            )
        )

    deleted_builds = [
        row["active_build_id"]
        for row in dst_docs.values()
        if row["kb_id"] not in live_kb_ids and row["active_build_id"]
    ]
    if deleted_builds:
        still_has = (
            await target.execute(
                _ALL_NULL_VECTOR_SQL.bindparams(bindparam("builds", expanding=True)),
                {"builds": [str(value) for value in deleted_builds]},
            )
        ).scalar_one()
        checks.append(
            Check(
                "已软删 KB 的切片未搬向量（目标库保持 NULL）",
                int(still_has) == 0,
                f"{still_has} 行带向量（迁移不该带）" if still_has else "全部为 NULL，符合预期",
            )
        )
    # 成员关系不比对 membership_id（两边都是 gen_random_uuid，必然不同），
    # 比的是"谁、什么角色"这个集合。
    ws_param = {"workspace_id": str(workspace_id)}
    src_members = (await source.execute(_MEMBERSHIP_SQL, ws_param)).mappings().all()
    dst_members = (await target.execute(_MEMBERSHIP_SQL, ws_param)).mappings().all()
    src_member_set = {(str(row["user_id"]), row["role"]) for row in src_members}
    dst_member_set = {(str(row["user_id"]), row["role"]) for row in dst_members}
    checks.append(
        Check(
            "workspace_memberships 一致（成员身份与角色）",
            src_member_set == dst_member_set,
            f"源 {len(src_member_set)} 人 / 目标 {len(dst_member_set)} 人"
            if src_member_set == dst_member_set
            else f"源 {len(src_member_set)} / 目标 {len(dst_member_set)}，"
            f"差异 {sorted(src_member_set ^ dst_member_set)}",
        )
    )

    src_versions = (
        await source.execute(_VERSION_COUNT_SQL, {"workspace_id": str(workspace_id)})
    ).scalar_one()
    dst_versions = (
        await target.execute(_VERSION_COUNT_SQL, {"workspace_id": str(workspace_id)})
    ).scalar_one()
    checks.append(
        Check(
            "document_versions 行数一致",
            int(src_versions) == int(dst_versions),
            f"源 {src_versions} / 目标 {dst_versions}",
        )
    )

    # 这一条是 `render_sql` 的 bytea 缺口唯一的探测手段：把 base64 文本当字节写进
    # bytea 列不会报错、行数也一样，只有 `octet_length` 会变成 4/3 倍。
    src_blob = (await source.execute(_BLOB_DIGEST_SQL, {"workspace_id": str(workspace_id)})).one()
    dst_blob = (await target.execute(_BLOB_DIGEST_SQL, {"workspace_id": str(workspace_id)})).one()
    blob_ok = (int(src_blob[0]), int(src_blob[1])) == (int(dst_blob[0]), int(dst_blob[1]))
    checks.append(
        Check(
            "document_blobs 行数与字节总数一致（二进制未被编码破坏）",
            blob_ok,
            f"源 {src_blob[0]} 行 / {src_blob[1]} 字节，目标 {dst_blob[0]} 行 / {dst_blob[1]} 字节",
        )
    )

    return checks
