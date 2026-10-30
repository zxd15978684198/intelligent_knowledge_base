"""删除链路的两个缺口：purge 只删 chunk、删库不管文档。

两个缺口都是"可见性已经切断、所以没人发现"的类型：
- `purge_document` 先前只 `DELETE FROM chunks`，`document_blobs` / `document_versions`
  永久留下（原始字节与快照，磁盘只增不减），`documents` 的版本指针指向已删行；
- `delete_knowledge_base` 只软删库本身，`documents` 是 `ON DELETE RESTRICT` 且同为软删，
  于是库消失后它的 chunk 仍在 `v_active_chunks` 里，检索照旧命中没有归属的内容。

这两个用例用真库钉住"删干净"，不是断言 SQL 文本。
"""

from __future__ import annotations

from sqlalchemy import text

from pyknora.core.scope import WORKSPACE_A
from pyknora.db.repos.knowledge_bases import SqlKnowledgeBaseRepo
from pyknora.services.ingest import documents as docs
from pyknora.services.kb.service import delete_knowledge_base
from pyknora.workers import ingest_worker

TXT = ("删除链路集成测试正文。" * 40).encode()


async def _ingest_one(session, *, scope, kb_repo, embedder, kb_id):
    up = await docs.upload_document(
        session, scope=scope, kb_repo=kb_repo, kb_id=kb_id, filename="d.txt", data=TXT
    )
    await session.commit()
    res = await ingest_worker.run_once(
        session, worker_id="w-test", kb_repo=kb_repo, embedder=embedder
    )
    await session.commit()
    assert res is not None and res.status == "succeeded"
    return up


async def _count(session, table: str, *, document_id) -> int:
    return (
        await session.execute(
            text(f"SELECT count(*) FROM {table} WHERE document_id = :d"), {"d": document_id}
        )
    ).scalar_one()


async def test_purge_removes_chunks_versions_and_blobs(session, scope_a, kb_repo, embedder, kb_id):
    up = await _ingest_one(session, scope=scope_a, kb_repo=kb_repo, embedder=embedder, kb_id=kb_id)
    blob_sql = text(
        "SELECT count(*) FROM document_blobs WHERE workspace_id = :w AND document_version_id IN "
        "(SELECT document_version_id FROM document_versions WHERE document_id = :d)"
    )
    assert (
        await session.execute(blob_sql, {"w": WORKSPACE_A, "d": up.document_id})
    ).scalar_one() == 1

    await docs.delete_document(session, scope=scope_a, document_id=up.document_id)
    await session.commit()

    # 可见性立刻断开，物理行等 purge
    assert await _count(session, "chunks", document_id=up.document_id) > 0

    await ingest_worker.run_once(session, worker_id="w-test", kb_repo=kb_repo, embedder=embedder)
    await session.commit()

    assert await _count(session, "chunks", document_id=up.document_id) == 0
    assert await _count(session, "document_versions", document_id=up.document_id) == 0
    assert (
        await session.execute(blob_sql, {"w": WORKSPACE_A, "d": up.document_id})
    ).scalar_one() == 0, "blob 字节必须一起删，否则每删一篇文档就漏一份原文"

    # 墓碑保留，但不再假装指向某份内容
    row = (
        await session.execute(
            text(
                "SELECT deleted_at, active_document_version_id, desired_document_version_id "
                "FROM documents WHERE document_id = :d"
            ),
            {"d": up.document_id},
        )
    ).one()
    assert row.deleted_at is not None
    assert row.active_document_version_id is None
    assert row.desired_document_version_id is None


async def test_purge_is_idempotent(session, scope_a, kb_repo, embedder, kb_id):
    """重复 purge 不能报错（重复入队是允许的，见 kb/service.py 的说明）。"""
    up = await _ingest_one(session, scope=scope_a, kb_repo=kb_repo, embedder=embedder, kb_id=kb_id)
    await docs.delete_document(session, scope=scope_a, document_id=up.document_id)
    await session.commit()
    await ingest_worker.run_once(session, worker_id="w-test", kb_repo=kb_repo, embedder=embedder)
    await session.commit()

    report = await docs.purge_document(session, scope=scope_a, document_id=up.document_id)
    await session.commit()
    assert report.total == 0


async def test_kb_delete_soft_deletes_documents_and_enqueues_purge(
    session, scope_a, kb_repo, embedder, kb_id
):
    up = await _ingest_one(session, scope=scope_a, kb_repo=kb_repo, embedder=embedder, kb_id=kb_id)
    repo = SqlKnowledgeBaseRepo(session)

    await delete_knowledge_base(session, repo, scope_a, kb_id, expected_version=1)
    await session.commit()

    # 文档被一起软删：库里再也查不到活文档
    assert (
        await session.execute(
            text("SELECT count(*) FROM documents WHERE workspace_id = :w AND deleted_at IS NULL"),
            {"w": WORKSPACE_A},
        )
    ).scalar_one() == 0, "删库必须连带切断文档可见性，否则 chunk 仍可被检索命中"

    purge_jobs = (
        await session.execute(
            text(
                "SELECT count(*) FROM ingestion_jobs "
                "WHERE workspace_id = :w AND job_type = 'purge' AND document_id = :d"
            ),
            {"w": WORKSPACE_A, "d": up.document_id},
        )
    ).scalar_one()
    assert purge_jobs == 1

    # 视图必须立刻看不见这篇文档的 chunk（不等 purge 跑完）
    visible = (
        await session.execute(
            text("SELECT count(*) FROM v_active_chunks WHERE document_id = :d"),
            {"d": up.document_id},
        )
    ).scalar_one()
    assert visible == 0

    await ingest_worker.run_once(session, worker_id="w-test", kb_repo=kb_repo, embedder=embedder)
    await session.commit()
    assert await _count(session, "chunks", document_id=up.document_id) == 0


async def test_kb_delete_blocks_inflight_ingest_job(session, scope_a, kb_repo, embedder, kb_id):
    """删库时还在排队的 ingest 作业不得把内容发布回来。

    上传后不跑 worker，直接删库：文档被软删，随后 ingest 作业被领到，
    `publish` 的 `deleted_at` 守卫必须把它判成 VERSION_SUPERSEDED，
    否则"库已删"与"新 chunk 上线"会同时成立。
    """
    up = await docs.upload_document(
        session, scope=scope_a, kb_repo=kb_repo, kb_id=kb_id, filename="d.txt", data=TXT
    )
    await session.commit()

    repo = SqlKnowledgeBaseRepo(session)
    await delete_knowledge_base(session, repo, scope_a, kb_id, expected_version=1)
    await session.commit()

    # 第一件是上传时入队的 ingest，第二件是删库入队的 purge
    first = await ingest_worker.run_once(
        session, worker_id="w-test", kb_repo=kb_repo, embedder=embedder
    )
    await session.commit()
    assert first is not None
    assert first.status == "failed"
    assert first.error_code == "VERSION_SUPERSEDED"

    await ingest_worker.run_once(session, worker_id="w-test", kb_repo=kb_repo, embedder=embedder)
    await session.commit()
    assert await _count(session, "chunks", document_id=up.document_id) == 0
