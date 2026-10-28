"""S08：PostgreSQL 队列。

设计选择（D2）：不引入 Redis/Celery，用同库表 + `FOR UPDATE SKIP LOCKED`。
理由：摄取作业的写入目标也是 PG，同库能保证"作业状态与索引发布在同一事务域"，
省掉一个组件，也省掉"DB 提交成功但 Redis 唤醒丢了"这类问题。

三重 fencing：
- `lease_epoch`：每次取件 +1，旧 Worker 拿着旧 epoch 回来发布会被拒
- `lease_expires_at`：租约到期后作业可被重新取走
- `attempt`：累计取件次数，超过 max_attempts 判 failed
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.models.tables import IngestionJobRow, JobAttemptRow

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED})


@dataclass(slots=True)
class ClaimedJob:
    """Worker 手里的一份作业凭据。发布事务要拿这三样去校验。"""

    job_id: UUID
    workspace_id: UUID
    document_id: UUID
    job_type: str
    attempt: int
    lease_epoch: int
    attempt_id: UUID
    payload: dict[str, Any]


async def enqueue(
    session: AsyncSession,
    *,
    scope: Scope,
    document_id: UUID,
    job_type: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> IngestionJobRow:
    """入队。同一个 idempotency_key 重复调用返回既有行，不新建。

    这是"不得假定后台任务恰好执行一次"的入口防线：客户端重试、网络抖动、
    双击上传，都应该落到同一行作业上。
    """
    key = idempotency_key or f"{job_type}:{document_id}:{uuid4()}"
    existing = (
        await session.execute(
            select(IngestionJobRow).where(
                IngestionJobRow.workspace_id == scope.workspace_id,
                IngestionJobRow.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # 同 key 不同参数是调用方的错，不是幂等。
        #
        # payload 也要比：同一份文档"换内容后重传"时 `document_id` 与 `job_type`
        # 都没变，只有 payload 里的 `document_version_id` 变了。只比前两项会返回
        # 上一版那个作业——它跑完发布的是旧内容，随后被 VERSION_SUPERSEDED 拒掉，
        # 结果是新版本永远没人处理，而接口却回了 202 表示"已受理"。
        same_params = (
            existing.document_id == document_id
            and existing.job_type == job_type
            and (existing.payload or {}) == (payload or {})
        )
        if not same_params:
            raise AppError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "idempotency key reused with different parameters",
                details={"job_id": str(existing.job_id)},
            )
        return existing

    row = IngestionJobRow(
        job_id=uuid4(),
        workspace_id=scope.workspace_id,
        document_id=document_id,
        job_type=job_type,
        status=STATUS_PENDING,
        idempotency_key=key,
        payload=payload or {},
        attempt=0,
        lease_epoch=0,
    )
    session.add(row)
    await session.flush()
    return row


async def claim(
    session: AsyncSession, *, worker_id: str, lease_seconds: int = 90
) -> ClaimedJob | None:
    """取一件作业。单条 SQL 完成"选中 + 加锁 + 标记"，不存在两步式的竞态窗口。

    `SKIP LOCKED` 让并发 Worker 直接跳过别人已锁住的行，而不是排队等锁；
    这是"两个 Worker 不会拿到同一件活"的全部机制。
    """
    now = datetime.now(UTC)
    stmt = text(
        """
        UPDATE ingestion_jobs j
        SET status = 'processing',
            lease_epoch = j.lease_epoch + 1,
            lease_expires_at = :expires_at,
            attempt = j.attempt + 1,
            worker_id = :worker_id,
            updated_at = :now
        WHERE j.job_id = (
            SELECT c.job_id FROM ingestion_jobs c
            WHERE c.status = 'pending'
               OR (c.status = 'processing' AND c.lease_expires_at < :now)
            ORDER BY c.created_at
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING j.job_id, j.workspace_id, j.document_id, j.job_type,
                  j.attempt, j.lease_epoch, j.payload
        """
    )
    row = (
        (
            await session.execute(
                stmt,
                {
                    "expires_at": now + timedelta(seconds=lease_seconds),
                    "now": now,
                    "worker_id": worker_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None

    attempt_id = uuid4()
    session.add(
        JobAttemptRow(
            attempt_id=attempt_id,
            workspace_id=row["workspace_id"],
            job_id=row["job_id"],
            lease_epoch=row["lease_epoch"],
            worker_id=worker_id,
        )
    )
    await session.flush()
    return ClaimedJob(
        job_id=row["job_id"],
        workspace_id=row["workspace_id"],
        document_id=row["document_id"],
        job_type=row["job_type"],
        attempt=row["attempt"],
        lease_epoch=row["lease_epoch"],
        attempt_id=attempt_id,
        payload=row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"]),
    )


async def finish_attempt(session: AsyncSession, *, attempt_id: UUID, outcome: str) -> None:
    await session.execute(
        text(
            """
            UPDATE job_attempts SET finished_at = :now, outcome = :outcome
            WHERE attempt_id = :attempt_id AND finished_at IS NULL
            """
        ),
        {"now": datetime.now(UTC), "outcome": outcome, "attempt_id": attempt_id},
    )


async def renew_lease(
    session: AsyncSession, *, job_id: UUID, lease_epoch: int, lease_seconds: int = 90
) -> bool:
    """续租。只有持有当前 epoch 的 Worker 能续；返回 False 说明已被别人接管。"""
    now = datetime.now(UTC)
    result = await session.execute(
        text(
            """
            UPDATE ingestion_jobs
            SET lease_expires_at = :expires_at, updated_at = :now
            WHERE job_id = :job_id AND lease_epoch = :lease_epoch AND status = 'processing'
            """
        ),
        {
            "expires_at": now + timedelta(seconds=lease_seconds),
            "now": now,
            "job_id": job_id,
            "lease_epoch": lease_epoch,
        },
    )
    return result.rowcount > 0


async def mark_failed(
    session: AsyncSession, *, job: ClaimedJob, error_code: str, max_attempts: int = 3
) -> None:
    """失败处理。未超上限则退回 pending 等重试，超了才置 failed。"""
    new_status = STATUS_FAILED if job.attempt >= max_attempts else STATUS_PENDING
    await session.execute(
        text(
            """
            UPDATE ingestion_jobs
            SET status = :status, error_code = :error_code, updated_at = :now
            WHERE job_id = :job_id AND lease_epoch = :lease_epoch
            """
        ),
        {
            "status": new_status,
            "error_code": error_code,
            "now": datetime.now(UTC),
            "job_id": job.job_id,
            "lease_epoch": job.lease_epoch,
        },
    )


async def get_job(session: AsyncSession, *, scope: Scope, job_id: UUID) -> IngestionJobRow | None:
    stmt = select(IngestionJobRow).where(
        IngestionJobRow.workspace_id == scope.workspace_id,
        IngestionJobRow.job_id == job_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def count_by_status(session: AsyncSession, *, workspace_id: UUID) -> dict[str, int]:
    rows = await session.execute(
        text(
            "SELECT status, count(*) AS n FROM ingestion_jobs "
            "WHERE workspace_id = :workspace_id GROUP BY status"
        ),
        {"workspace_id": workspace_id},
    )
    return {r.status: r.n for r in rows}


async def pending_jobs(
    session: AsyncSession, *, workspace_id: UUID, job_type: str | None = None
) -> list[IngestionJobRow]:
    stmt = select(IngestionJobRow).where(
        IngestionJobRow.workspace_id == workspace_id,
        IngestionJobRow.status == STATUS_PENDING,
    )
    if job_type:
        stmt = stmt.where(IngestionJobRow.job_type == job_type)
    return list((await session.execute(stmt.order_by(IngestionJobRow.created_at))).scalars())


async def has_live_processing(session: AsyncSession, *, job_id: UUID) -> bool:
    """是否还有未过期的租约——用于判断某文档是否真的没有 Worker 在写。"""
    row = await session.execute(
        text(
            "SELECT 1 FROM ingestion_jobs WHERE job_id = :job_id "
            "AND status = 'processing' AND lease_expires_at > now()"
        ),
        {"job_id": job_id},
    )
    return row.first() is not None
