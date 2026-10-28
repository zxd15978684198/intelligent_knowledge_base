"""S11：索引发布事务。整个模块唯一没有开源对标的部分。

**为什么需要一个事务**：一次发布要同时改动五张表（documents 指针、
index_builds 状态、ingestion_jobs 状态、job_attempts 结果、outbox 事件）。
任何一步单独提交，都会留下一个"检索能看见但状态说没完成"或
"状态说完成但检索看不见"的窗口。

**发布事务的完整足迹**（六步，按序不可换）：
1. 锁 documents 行                      ← 拿写锁，序列化同一文档的并发发布
2. 校验 attempt / lease_epoch / version ← 不合格直接抛 STALE_ATTEMPT
3. 分配 index_version（在锁内）          ← 保证同一文档版本号单调不减
4. 旧 active build → retired             ← 必须先退休再上新，否则视图会短暂看到两个
5. 新 build → active + 切 documents 指针 ← 视图可见性在此刻翻转
6. job → succeeded + 写 outbox           ← 与指针同一事务，不存在"发布成功但通知丢了"

**最容易写错的地方**：把第 3 步放到锁外。两个 Worker 并发发布会拿到同一个
号，然后其中一个覆盖另一个——这正是我们要防的场景。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.errors import AppError, ErrorCode
from pyknora.models.tables import (
    ChunkRow,
    DocumentRow,
    DocumentVersionRow,
    IndexBuildRow,
    IngestionJobRow,
)
from pyknora.services.queue import jobs as queue
from pyknora.services.queue.jobs import ClaimedJob

logger = logging.getLogger(__name__)

BUILD_STAGING = "staging"
BUILD_ACTIVE = "active"
BUILD_RETIRED = "retired"


@dataclass(slots=True)
class PublishManifest:
    """Worker 交给发布事务的产出清单。里面的每个 id 都会被逐项校验。"""

    build_id: UUID
    document_version_id: UUID
    chunk_count: int
    embedding_model_key: str
    embedding_dim: int


async def allocate_index_version(session: AsyncSession, document: DocumentRow) -> int:
    """在 documents 行锁内分配下一个 index_version。

    调用前提：`document` 已被 `SELECT ... FOR UPDATE` 锁住。
    """
    current = document.active_index_version or 0
    return current + 1


async def create_staging_build(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    document_id: UUID,
    attempt_id: UUID,
    index_version: int,
    embedding_model_key: str,
    embedding_dim: int,
    chunk_size: int,
    chunk_overlap: int,
    normalize: bool = True,
) -> IndexBuildRow:
    """建一个 staging build。staging 数据在视图里不可见——这是隔离的物理保证。"""
    build = IndexBuildRow(
        build_id=uuid4(),
        workspace_id=workspace_id,
        document_id=document_id,
        index_version=index_version,
        attempt_id=attempt_id,
        embedding_model_key=embedding_model_key,
        embedding_dim=embedding_dim,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        normalize=normalize,
        chunk_count=0,
        state=BUILD_STAGING,
    )
    session.add(build)
    await session.flush()
    return build


async def publish(
    session: AsyncSession,
    *,
    job: ClaimedJob,
    manifest: PublishManifest,
) -> IndexBuildRow:
    """原子发布。要么全部生效，要么全部不生效。

    抛错时不自行 rollback——由上层 `session_scope` 统一回滚，
    这样"发布失败"和"别的东西也失败了"共享同一条回滚路径。
    """
    # ---- 第 1 步：锁 documents 行 ----
    document = (
        await session.execute(
            select(DocumentRow)
            .where(
                DocumentRow.workspace_id == job.workspace_id,
                DocumentRow.document_id == job.document_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if document is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "document not found for publish")
    if document.deleted_at is not None:
        raise AppError(ErrorCode.VERSION_SUPERSEDED, "document deleted during ingestion")

    # ---- 第 2 步：三重校验 ----
    live_job = (
        await session.execute(
            select(IngestionJobRow).where(
                IngestionJobRow.workspace_id == job.workspace_id,
                IngestionJobRow.job_id == job.job_id,
            )
        )
    ).scalar_one_or_none()
    if live_job is None:
        raise AppError(ErrorCode.STALE_ATTEMPT, "job vanished before publish")
    if live_job.lease_epoch != job.lease_epoch:
        raise AppError(
            ErrorCode.STALE_ATTEMPT,
            "lease epoch moved on; another worker owns this job now",
            details={"publish_epoch": job.lease_epoch, "live_epoch": live_job.lease_epoch},
        )
    if document.desired_document_version_id not in (None, manifest.document_version_id):
        # 有新版本在等，本次发布已过期
        raise AppError(
            ErrorCode.VERSION_SUPERSEDED,
            "a newer document version is pending publication",
            details={
                "publish_version": str(manifest.document_version_id),
                "desired_version": str(document.desired_document_version_id),
            },
        )

    build = (
        await session.execute(
            select(IndexBuildRow).where(
                IndexBuildRow.workspace_id == job.workspace_id,
                IndexBuildRow.build_id == manifest.build_id,
            )
        )
    ).scalar_one_or_none()
    if build is None:
        raise AppError(ErrorCode.STALE_ATTEMPT, "build not found")
    if build.attempt_id != job.attempt_id:
        raise AppError(
            ErrorCode.STALE_ATTEMPT,
            "build belongs to a different attempt",
            details={"build_attempt": str(build.attempt_id), "job_attempt": str(job.attempt_id)},
        )
    if build.state == BUILD_ACTIVE and document.active_build_id == build.build_id:
        # 重复发布同一 build：幂等返回，不报错也不重写
        logger.info("build %s already active; publish is a no-op", build.build_id)
        return build

    # manifest 完整性：库里实际 chunk 数必须与 Worker 声称的一致
    actual_count = (
        await session.execute(
            select(func.count())
            .select_from(ChunkRow)
            .where(
                ChunkRow.workspace_id == job.workspace_id,
                ChunkRow.build_id == build.build_id,
            )
        )
    ).scalar_one()
    if actual_count != manifest.chunk_count:
        raise AppError(
            ErrorCode.INDEX_INCOMPLETE,
            "chunk count mismatch between manifest and database",
            details={"manifest": manifest.chunk_count, "actual": actual_count},
        )
    if actual_count == 0:
        raise AppError(ErrorCode.INDEX_INCOMPLETE, "refusing to publish an empty build")

    # ---- 第 3 步：分配 index_version（锁内） ----
    # 允许跳号（中间有失败的重试），但不允许回退——回退意味着旧 build 覆盖新索引
    active_version = document.active_index_version or 0
    if build.index_version <= active_version:
        raise AppError(
            ErrorCode.STALE_ATTEMPT,
            "build index_version is not newer than the active one",
            details={
                "build_version": build.index_version,
                "active_version": document.active_index_version,
            },
        )

    now = datetime.now(UTC)

    # ---- 第 4 步：旧 active build 退休 ----
    if document.active_build_id is not None and document.active_build_id != build.build_id:
        await session.execute(
            text(
                """
                UPDATE index_builds
                SET state = 'retired', retired_at = :now
                WHERE workspace_id = :workspace_id AND build_id = :build_id
                  AND state = 'active'
                """
            ),
            {
                "now": now,
                "workspace_id": job.workspace_id,
                "build_id": document.active_build_id,
            },
        )

    # ---- 第 5 步：新 build 转正 + 切指针 ----
    build.state = BUILD_ACTIVE
    build.activated_at = now
    build.chunk_count = actual_count

    document.active_build_id = build.build_id
    document.active_index_version = build.index_version
    document.active_document_version_id = manifest.document_version_id
    document.generation = document.generation + 1
    document.row_version = document.row_version + 1

    # 版本快照标记成功
    await session.execute(
        text(
            """
            UPDATE document_versions SET parse_status = 'succeeded'
            WHERE workspace_id = :workspace_id AND document_version_id = :document_version_id
            """
        ),
        {
            "workspace_id": job.workspace_id,
            "document_version_id": manifest.document_version_id,
        },
    )

    # ---- 第 6 步：job 成功 + outbox（同事务） ----
    await session.execute(
        text(
            """
            UPDATE ingestion_jobs
            SET status = 'succeeded', error_code = NULL, updated_at = :now,
                lease_expires_at = NULL
            WHERE workspace_id = :workspace_id AND job_id = :job_id
              AND lease_epoch = :lease_epoch
            """
        ),
        {
            "now": now,
            "workspace_id": job.workspace_id,
            "job_id": job.job_id,
            "lease_epoch": job.lease_epoch,
        },
    )
    await queue.finish_attempt(session, attempt_id=job.attempt_id, outcome="succeeded")

    await session.execute(
        text(
            """
            INSERT INTO outbox (event_id, workspace_id, aggregate_type, aggregate_id,
                                event_type, payload, created_at)
            VALUES (:event_id, :workspace_id, 'document', :document_id,
                    'document.index_published', CAST(:payload AS jsonb), :now)
            """
        ),
        {
            "event_id": uuid4(),
            "workspace_id": job.workspace_id,
            "document_id": job.document_id,
            "payload": (
                f'{{"build_id":"{build.build_id}",'
                f'"index_version":{build.index_version},'
                f'"chunk_count":{actual_count}}}'
            ),
            "now": now,
        },
    )

    await session.flush()
    return build


async def retire_build(session: AsyncSession, *, workspace_id: UUID, build_id: UUID) -> None:
    await session.execute(
        text(
            """
            UPDATE index_builds SET state = 'retired', retired_at = :now
            WHERE workspace_id = :workspace_id AND build_id = :build_id
            """
        ),
        {"now": datetime.now(UTC), "workspace_id": workspace_id, "build_id": build_id},
    )


async def get_build(
    session: AsyncSession, *, workspace_id: UUID, build_id: UUID
) -> IndexBuildRow | None:
    return (
        await session.execute(
            select(IndexBuildRow).where(
                IndexBuildRow.workspace_id == workspace_id,
                IndexBuildRow.build_id == build_id,
            )
        )
    ).scalar_one_or_none()


async def find_version_by_hash(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    document_id: UUID,
    content_hash: str,
) -> DocumentVersionRow | None:
    """按内容哈希查历史版本——去重判断的落点。

    注意查的是 document_versions 而不是 documents：内容哈希属于版本，
    不属于文档。存在 documents 上会导致 A→B→A 时误判为"无变更"。
    """
    return (
        (
            await session.execute(
                select(DocumentVersionRow).where(
                    DocumentVersionRow.workspace_id == workspace_id,
                    DocumentVersionRow.document_id == document_id,
                    DocumentVersionRow.content_hash == content_hash,
                )
            )
        )
        .scalars()
        .first()
    )
