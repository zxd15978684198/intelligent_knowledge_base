"""S07 / S07b：文档上传、hash 三分支、删除。

hash 三分支是 Day2 的 P0 要求，也是"增量索引"的入口：
- 文档不存在        → 新建 v1 + 入队
- 文档存在、hash 同  → 不升版本、不切片、不调 Embedding，直接返回 deduped=True
- 文档存在、hash 异  → 新建版本 + 入队，旧版本先保持可见直到新索引发布

**内容哈希落在 document_versions 而不是 documents**：存在文档表上会导致
A→B→A 时第三次上传被判成"无变更"，而实际上中间索引已是 B 的内容。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.errors import AppError, ErrorCode, not_found
from pyknora.core.scope import Scope
from pyknora.core.settings import settings
from pyknora.models.tables import DocumentBlobRow as BLOB_TABLE
from pyknora.models.tables import DocumentRow, DocumentVersionRow, IndexBuildRow
from pyknora.services.contracts.reads import KbRepo
from pyknora.services.ingest.embedder import check_profile_compat
from pyknora.services.ingest.publish import find_version_by_hash
from pyknora.services.queue import jobs as queue

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UploadResult:
    document_id: UUID
    deduped: bool
    job_id: UUID | None
    document_version_id: UUID
    status: str


def compute_content_hash(data: bytes) -> str:
    """SHA-256。流式算与一次性算结果相同，这里数据已在内存，直接算。"""
    return hashlib.sha256(data).hexdigest()


async def upload_document(
    session: AsyncSession,
    *,
    scope: Scope,
    kb_repo: KbRepo,
    kb_id: UUID,
    filename: str,
    data: bytes,
    content_type: str | None = None,
    principal_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> UploadResult:
    """上传入口。返回 deduped 标志，调用方据此决定回 200 还是 202。"""
    if not data:
        raise AppError(ErrorCode.INVALID_REQUEST, "empty upload")

    # 1) 目标 KB 必须存在且在本 scope 内。拿不到 profile 就等于 KB 不可见。
    profile = await kb_repo.get_embedding_profile(scope, kb_id)
    if profile is None:
        raise not_found("knowledge base")

    # 1b) 上传即校验 embedding 配方。成员1 的 KB profile 与当前部署的 embedding
    #     配置不一致时，在这里就以 4xx 失败，而不是让作业入队、等 worker 消费后
    #     才标 failed——那样用户得轮询才发现，staging 数据还白写了一遍。
    check_profile_compat(
        profile, dim=settings.embedding_dim, model_key=settings.embedding_model_key
    )

    content_hash = compute_content_hash(data)

    # 2) 找同名文档（同一 KB 内）。没有则新建文档行 + v1。
    existing_doc = (
        await session.execute(
            select(DocumentRow)
            .where(
                DocumentRow.workspace_id == scope.workspace_id,
                DocumentRow.kb_id == kb_id,
                DocumentRow.deleted_at.is_(None),
                DocumentRow.document_id.in_(
                    select(DocumentVersionRow.document_id).where(
                        DocumentVersionRow.workspace_id == scope.workspace_id,
                        DocumentVersionRow.object_key.like(f"%/{filename}"),
                    )
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing_doc is None:
        document = DocumentRow(
            document_id=uuid4(),
            workspace_id=scope.workspace_id,
            kb_id=kb_id,
            generation=1,
            row_version=1,
        )
        session.add(document)
        await session.flush()
        version = await _create_version(
            session,
            scope=scope,
            document_id=document.document_id,
            content_hash=content_hash,
            data=data,
            filename=filename,
            principal_id=principal_id or scope.principal_id,
            source="upload",
        )
        document.desired_document_version_id = version.document_version_id
        job = await queue.enqueue(
            session,
            scope=scope,
            document_id=document.document_id,
            job_type="ingest",
            payload={
                "document_version_id": str(version.document_version_id),
                "filename": filename,
                "content_type": content_type,
                "kb_id": str(kb_id),
            },
            idempotency_key=idempotency_key,
        )
        return UploadResult(
            document_id=document.document_id,
            deduped=False,
            job_id=job.job_id,
            document_version_id=version.document_version_id,
            status="pending",
        )

    # 3) 已有文档：查同一 hash 的历史版本
    same = await find_version_by_hash(
        session,
        workspace_id=scope.workspace_id,
        document_id=existing_doc.document_id,
        content_hash=content_hash,
    )
    if same is not None:
        # 分支二：无变更。不升版本、不切片、不调 Embedding、不入队。
        # 但如果当前活动版本不是这一版（例如连续 A→B→A），仍需把指针指回来。
        if existing_doc.active_document_version_id != same.document_version_id:
            existing_doc.desired_document_version_id = same.document_version_id
            await session.flush()
        return UploadResult(
            document_id=existing_doc.document_id,
            deduped=True,
            job_id=None,
            document_version_id=same.document_version_id,
            status="deduped",
        )

    # 分支三：有变更。新建版本 + 入队；旧索引保持可见直到新索引发布。
    version = await _create_version(
        session,
        scope=scope,
        document_id=existing_doc.document_id,
        content_hash=content_hash,
        data=data,
        filename=filename,
        principal_id=principal_id or scope.principal_id,
        source="upload",
    )
    existing_doc.desired_document_version_id = version.document_version_id
    existing_doc.row_version = existing_doc.row_version + 1
    job = await queue.enqueue(
        session,
        scope=scope,
        document_id=existing_doc.document_id,
        job_type="ingest",
        payload={
            "document_version_id": str(version.document_version_id),
            "filename": filename,
            "content_type": content_type,
            "kb_id": str(kb_id),
        },
        idempotency_key=idempotency_key,
    )
    await session.flush()
    return UploadResult(
        document_id=existing_doc.document_id,
        deduped=False,
        job_id=job.job_id,
        document_version_id=version.document_version_id,
        status="pending",
    )


async def _create_version(
    session: AsyncSession,
    *,
    scope: Scope,
    document_id: UUID,
    content_hash: str,
    data: bytes,
    filename: str,
    principal_id: UUID,
    source: str,
) -> DocumentVersionRow:
    """写不可变版本快照。对象 key 用内容哈希，同内容天然落到同一 key。"""
    version = DocumentVersionRow(
        document_version_id=uuid4(),
        workspace_id=scope.workspace_id,
        document_id=document_id,
        content_hash=content_hash,
        byte_size=len(data),
        object_key=f"objects/{scope.workspace_id}/{content_hash}/{filename}",
        parse_status="pending",
        created_by_principal_id=principal_id,
        ingestion_source=source,
    )
    session.add(version)
    await session.flush()

    # 正文落到替身表（真实部署这里是 PUT 对象存储）。用 upsert：
    # 同一内容重复上传会落到同一个 object_key，写两次不是错误。
    await session.execute(
        pg_insert(BLOB_TABLE)
        .values(
            workspace_id=scope.workspace_id,
            document_version_id=version.document_version_id,
            content=data,
        )
        .on_conflict_do_update(
            index_elements=["workspace_id", "document_version_id"],
            set_={"content": data},
        )
    )
    return version


async def delete_document(
    session: AsyncSession,
    *,
    scope: Scope,
    document_id: UUID,
) -> UUID:
    """S07b：删除文档。

    顺序不可反：先在同一事务里置 `deleted_at` **和** `active_build_id = NULL`，
    视图立刻看不见这篇文档的全部 chunk；再入队异步清理。
    反过来先删 chunk 再置 deleted_at，中间那段窗口检索还能命中已删文档。

    返回 purge job 的 id。
    """
    document = (
        await session.execute(
            select(DocumentRow)
            .where(
                DocumentRow.workspace_id == scope.workspace_id,
                DocumentRow.document_id == document_id,
                DocumentRow.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if document is None:
        raise not_found("document")

    now = datetime.now(UTC)
    document.deleted_at = now
    document.active_build_id = None
    document.row_version = document.row_version + 1
    document.generation = document.generation + 1
    await session.flush()

    job = await queue.enqueue(
        session,
        scope=scope,
        document_id=document_id,
        job_type="purge",
        payload={"document_id": str(document_id)},
    )
    return job.job_id


async def purge_document_chunks(session: AsyncSession, *, scope: Scope, document_id: UUID) -> int:
    """purge 任务本体：真正删掉 chunk 与 build 行。

    此时可见性已经由 `deleted_at` 切断，所以这里慢一点不影响正确性。
    """
    deleted = await session.execute(
        text("DELETE FROM chunks WHERE workspace_id = :ws AND document_id = :doc"),
        {"ws": scope.workspace_id, "doc": document_id},
    )
    await session.execute(
        text(
            "UPDATE index_builds SET state = 'retired', retired_at = :now "
            "WHERE workspace_id = :ws AND document_id = :doc AND state <> 'retired'"
        ),
        {"now": datetime.now(UTC), "ws": scope.workspace_id, "doc": document_id},
    )
    return deleted.rowcount


async def count_chunks(session: AsyncSession, *, workspace_id: UUID, document_id: UUID) -> int:
    return (
        await session.execute(
            text("SELECT count(*) FROM chunks WHERE workspace_id = :ws AND document_id = :doc"),
            {"ws": workspace_id, "doc": document_id},
        )
    ).scalar_one()


async def count_jobs(session: AsyncSession, *, workspace_id: UUID) -> int:
    """作业总数。A1 用它断言"第二次上传没有新建作业"。"""
    return (
        await session.execute(
            text("SELECT count(*) FROM ingestion_jobs WHERE workspace_id = :ws"),
            {"ws": workspace_id},
        )
    ).scalar_one()


async def upsert_document_row(
    session: AsyncSession, *, workspace_id: UUID, kb_id: UUID, document_id: UUID
) -> None:
    """幂等插入文档行，供 seed 与测试使用。"""
    stmt = pg_insert(DocumentRow).values(
        document_id=document_id,
        workspace_id=workspace_id,
        kb_id=kb_id,
        generation=1,
        row_version=1,
    )
    await session.execute(stmt.on_conflict_do_nothing(index_elements=["document_id"]))


async def active_build_of(
    session: AsyncSession, *, workspace_id: UUID, document_id: UUID
) -> IndexBuildRow | None:
    row = await session.execute(
        text("SELECT build_id FROM documents WHERE workspace_id = :ws AND document_id = :doc"),
        {"ws": workspace_id, "doc": document_id},
    )
    build_id = row.scalar_one_or_none()
    if build_id is None:
        return None
    return (
        await session.execute(
            select(IndexBuildRow).where(
                IndexBuildRow.workspace_id == workspace_id,
                IndexBuildRow.build_id == build_id,
            )
        )
    ).scalar_one_or_none()
