"""C2 追加面的 SQL 语义（需要真 PG，宿主 5433）。

只钉下游真正依赖的四条语义，不重复覆盖成员2 已有的检索能力：

1. `get_chunks` 只认**当前活动** build 的切片——retired 的 id 必须缺席；
2. `list_chunks_by_build` 读得到 retired build（I1 的受控例外）；
3. 历史读取仍然受**工作区隔离**与**文档删除**约束；
4. `list_builds` 倒序、`get_version` 跨区返回 None；
5. **知识库软删**同样使切片整体不可见（视图与受控例外两条路都要，S1 的回归）。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import text

from pyknora.core.scope import Scope
from pyknora.db.repos.reads import SqlChunkRepo, SqlDocumentRepo, SqlIndexBuildRepo

_DIM = 512
_ZERO_VEC = "[" + ",".join(["0"] * _DIM) + "]"


async def _seed_two_builds(session, scope: Scope, kb_id: UUID) -> dict[str, UUID | list[UUID]]:
    """造一份文档 + 两个 build：旧 build(retired, 2 片) 与 新 build(active, 2 片)。"""
    doc_id = uuid4()
    version_id = uuid4()
    attempt_id = uuid4()
    build_old = uuid4()
    build_new = uuid4()
    old_chunks = [uuid4(), uuid4()]
    new_chunks = [uuid4(), uuid4()]

    # 顺序不能反：document_versions / index_builds / chunks 三条都有
    # (workspace_id, document_id) → documents 的复合外键（migrations/0001_init.sql:66/92/120），
    # 而 documents 对版本和 build 只是普通 uuid 列、**没有反向外键**。
    # 所以先插 documents，再插版本、build、切片。
    await session.execute(
        text(
            """
            INSERT INTO documents
                (document_id, workspace_id, kb_id, desired_document_version_id,
                 active_document_version_id, active_build_id, active_index_version)
            VALUES (:doc, :ws, :kb, :ver, :ver, :build, 2)
            """
        ),
        {
            "doc": doc_id,
            "ws": scope.workspace_id,
            "kb": kb_id,
            "ver": version_id,
            "build": build_new,
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO document_versions
                (document_version_id, workspace_id, document_id, content_hash,
                 byte_size, object_key, parse_status, created_by_principal_id,
                 ingestion_source)
            VALUES (:ver, :ws, :doc, md5('v1'), 12, 'blobs/v1', 'parsed', :principal, 'upload')
            """
        ),
        {
            "ver": version_id,
            "ws": scope.workspace_id,
            "doc": doc_id,
            "principal": scope.principal_id,
        },
    )

    for build_id, index_version, state, age_clause in (
        (build_old, 1, "retired", "now() - interval '1 hour'"),
        (build_new, 2, "active", "now()"),
    ):
        await session.execute(
            text(
                f"""
                INSERT INTO index_builds
                    (build_id, workspace_id, document_id, index_version, attempt_id,
                     embedding_model_key, embedding_dim, chunk_size, chunk_overlap,
                     chunk_count, state, created_at, activated_at)
                VALUES (:build, :ws, :doc, :ver, :attempt, 'test/hashing', :dim,
                        512, 64, 2, :state, {age_clause},
                        CASE WHEN :state = 'active' THEN now() ELSE NULL END)
                """
            ),
            {
                "build": build_id,
                "ws": scope.workspace_id,
                "doc": doc_id,
                "ver": index_version,
                "attempt": attempt_id,
                "dim": _DIM,
                "state": state,
            },
        )

    for build_id, index_version, chunk_ids, base, label in (
        (build_old, 1, old_chunks, 0, "旧 build 片段"),
        (build_new, 2, new_chunks, 10, "新 build 片段"),
    ):
        for offset, chunk_id in enumerate(chunk_ids):
            content = f"{label}{'一二'[offset]}"
            await session.execute(
                text(
                    """
                    INSERT INTO chunks
                        (chunk_id, workspace_id, document_id, build_id, index_version,
                         attempt_id, chunk_ordinal, content, content_hash, embedding)
                    VALUES (:chunk, :ws, :doc, :build, :ver, :attempt, :ordinal,
                            :content, md5(:content), CAST(:vec AS vector))
                    """
                ),
                {
                    "chunk": chunk_id,
                    "ws": scope.workspace_id,
                    "doc": doc_id,
                    "build": build_id,
                    "ver": index_version,
                    "attempt": attempt_id,
                    "ordinal": base + offset,
                    "content": content,
                    "vec": _ZERO_VEC,
                },
            )

    await session.commit()
    return {
        "doc": doc_id,
        "version": version_id,
        "old_build": build_old,
        "new_build": build_new,
        "old_chunks": old_chunks,
        "new_chunks": new_chunks,
    }


async def test_get_chunks_returns_only_active_build(session, scope_a, kb_id) -> None:
    """拿 retired build 的 chunk_id 去查活动视图 → 它必须缺席，而不是返回旧内容。"""
    ids = await _seed_two_builds(session, scope_a, kb_id)
    repo = SqlChunkRepo(session)

    old_chunks = ids["old_chunks"]
    new_chunks = ids["new_chunks"]
    got = await repo.get_chunks(scope_a, [new_chunks[0], old_chunks[0], new_chunks[1]])

    assert [c.chunk_id for c in got] == [new_chunks[0], new_chunks[1]]
    assert {c.build_id for c in got} == {ids["new_build"]}


async def test_get_chunks_dedups_and_keeps_empty_input_cheap(session, scope_a, kb_id) -> None:
    ids = await _seed_two_builds(session, scope_a, kb_id)
    repo = SqlChunkRepo(session)
    new_chunks = ids["new_chunks"]

    assert await repo.get_chunks(scope_a, []) == []
    dup = await repo.get_chunks(scope_a, [new_chunks[0], new_chunks[0]])
    assert [c.chunk_id for c in dup] == [new_chunks[0]]


async def test_list_chunks_by_build_reads_retired_build(session, scope_a, kb_id) -> None:
    """I1 的受控例外：retired build 在视图里不存在，但历史引用必须读得到。"""
    ids = await _seed_two_builds(session, scope_a, kb_id)
    repo = SqlChunkRepo(session)

    old = await repo.list_chunks_by_build(scope_a, ids["old_build"])
    assert [c.content for c in old] == ["旧 build 片段一", "旧 build 片段二"]
    assert {c.build_id for c in old} == {ids["old_build"]}

    active_view = await repo.list_active_chunks(scope_a, ids["doc"])
    assert {c.build_id for c in active_view} == {ids["new_build"]}


async def test_list_chunks_by_build_is_workspace_scoped(session, scope_a, scope_b, kb_id) -> None:
    """B 区拿 A 区的 build_id → 空，不能靠 join 漏出别人的切片。"""
    ids = await _seed_two_builds(session, scope_a, kb_id)
    repo = SqlChunkRepo(session)

    assert await repo.list_chunks_by_build(scope_b, ids["old_build"]) == []
    assert await repo.list_chunks_by_build(scope_b, ids["new_build"]) == []


async def test_list_chunks_by_build_hides_deleted_document(session, scope_a, kb_id) -> None:
    """文档删除后历史版本也不能成为旁路——整条链路读不到。"""
    ids = await _seed_two_builds(session, scope_a, kb_id)
    repo = SqlChunkRepo(session)

    await session.execute(
        text("UPDATE documents SET deleted_at = now() WHERE document_id = :doc"),
        {"doc": ids["doc"]},
    )
    await session.commit()

    assert await repo.list_chunks_by_build(scope_a, ids["old_build"]) == []
    assert await repo.list_chunks_by_build(scope_a, ids["new_build"]) == []


async def test_soft_deleted_kb_hides_chunks_everywhere(session, scope_a, kb_id) -> None:
    """知识库软删 → 活动视图与历史读取都必须读不到（S1 的回归）。

    原先 `v_active_chunks` 只过滤 `documents.deleted_at`、不 join `knowledge_bases`，
    而 `soft_delete` 只写 KB 那一行（文档、build、chunk 原样留着）——于是删库之后
    文档列表变空、检索却照样命中，同一个库在两处有不同答案。
    """
    ids = await _seed_two_builds(session, scope_a, kb_id)
    repo = SqlChunkRepo(session)

    assert await repo.list_active_chunks(scope_a, ids["doc"])
    assert await repo.get_chunks(scope_a, ids["new_chunks"])
    assert await repo.list_chunks_by_build(scope_a, ids["new_build"])

    await session.execute(
        text("UPDATE knowledge_bases SET deleted_at = now() WHERE kb_id = :kb"), {"kb": kb_id}
    )
    await session.commit()

    # 活动视图两个入口（成员3 检索与 C2 契约都打这里）
    assert await repo.list_active_chunks(scope_a, ids["doc"]) == []
    assert await repo.get_chunks(scope_a, ids["new_chunks"]) == []
    # I1 的受控例外不能变成旁路：历史 build 也要跟着不可见
    assert await repo.list_chunks_by_build(scope_a, ids["new_build"]) == []
    assert await repo.list_chunks_by_build(scope_a, ids["old_build"]) == []


async def test_list_builds_is_newest_first_and_includes_retired(session, scope_a, kb_id) -> None:
    ids = await _seed_two_builds(session, scope_a, kb_id)
    repo = SqlIndexBuildRepo(session)

    builds = await repo.list_builds(scope_a, ids["doc"])
    assert [b.build_id for b in builds] == [ids["new_build"], ids["old_build"]]
    assert [b.state for b in builds] == ["active", "retired"]


async def test_get_version_is_workspace_scoped(session, scope_a, scope_b, kb_id) -> None:
    ids = await _seed_two_builds(session, scope_a, kb_id)
    repo = SqlDocumentRepo(session)

    version = await repo.get_version(scope_a, ids["version"])
    assert version is not None
    assert version.document_id == ids["doc"]
    assert version.object_key == "blobs/v1"
    assert version.content_hash

    assert await repo.get_version(scope_b, ids["version"]) is None
    assert await repo.get_version(scope_a, uuid4()) is None
