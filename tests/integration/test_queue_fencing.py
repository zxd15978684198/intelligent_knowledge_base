"""S08 队列的幂等与 fencing。

这些性质**在 HTTP 层测不到**：上传接口的重放会先命中内容哈希去重，根本走不到
`enqueue`。而"同 key 同参复用同一个作业"是 AGENTS.md 里"不得假定后台任务恰好
执行一次"的直接落点，必须直连 `queue.enqueue` 验。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.services.queue import jobs as queue

KB_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
KB_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


async def _document(session, scope: Scope) -> object:
    """插一条最小文档行，供作业挂靠。作业表有 (workspace_id, document_id) 复合外键。"""
    doc_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO documents (document_id, workspace_id, kb_id, generation, row_version) "
            "VALUES (:d, :w, :kb, 1, 1)"
        ),
        {
            "d": doc_id,
            "w": scope.workspace_id,
            "kb": KB_A if str(scope.workspace_id).startswith("11111111") else KB_B,
        },
    )
    await session.flush()
    return doc_id


async def test_same_key_same_params_returns_same_job(session, scope_a) -> None:
    """同 key、同文档、同 payload → 同一行，不新建。

    这条挡的是客户端重试、网络抖动、双击上传。少了它，一次抖动会变成两次
    解析 + 两次 Embedding，而第二次发布会被 VERSION_SUPERSEDED 拒掉，
    前端却能查到两条都"成功过又互相取代"的作业。
    """
    doc_id = await _document(session, scope_a)
    payload = {"document_version_id": str(uuid4())}

    first = await queue.enqueue(
        session,
        scope=scope_a,
        document_id=doc_id,
        job_type="ingest",
        payload=payload,
        idempotency_key="same-key",
    )
    second = await queue.enqueue(
        session,
        scope=scope_a,
        document_id=doc_id,
        job_type="ingest",
        payload=payload,
        idempotency_key="same-key",
    )

    assert first.job_id == second.job_id
    count = await session.execute(
        text("SELECT count(*) FROM ingestion_jobs WHERE workspace_id = :w"),
        {"w": scope_a.workspace_id},
    )
    assert count.scalar_one() == 1


async def test_same_key_different_payload_conflicts(session, scope_a) -> None:
    """同 key 但 payload 里的版本号变了 → 409，而不是悄悄复用旧作业。

    **这是本轮修掉的一个真 bug**：早先只比 `document_id` 与 `job_type`。而
    "同一份文档换了内容再传"这两项完全一样，只有 `document_version_id` 变了，
    于是复用上一版作业 —— 它发布的是旧内容，随后被 VERSION_SUPERSEDED 拒掉，
    结果是新版本永远没人处理，接口却已经回了 202 表示"已受理"。
    """
    doc_id = await _document(session, scope_a)
    await queue.enqueue(
        session,
        scope=scope_a,
        document_id=doc_id,
        job_type="ingest",
        payload={"document_version_id": str(uuid4())},
        idempotency_key="reused-key",
    )

    with pytest.raises(AppError) as exc:
        await queue.enqueue(
            session,
            scope=scope_a,
            document_id=doc_id,
            job_type="ingest",
            payload={"document_version_id": str(uuid4())},
            idempotency_key="reused-key",
        )
    assert exc.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


async def test_same_key_different_job_type_conflicts(session, scope_a) -> None:
    """同 key 换了作业类型（ingest → purge）必须冲突。"""
    doc_id = await _document(session, scope_a)
    await queue.enqueue(
        session,
        scope=scope_a,
        document_id=doc_id,
        job_type="ingest",
        payload={},
        idempotency_key="type-key",
    )
    with pytest.raises(AppError) as exc:
        await queue.enqueue(
            session,
            scope=scope_a,
            document_id=doc_id,
            job_type="purge",
            payload={},
            idempotency_key="type-key",
        )
    assert exc.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


async def test_key_is_scoped_per_workspace(session, scope_a, scope_b) -> None:
    """同一个 key 在 B 区是另一个命名空间，不该撞上 A 区的作业。"""
    doc_a = await _document(session, scope_a)
    doc_b = await _document(session, scope_b)

    job_a = await queue.enqueue(
        session,
        scope=scope_a,
        document_id=doc_a,
        job_type="ingest",
        payload={},
        idempotency_key="shared-key",
    )
    job_b = await queue.enqueue(
        session,
        scope=scope_b,
        document_id=doc_b,
        job_type="ingest",
        payload={},
        idempotency_key="shared-key",
    )
    assert job_a.job_id != job_b.job_id


async def test_claim_bumps_lease_epoch(session, scope_a) -> None:
    """取件一次 epoch +1、attempt +1，并落下 attempt 行。

    `publish` 靠 epoch + attempt 两道比对拒绝旧 Worker，所以这两个数必须是
    每次取件都推进，不能只在第一次写。
    """
    doc_id = await _document(session, scope_a)
    job = await queue.enqueue(
        session, scope=scope_a, document_id=doc_id, job_type="ingest", payload={}
    )

    claimed = await queue.claim(session, worker_id="w-1")
    assert claimed is not None
    assert claimed.job_id == job.job_id
    assert claimed.lease_epoch == 1
    assert claimed.attempt == 1

    row = await session.execute(
        text("SELECT lease_epoch, attempt, status FROM ingestion_jobs WHERE job_id = :j"),
        {"j": job.job_id},
    )
    lease_epoch, attempt, status = row.one()
    assert (lease_epoch, attempt, status) == (1, 1, "processing")

    attempts = await session.execute(
        text("SELECT count(*) FROM job_attempts WHERE job_id = :j"), {"j": job.job_id}
    )
    assert attempts.scalar_one() == 1


async def test_claim_second_worker_gets_nothing(session, scope_a) -> None:
    """只有一个 pending 作业时，第二个 Worker 取到 None 而不是同一件活。

    `FOR UPDATE SKIP LOCKED` 的语义：跳过别人锁住的行，**不排队**。
    """
    doc_id = await _document(session, scope_a)
    await queue.enqueue(session, scope=scope_a, document_id=doc_id, job_type="ingest", payload={})

    first = await queue.claim(session, worker_id="w-1")
    second = await queue.claim(session, worker_id="w-2")
    assert first is not None
    assert second is None


async def test_expired_lease_can_be_reclaimed(session, scope_a) -> None:
    """租约过期后另一 Worker 可以接管，且 epoch 继续推进。

    没有这条，Worker 崩掉后作业就永远卡在 processing——没有任何机制会重新捡起它。
    """
    doc_id = await _document(session, scope_a)
    await queue.enqueue(session, scope=scope_a, document_id=doc_id, job_type="ingest", payload={})

    first = await queue.claim(session, worker_id="w-1")
    assert first is not None

    # 手工把租约打到过去，模拟 Worker 崩溃。
    await session.execute(
        text(
            "UPDATE ingestion_jobs SET lease_expires_at = now() - interval '1 hour' "
            "WHERE job_id = :j"
        ),
        {"j": first.job_id},
    )
    await session.flush()

    second = await queue.claim(session, worker_id="w-2")
    assert second is not None
    assert second.job_id == first.job_id
    assert second.lease_epoch == first.lease_epoch + 1
    # attempt 累加，超 max_attempts 才会被判 failed。
    assert second.attempt == 2


async def test_renew_lease_fails_for_stale_worker(session, scope_a) -> None:
    """旧 Worker 续不到租约——这是它"发现自己被接管"的唯一途径。"""
    doc_id = await _document(session, scope_a)
    await queue.enqueue(session, scope=scope_a, document_id=doc_id, job_type="ingest", payload={})

    old = await queue.claim(session, worker_id="w-1")
    assert old is not None
    await session.execute(
        text(
            "UPDATE ingestion_jobs SET lease_expires_at = now() - interval '1 hour' "
            "WHERE job_id = :j"
        ),
        {"j": old.job_id},
    )
    await session.flush()
    new = await queue.claim(session, worker_id="w-2")
    assert new is not None

    assert await queue.renew_lease(session, job_id=old.job_id, lease_epoch=old.lease_epoch) is False
    assert await queue.renew_lease(session, job_id=new.job_id, lease_epoch=new.lease_epoch) is True


async def test_cancelled_job_is_terminal_and_not_claimable(session, scope_a) -> None:
    """取消后的行不能被取件捡走，且落在终态集合里。"""
    doc_id = await _document(session, scope_a)
    job = await queue.enqueue(
        session, scope=scope_a, document_id=doc_id, job_type="ingest", payload={}
    )
    await session.execute(
        text("UPDATE ingestion_jobs SET status = 'cancelled' WHERE job_id = :j"),
        {"j": job.job_id},
    )
    await session.flush()

    assert await queue.claim(session, worker_id="w-1") is None

    row = await session.execute(
        text("SELECT status FROM ingestion_jobs WHERE job_id = :j"), {"j": job.job_id}
    )
    assert row.scalar_one() in queue.TERMINAL_STATUSES


async def test_claim_filters_by_job_type(session, scope_a) -> None:
    """`job_types` 必须真的进 WHERE：否则 Worker 会领走自己不认识的作业。

    场景是"以后加了第三种作业类型"：没过滤时任何 Worker 都可能先领到它，
    然后在 `process_job` 里撞 unknown 分支 → 作业直接 failed，而正确的消费者
    永远等不到它——作业不是排队，是丢了。
    """
    doc_id = await _document(session, scope_a)
    purge = await queue.enqueue(
        session,
        scope=scope_a,
        document_id=doc_id,
        job_type="purge",
        payload={"principal_id": str(scope_a.principal_id)},
    )
    await session.flush()

    # 只消费 ingest 的 Worker 看不见 purge 作业
    assert await queue.claim(session, worker_id="w-ingest", job_types=("ingest",)) is None

    # 声明 purge 的才能领到，且拿到的就是那一件
    claimed = await queue.claim(session, worker_id="w-purge", job_types=("purge",))
    assert claimed is not None
    assert claimed.job_id == purge.job_id
    assert claimed.job_type == "purge"
