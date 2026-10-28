"""A1–A9：摄取链路的集成验收。

每个测试真连 PG 容器，session 夹具在开头 TRUNCATE 全部表。
这些断言对应 `docs/plans/成员2执行计划.md` §8.2。
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B
from pyknora.db.repos.reads import SqlChunkRepo
from pyknora.models.tables import DocumentVersionRow
from pyknora.services.ingest import documents as docs
from pyknora.services.queue import jobs as queue
from pyknora.workers import ingest_worker

TXT = ("PyKnora 摄取链路集成测试正文。" * 40).encode()


async def _ingest_one(session, *, scope, kb_repo, embedder, kb_id, data=TXT, name="a.txt"):
    """上传 + 跑一次 worker，返回 (upload_result, process_result)。"""
    up = await docs.upload_document(
        session, scope=scope, kb_repo=kb_repo, kb_id=kb_id, filename=name, data=data
    )
    await session.commit()
    res = await ingest_worker.run_once(
        session, worker_id="w-test", kb_repo=kb_repo, embedder=embedder
    )
    await session.commit()
    return up, res


# ---------------- A1：hash 三分支 ----------------


async def test_a1_same_file_twice_is_deduped(session, scope_a, kb_repo, embedder, kb_id):
    up1 = await docs.upload_document(
        session, scope=scope_a, kb_repo=kb_repo, kb_id=kb_id, filename="a.txt", data=TXT
    )
    await session.commit()
    jobs_before = await docs.count_jobs(session, workspace_id=WORKSPACE_A)

    up2 = await docs.upload_document(
        session, scope=scope_a, kb_repo=kb_repo, kb_id=kb_id, filename="a.txt", data=TXT
    )
    await session.commit()
    jobs_after = await docs.count_jobs(session, workspace_id=WORKSPACE_A)

    assert up1.deduped is False
    assert up2.deduped is True
    assert up2.job_id is None
    assert jobs_after == jobs_before, "无变更分支不得入队"


async def test_a1_modified_file_bumps_version(session, scope_a, kb_repo, embedder, kb_id):
    up1 = await docs.upload_document(
        session, scope=scope_a, kb_repo=kb_repo, kb_id=kb_id, filename="a.txt", data=TXT
    )
    await session.commit()
    up2 = await docs.upload_document(
        session,
        scope=scope_a,
        kb_repo=kb_repo,
        kb_id=kb_id,
        filename="a.txt",
        data=TXT + b"changed",
    )
    await session.commit()

    assert up2.deduped is False
    assert up2.document_id == up1.document_id
    assert up2.document_version_id != up1.document_version_id


async def test_a1_aba_returns_to_first_version(session, scope_a, kb_repo, kb_id):
    """A→B→A：第三次上传必须被判"有变更"（指针指回），不能因 documents 上有 hash 而误判。"""
    up_a = await docs.upload_document(
        session, scope=scope_a, kb_repo=kb_repo, kb_id=kb_id, filename="a.txt", data=TXT
    )
    await session.commit()
    await docs.upload_document(
        session,
        scope=scope_a,
        kb_repo=kb_repo,
        kb_id=kb_id,
        filename="a.txt",
        data=TXT + b"B",
    )
    await session.commit()
    up_a2 = await docs.upload_document(
        session, scope=scope_a, kb_repo=kb_repo, kb_id=kb_id, filename="a.txt", data=TXT
    )
    await session.commit()

    assert up_a2.deduped is True
    assert up_a2.document_version_id == up_a.document_version_id


# ---------------- A2：并发 claim 不重复 ----------------


async def test_a2_two_workers_no_double_claim(session, scope_a, kb_repo, kb_id):
    """两个 Worker 抢同一批作业，不能拿到同一件。

    写法上绕开了两个坑：

    1. **不能对同一个 session gather**。AsyncSession 不允许并发操作，会直接抛
       InvalidRequestError——那测的是 SQLAlchemy 而不是我们的 SQL。
    2. **不能"先 commit 再 claim 下一个"**。commit 会释放行锁，第二个 Worker
       自然拿不到同一行，测不出 `SKIP LOCKED` 是否真的生效。

    所以：两个独立 session，第一个 claim 后**故意不提交**（锁仍持有），
    第二个 claim 必须跳过被锁的那行。如果 `SKIP LOCKED` 被删掉，第二个
    claim 会阻塞等锁，用 `wait_for` 把它变成一条明确的失败信息而不是挂死。
    """
    from pyknora.db.session import get_session_factory

    for i in range(4):
        await docs.upload_document(
            session,
            scope=scope_a,
            kb_repo=kb_repo,
            kb_id=kb_id,
            filename=f"doc{i}.txt",
            data=TXT + str(i).encode(),
        )
    await session.commit()

    factory = get_session_factory()
    s1 = factory()
    s2 = factory()
    try:
        first = await queue.claim(s1, worker_id="w1")
        assert first is not None

        # 第一个事务未提交，行锁仍在；第二个 Worker 必须跳过它
        second = await asyncio.wait_for(queue.claim(s2, worker_id="w2"), timeout=5)
        assert second is not None
        assert first.job_id != second.job_id, "两个 Worker 不能 claim 到同一件"
        assert second.lease_epoch == 1, "各自首轮 claim 的 epoch 都是 1"
    finally:
        await s1.rollback()
        await s1.close()
        await s2.rollback()
        await s2.close()


# ---------------- A5 / A6：staging 不可见、旧 attempt 被拒 ----------------


async def test_a5_staging_chunks_invisible(session, scope_a, kb_repo, embedder, kb_id):
    up = await docs.upload_document(
        session, scope=scope_a, kb_repo=kb_repo, kb_id=kb_id, filename="a.txt", data=TXT
    )
    await session.commit()

    job = await queue.claim(session, worker_id="w1")
    assert job is not None
    # 只跑解析、建 staging build、写 chunk，不发布
    from pyknora.services.ingest import parser, publish, splitter

    parsed = parser.parse(TXT, "a.txt", None)
    chunks = splitter.chunk_parsed(parsed)
    build = await publish.create_staging_build(
        session,
        workspace_id=WORKSPACE_A,
        document_id=up.document_id,
        attempt_id=job.attempt_id,
        index_version=1,
        embedding_model_key=embedder.model_key,
        embedding_dim=embedder.dim,
        chunk_size=512,
        chunk_overlap=64,
    )
    from pyknora.models.tables import ChunkRow

    for c in chunks:
        session.add(
            ChunkRow(
                chunk_id=uuid4(),
                workspace_id=WORKSPACE_A,
                document_id=up.document_id,
                build_id=build.build_id,
                index_version=1,
                attempt_id=job.attempt_id,
                chunk_ordinal=c.ordinal,
                content=c.content,
                content_hash="x",
                page_no=c.page_no,
                heading_path=c.heading_path,
                char_start=c.char_start,
                char_end=c.char_end,
                token_count=c.token_count,
                locator_ref=c.locator_ref,
                embedding=[0.1] * embedder.dim,
            )
        )
    await session.commit()

    repo = SqlChunkRepo(session)
    visible = await repo.list_active_chunks(scope_a, up.document_id)
    assert visible == [], "staging build 的 chunk 不得出现在活动视图里"


async def test_a6_stale_attempt_rejected(session, scope_a, kb_repo, embedder, kb_id):
    """旧 Worker 晚到：lease 被新一代接管后再发布，必须抛 STALE_ATTEMPT。

    场景要真实：不是"把 epoch 手动改成 0"，而是让一个 Worker 跑完，
    作业回到 pending 后被**另一个 Worker 重新 claim**（epoch 自然 +1），
    此时第一个 Worker 手里那份旧 job 对象再来发布，就该被拒。
    """
    up = await docs.upload_document(
        session, scope=scope_a, kb_repo=kb_repo, kb_id=kb_id, filename="a.txt", data=TXT
    )
    await session.commit()

    # 第一个 Worker：正常 claim 并跑完
    old_job = await queue.claim(session, worker_id="w-old", lease_seconds=90)
    assert old_job is not None
    await session.commit()
    old_epoch = old_job.lease_epoch

    res = await ingest_worker.process_job(session, job=old_job, kb_repo=kb_repo, embedder=embedder)
    await session.commit()
    assert res.status == "succeeded"

    active_before = (
        await session.execute(text("SELECT active_index_version FROM documents"))
    ).scalar_one()

    # 把作业退回 pending，让另一个 Worker 接管（epoch 递增）
    await session.execute(text("UPDATE ingestion_jobs SET status = 'pending'"))
    await session.commit()
    new_job = await queue.claim(session, worker_id="w-new", lease_seconds=90)
    await session.commit()
    assert new_job is not None
    assert new_job.lease_epoch > old_epoch, "新一代 Worker 的 epoch 必须更大"

    # 旧 Worker 手里那份 job（旧 epoch）现在来发布 → 必被拒
    with pytest.raises(AppError) as exc:
        await publish_call(session, old_job, res.build_id, up.document_version_id)
    assert exc.value.code == ErrorCode.STALE_ATTEMPT

    await session.rollback()
    active_after = (
        await session.execute(text("SELECT active_index_version FROM documents"))
    ).scalar_one()
    assert active_after == active_before, "活动版本不得被回退"


async def publish_call(session, job, build_id, version_id, *, chunk_count: int = 1):
    from pyknora.services.ingest import publish

    return await publish.publish(
        session,
        job=job,
        manifest=publish.PublishManifest(
            build_id=build_id,
            document_version_id=version_id,
            chunk_count=chunk_count,
            embedding_model_key="k",
            embedding_dim=512,
        ),
    )


# ---------------- A7：跨区隔离 ----------------


async def test_a7_cross_workspace_isolated(session, scope_a, scope_b, kb_repo, embedder, kb_id):
    """两区正文完全相同，各自都能搜到自己的，且 A 区结果里没有 B 区的行。"""
    from pyknora.schemas.kb import EmbeddingProfile

    profile = EmbeddingProfile(
        embedding_model_key="test/hashing",
        embedding_dim=512,
        chunk_size=512,
        chunk_overlap=64,
        normalize=True,
    )
    repo_a = kb_repo
    repo_b = type(kb_repo)()
    kb_id_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    repo_b.add(scope_b, kb_id_b, profile)

    up_a = await docs.upload_document(
        session, scope=scope_a, kb_repo=repo_a, kb_id=kb_id, filename="a.txt", data=TXT
    )
    await session.commit()
    await ingest_worker.run_once(session, worker_id="w1", kb_repo=repo_a, embedder=embedder)
    await session.commit()

    up_b = await docs.upload_document(
        session, scope=scope_b, kb_repo=repo_b, kb_id=kb_id_b, filename="a.txt", data=TXT
    )
    await session.commit()
    await ingest_worker.run_once(session, worker_id="w2", kb_repo=repo_b, embedder=embedder)
    await session.commit()

    chunk_repo = SqlChunkRepo(session)
    a_chunks = await chunk_repo.list_active_chunks(scope_a, up_a.document_id)
    b_chunks = await chunk_repo.list_active_chunks(scope_b, up_b.document_id)

    assert len(a_chunks) > 0 and len(b_chunks) > 0, "两区都应各自命中"
    assert all(c.workspace_id == WORKSPACE_A for c in a_chunks)
    assert all(c.workspace_id == WORKSPACE_B for c in b_chunks)

    # 用 B 的 scope 去查 A 的文档 → 一条都读不到
    leak = await chunk_repo.list_active_chunks(scope_b, up_a.document_id)
    assert leak == [], "跨区不得读到任何 chunk"


# ---------------- A8：删除立刻不可见 ----------------


async def test_a8_delete_hides_immediately(session, scope_a, kb_repo, embedder, kb_id):
    up, res = await _ingest_one(
        session, scope=scope_a, kb_repo=kb_repo, embedder=embedder, kb_id=kb_id
    )
    assert res is not None and res.status == "succeeded"

    repo = SqlChunkRepo(session)
    assert len(await repo.list_active_chunks(scope_a, up.document_id)) > 0

    purge_job = await docs.delete_document(session, scope=scope_a, document_id=up.document_id)
    await session.commit()
    assert purge_job is not None

    # 不等 purge 跑，立刻不可见
    assert await repo.list_active_chunks(scope_a, up.document_id) == []

    # 跑 purge 之后物理行也没了
    await ingest_worker.run_once(session, worker_id="w1", kb_repo=kb_repo, embedder=embedder)
    await session.commit()
    remaining = await docs.count_chunks(
        session, workspace_id=WORKSPACE_A, document_id=up.document_id
    )
    assert remaining == 0


# ---------------- A9：向量缓存 ----------------


async def test_a9_embedding_cache_hits(session, embedder):
    from pyknora.services.ingest.embedding_service import EmbeddingService

    calls = {"n": 0}

    class Spy:
        model_key = embedder.model_key
        dim = embedder.dim

        async def embed(self, texts):
            calls["n"] += 1
            return await embedder.embed(texts)

    texts = ["缓存测试正文一", "缓存测试正文二"]
    spy = Spy()

    svc = EmbeddingService(spy, session)
    v1 = await svc.embed_cached(texts)
    await session.commit()
    assert calls["n"] == 1

    svc2 = EmbeddingService(spy, session)
    v2 = await svc2.embed_cached(texts)
    await session.commit()
    assert calls["n"] == 1, "同文本同模型不得再调后端"
    # 不能写 v1 == v2：向量走过一遍 pgvector 的 float4 往返，末位会变。
    # 要断言的是"数值等价"，不是"位相等"。
    for row_a, row_b in zip(v1, v2, strict=True):
        assert row_a == pytest.approx(row_b, rel=1e-6, abs=1e-6)

    # 换 model_key → 必须重新调
    class SpyOther(Spy):
        model_key = "other/model"

    svc3 = EmbeddingService(SpyOther(), session)
    await svc3.embed_cached(texts)
    await session.commit()
    assert calls["n"] == 2


# ---------------- 版本指针与视图一致性 ----------------


async def test_version_pointer_and_view_agree(session, scope_a, kb_repo, embedder, kb_id):
    up, res = await _ingest_one(
        session, scope=scope_a, kb_repo=kb_repo, embedder=embedder, kb_id=kb_id
    )
    assert res.status == "succeeded"

    doc = (
        await session.execute(
            text(
                "SELECT active_document_version_id, active_build_id, active_index_version "
                "FROM documents WHERE document_id = :d"
            ),
            {"d": up.document_id},
        )
    ).first()
    assert doc[0] == up.document_version_id
    assert doc[1] == res.build_id
    assert doc[2] == 1

    chunks = (
        (
            await session.execute(
                text("SELECT DISTINCT build_id FROM v_active_chunks WHERE document_id = :d"),
                {"d": up.document_id},
            )
        )
        .scalars()
        .all()
    )
    assert chunks == [res.build_id]

    status = (
        await session.execute(
            select(DocumentVersionRow.parse_status).where(
                DocumentVersionRow.document_version_id == up.document_version_id
            )
        )
    ).scalar_one()
    assert status == "succeeded"
