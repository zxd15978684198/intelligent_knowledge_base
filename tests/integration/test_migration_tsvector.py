"""成员5 迁库路径的 tsvector 口径：真库行为回归。

复核来由（2026-10-30）：中文 FTS 修好（`migrations/0007_cjk_bigram_fts.sql`）后，
快照恢复与重建索引两处都必须在**新库**上重算 `tsvector`，且必须走
`pyknora_cjk_bigram`。在这之前没有任何用例验证过"恢复出来的索引中文搜得到"——
把恢复 SQL 换回裸 `content` 的旧口径，测试也不会红（`tests/unit/
test_migration_snapshot.py` 只断言导出时**拒绝**搬运 tsvector 列，没断言重算口径）。

这里用真库把那条 SQL 跑一遍，再用检索入口反过来验证它写出的词元能被中文短词命中：
口径错了就搜不到，不报错，这正是最需要钉住的情形。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import bindparam
from sqlalchemy import text as sql

from pyknora.core.scope import WORKSPACE_A, Scope
from pyknora.db.repos.reads import SqlChunkSearchRepo
from pyknora.services.ingest.embedder import HashingEmbedder
from pyknora.services.migration.snapshot import _TSVECTOR_SQL

KB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
DIM = 512


async def _seed_build_with_null_tsvector(session, scope: Scope, kb_id: UUID, text: str) -> UUID:
    """插一条 chunk，`tsvector` 留 NULL——模拟快照重放后的现场。

    快照导出时 `FORBIDDEN_COLUMNS` 含 `tsvector`，所以恢复进新库的 chunk
    `tsvector` 一定是 NULL，等恢复末尾那条 UPDATE 补。这里就复刻那个现场。
    """
    doc_id, build_id, version_id, attempt_id, chunk_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    vec = (await HashingEmbedder(dim=DIM).embed([text]))[0]
    vec_literal = "[" + ",".join(f"{x:.8f}" for x in vec) + "]"

    await session.execute(
        sql(
            """
            INSERT INTO documents
                (document_id, workspace_id, kb_id, desired_document_version_id,
                 active_document_version_id, active_build_id, active_index_version)
            VALUES (:doc, :ws, :kb, :ver, :ver, :build, 1)
            """
        ),
        {
            "doc": doc_id,
            "ws": scope.workspace_id,
            "kb": kb_id,
            "ver": version_id,
            "build": build_id,
        },
    )
    await session.execute(
        sql(
            """
            INSERT INTO index_builds
                (build_id, workspace_id, document_id, index_version, attempt_id,
                 embedding_model_key, embedding_dim, chunk_size, chunk_overlap,
                 chunk_count, state, activated_at)
            VALUES (:build, :ws, :doc, 1, :attempt, 'test/hashing', :dim,
                    512, 64, 1, 'active', now())
            """
        ),
        {
            "build": build_id,
            "ws": scope.workspace_id,
            "doc": doc_id,
            "attempt": attempt_id,
            "dim": DIM,
        },
    )
    await session.execute(
        sql(
            """
            INSERT INTO chunks
                (chunk_id, workspace_id, document_id, build_id, index_version,
                 attempt_id, chunk_ordinal, content, content_hash, embedding, tsvector)
            VALUES (:chunk, :ws, :doc, :build, 1, :attempt, 0, :content,
                    md5(:content), CAST(:vec AS vector), NULL)
            """
        ),
        {
            "chunk": chunk_id,
            "ws": scope.workspace_id,
            "doc": doc_id,
            "build": build_id,
            "attempt": attempt_id,
            "content": text,
            "vec": vec_literal,
        },
    )
    await session.commit()
    return build_id


async def _backfill(session, build_ids: list[UUID]) -> int:
    """跑与快照恢复末尾完全同一个语句对象（不是复制一份 SQL）。"""
    result = await session.execute(
        _TSVECTOR_SQL.bindparams(bindparam("builds", expanding=True)),
        {"builds": build_ids},
    )
    await session.commit()
    return int(result.rowcount or 0)


async def test_restore_backfill_makes_chinese_subword_searchable(session) -> None:
    scope = Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A)
    build_id = await _seed_build_with_null_tsvector(
        session, scope, KB_ID, "银狐锚点报销标准：一线城市住宿上限 500 元"
    )
    repo = SqlChunkSearchRepo(session)

    # 回填之前：中文短词搜不到（这正是"恢复完却没重建词元"的现场）。
    assert await repo.search_keyword(scope, KB_ID, "报销", limit=5) == []

    written = await _backfill(session, [build_id])
    assert written == 1

    # 回填之后：同一个中文短词必须命中。旧口径（裸 content）在这里会返回空。
    hits = await repo.search_keyword(scope, KB_ID, "报销", limit=5)
    assert len(hits) == 1
    assert hits[0].kb_id == KB_ID

    # 更短的二字词也要能命中——这是 bigram 与"整段当一个词元"的分水岭。
    assert len(await repo.search_keyword(scope, KB_ID, "住宿", limit=5)) == 1


async def test_restore_backfill_only_touches_given_builds(session) -> None:
    """`WHERE build_id IN :builds` 必须真的收窄，否则恢复一个文档会扫全库。"""
    scope = Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A)
    target = await _seed_build_with_null_tsvector(session, scope, KB_ID, "赤鹿锚点：报销标准")
    other = await _seed_build_with_null_tsvector(session, scope, KB_ID, "白鹤锚点：报销标准")

    assert await _backfill(session, [target]) == 1

    rows = (
        await session.execute(
            sql(
                "SELECT build_id, tsvector IS NULL AS missing FROM chunks "
                "WHERE build_id IN (:a, :b) ORDER BY build_id"
            ),
            {"a": target, "b": other},
        )
    ).all()
    by_build = {str(row.build_id): bool(row.missing) for row in rows}

    assert by_build[str(target)] is False
    assert by_build[str(other)] is True
