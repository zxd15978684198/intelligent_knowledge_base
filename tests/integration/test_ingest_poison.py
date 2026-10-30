"""坏文件不能打死 Worker（P0 回归）。

## 事故形状

Worker 的取件循环是「领一件、跑一件、提交」。作业体抛出的**非 `AppError`** 异常
（真实触发物：被截断的 PDF → `pypdf.errors.PdfStreamError: Stream has ended
unexpectedly`）会一路穿过 `run_once` 到 `scripts/run_worker.py` 的
`session_scope()`，于是整个事务回滚：

- 失败记录（`job_attempts` 那行、`ingestion_jobs.status`）一起被回滚掉；
- 作业停在 `pending`、`attempt` 停在 0；
- 队列按 `created_at` 认领，它就一直坐在队头，**后面所有上传都进不来**。

实测现象：同一作业每 5s 被重新领一次，状态一个字都不变，队列 3 分钟零吞吐。

## 这里钉住四件事

1. 进程不崩：`run_once` 正常返回 `status="failed"`；
2. 失败真的落库：`status='failed'`、`error_code='PARSE_FAILED'`；
3. 尝试记录也在：`job_attempts` 有一行 `outcome='failed'`（证明没被回滚）；
4. 队列能继续走：随后上传的正常文件在同一条 session 上能成功处理。
"""

from __future__ import annotations

from sqlalchemy import text

from pyknora.core.errors import ErrorCode
from pyknora.services.ingest import documents as docs
from pyknora.workers import ingest_worker

# 一段被截断的 PDF：有魔数、有对象头，但没有 xref/trailer。
# `PdfReader` 在这里抛 `PdfStreamError`，属于"不是 AppError"的意外异常。
CORRUPT_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n"

TXT = ("PyKnora 坏文件回归正文。" * 40).encode()


async def test_corrupt_pdf_fails_job_without_killing_the_worker(
    session, scope_a, kb_repo, embedder, kb_id
) -> None:
    up = await docs.upload_document(
        session,
        scope=scope_a,
        kb_repo=kb_repo,
        kb_id=kb_id,
        filename="broken.pdf",
        data=CORRUPT_PDF,
    )
    await session.commit()

    result = await ingest_worker.run_once(
        session, worker_id="w-poison", kb_repo=kb_repo, embedder=embedder
    )
    await session.commit()

    assert result is not None, "作业没被认领——问题就不在这条链路上"
    assert result.status == "failed"
    assert result.error_code == ErrorCode.PARSE_FAILED

    job = (
        await session.execute(
            text("SELECT status, error_code, attempt FROM ingestion_jobs WHERE job_id = :job_id"),
            {"job_id": up.job_id},
        )
    ).one()
    assert job.status == "failed", "坏文件必须是终态 failed，不能留在 pending 里被无限重领"
    assert job.error_code == ErrorCode.PARSE_FAILED
    assert job.attempt == 1

    attempts = (
        (
            await session.execute(
                text("SELECT outcome FROM job_attempts WHERE job_id = :job_id"),
                {"job_id": up.job_id},
            )
        )
        .scalars()
        .all()
    )
    assert attempts == ["failed"], "失败记录被回滚了——这正是事故的根因"


async def test_queue_keeps_moving_after_a_poison_job(
    session, scope_a, kb_repo, embedder, kb_id
) -> None:
    """坏作业失败后，后面的正常作业必须能被处理（队头没被堵死）。"""
    await docs.upload_document(
        session,
        scope=scope_a,
        kb_repo=kb_repo,
        kb_id=kb_id,
        filename="broken.pdf",
        data=CORRUPT_PDF,
    )
    await session.commit()
    await ingest_worker.run_once(session, worker_id="w-poison", kb_repo=kb_repo, embedder=embedder)
    await session.commit()

    good = await docs.upload_document(
        session, scope=scope_a, kb_repo=kb_repo, kb_id=kb_id, filename="good.txt", data=TXT
    )
    await session.commit()

    result = await ingest_worker.run_once(
        session, worker_id="w-poison", kb_repo=kb_repo, embedder=embedder
    )
    await session.commit()

    assert result is not None
    assert result.job_id == good.job_id
    assert result.status == "succeeded"
    assert result.chunk_count >= 1
