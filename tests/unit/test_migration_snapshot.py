"""任务17：迁库快照的纯逻辑单测（不需要数据库）。

覆盖的是"合约"，不是"能跑"：

1. 快照里不许出现向量/派生列——列名检测，且**正文里出现 `embedding` 一词不算**；
2. 导出 SQL 中涉及 embedding/tsvector 的 INSERT 行数必须能统计到 0（计划书 `:571` 判据）；
3. SQL 字面量转义正确（引号、NULL、bool、timestamptz），否则人工执行会炸；
4. 重放参数非法时立刻拒绝，不走数据库。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from pyknora.services.migration.snapshot import (
    FORBIDDEN_COLUMNS,
    REPLAY_ORDER,
    TABLES,
    SnapshotError,
    _decode_rows,
    _sql_literal,
    assert_no_embedding,
    build_snapshot,
    count_embedding_inserts,
    render_sql,
    replay_metadata,
)

_WS = uuid.UUID("11111111-1111-1111-1111-111111111111")
_KB = uuid.UUID("22222222-2222-2222-2222-222222222222")
_DOC = uuid.UUID("33333333-3333-3333-3333-333333333333")
_BUILD = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _minimal_tables() -> dict[str, list[dict[str, object]]]:
    """一份最小但外键自洽的快照行集合（值不重要，用于跑通渲染与解码）。"""
    now = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    return {
        "principals": [
            {
                "principal_id": _WS,
                "username": "m2",
                "email": "m2@example.com",
                "password_hash": "argon2$x",
                "display_name": "成员2",
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        ],
        "workspaces": [
            {
                "workspace_id": _WS,
                "name": "教学区",
                "owner_principal_id": _WS,
                "is_public": False,
                "created_at": now,
            }
        ],
        "knowledge_bases": [
            {
                "kb_id": _KB,
                "workspace_id": _WS,
                "name": "测试库",
                "embedding_model_key": "hashing-512",
                "embedding_dim": 512,
                "chunk_size": 800,
                "chunk_overlap": 80,
                "normalize": True,
                "active_index_build_id": _BUILD,
                "row_version": 1,
                "generation": 1,
                "deleted_at": None,
                "created_at": now,
                "updated_at": now,
            }
        ],
        "documents": [
            {
                "document_id": _DOC,
                "workspace_id": _WS,
                "kb_id": _KB,
                "desired_document_version_id": None,
                "active_document_version_id": None,
                "active_build_id": _BUILD,
                "active_index_version": 1,
                "generation": 1,
                "row_version": 1,
                "deleted_at": None,
                "created_at": now,
            }
        ],
        "document_versions": [],
        "document_blobs": [],
        "index_builds": [
            {
                "build_id": _BUILD,
                "workspace_id": _WS,
                "document_id": _DOC,
                "index_version": 1,
                "attempt_id": uuid.UUID("55555555-5555-5555-5555-555555555555"),
                "embedding_model_key": "hashing-512",
                "embedding_dim": 512,
                "chunk_size": 800,
                "chunk_overlap": 80,
                "normalize": True,
                "chunk_count": 1,
                "state": "active",
                "created_at": now,
                "activated_at": now,
                "retired_at": None,
            }
        ],
        "chunks": [
            {
                "chunk_id": uuid.UUID("66666666-6666-6666-6666-666666666666"),
                "workspace_id": _WS,
                "document_id": _DOC,
                "build_id": _BUILD,
                "index_version": 1,
                "attempt_id": uuid.UUID("55555555-5555-5555-5555-555555555555"),
                # 正文里故意出现 embedding 一词：它不该被当成"搬了向量"。
                "content": "embedding 是向量，但这里只有正文",
                "content_hash": "abc",
                "page_no": 1,
                "heading_path": "1. 引言",
                "char_start": 0,
                "char_end": 12,
                "token_count": 5,
                "locator_ref": "p1:c0-12",
            }
        ],
    }


def test_no_table_spec_declares_a_vector_column() -> None:
    """结构断言：没有人往 TABLES 的列清单里塞向量/派生列。"""
    for spec in TABLES:
        leaked = FORBIDDEN_COLUMNS.intersection(spec.columns)
        assert not leaked, f"{spec.name} 声明了 {leaked}"


def test_build_snapshot_accepts_text_mentioning_embedding() -> None:
    """正文里出现 "embedding" 一词不构成"搬向量"——按列名判定，不按字符串搜。"""
    snapshot = build_snapshot(_minimal_tables())
    assert snapshot["format"] == "pyknora-metadata-snapshot"
    assert snapshot["version"] == 1
    chunk = snapshot["tables"]["chunks"][0]
    assert "embedding" in chunk["content"]
    assert "embedding" not in chunk
    assert isinstance(chunk["chunk_id"], str)


def test_build_snapshot_rejects_vector_column() -> None:
    tables = _minimal_tables()
    tables["chunks"][0]["embedding"] = [0.1, 0.2]
    with pytest.raises(SnapshotError, match="禁止导出"):
        build_snapshot(tables)


def test_build_snapshot_rejects_tsvector_column() -> None:
    tables = _minimal_tables()
    tables["chunks"][0]["tsvector"] = "'x':1"
    with pytest.raises(SnapshotError, match="tsvector"):
        build_snapshot(tables)


def test_build_snapshot_rejects_unknown_table_and_cache() -> None:
    with pytest.raises(SnapshotError, match="未知的表"):
        build_snapshot({"chunkz": []})
    with pytest.raises(SnapshotError, match="embedding_cache"):
        assert_no_embedding({"tables": {"embedding_cache": [{"content_hash": "x"}]}})


def test_render_sql_has_zero_embedding_inserts() -> None:
    """计划书 `:571` 判据：导出 SQL 中涉及 embedding 的 INSERT 行数 == 0。"""
    sql_text = render_sql(build_snapshot(_minimal_tables()))
    assert count_embedding_inserts(sql_text) == 0
    assert "INSERT INTO chunks" in sql_text
    assert "BEGIN;" in sql_text and "COMMIT;" in sql_text
    assert "INSERT INTO chunks (chunk_id, workspace_id, document_id" in sql_text
    # 正文里那个 "embedding" 词照样在，但它不在列清单里——判据看的是列，不是词。
    assert "embedding 是向量" in sql_text


def test_count_embedding_inserts_detects_a_leak() -> None:
    """反向验证判据本身有效：手写的"带向量 INSERT"必须被数出来。"""
    leaky = "INSERT INTO chunks (chunk_id, embedding) VALUES ('a', '[0.1]');\n"
    assert count_embedding_inserts(leaky) == 1


def test_sql_literal_escapes_and_types() -> None:
    assert _sql_literal("content", "O'Reilly") == "'O''Reilly'"
    assert _sql_literal("page_no", None) == "NULL"
    assert _sql_literal("normalize", True) == "TRUE"
    assert _sql_literal("normalize", False) == "FALSE"
    assert _sql_literal("page_no", 7) == "7"
    assert _sql_literal("created_at", "2026-10-30T12:00:00+00:00") == (
        "'2026-10-30T12:00:00+00:00'::timestamptz"
    )


def test_render_sql_order_follows_foreign_keys() -> None:
    sql_text = render_sql(build_snapshot(_minimal_tables()))
    positions = [
        sql_text.index(f"INSERT INTO {name} ")
        for name in REPLAY_ORDER
        if (f"INSERT INTO {name} " in sql_text)
    ]
    assert positions == sorted(positions), "重放顺序必须先父后子，否则外键会拒绝"


def test_decode_rows_requires_every_declared_column() -> None:
    spec = next(item for item in TABLES if item.name == "workspaces")
    missing = {column: "x" for column in spec.columns if column != "name"}
    with pytest.raises(SnapshotError, match="缺列 'name'"):
        _decode_rows("workspaces", [missing])


def test_replay_rejects_unknown_conflict_policy_without_touching_db() -> None:
    """参数非法时在连库之前就拒绝（session=None 也能走到抛错）。"""
    snapshot = build_snapshot(_minimal_tables())
    with pytest.raises(SnapshotError, match="on_conflict"):
        asyncio.run(replay_metadata(None, snapshot, on_conflict="overwrite"))  # type: ignore[arg-type]


def test_replay_refuses_snapshot_with_vectors_before_touching_db() -> None:
    snapshot = build_snapshot(_minimal_tables())
    snapshot["tables"]["chunks"][0]["embedding"] = [0.1]
    with pytest.raises(SnapshotError, match="禁止导出"):
        asyncio.run(replay_metadata(None, snapshot))  # type: ignore[arg-type]
