"""成员2 的 Worker：把作业从 claim 跑到 publish。

链路：claim → 取版本字节 → 解析 → 切片 → 向量化（带缓存）→ staging build
     → publish（原子切换）

失败语义：
- 未超 max_attempts → 退回 pending，等下一次 claim
- 超了 → failed，并写 error_code
- 抛出 STALE_ATTEMPT / VERSION_SUPERSEDED → 说明已被更新的尝试接管，
  不再重试（重试只会再失败一次），直接放弃本次

**不假定恰好执行一次**：任何时刻被打断，重新 claim 都会重做一遍，
所以每一步都必须可重入（staging build 先删后建、vector 先删后写）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.core.settings import settings
from pyknora.models.tables import ChunkRow, DocumentVersionRow
from pyknora.services.contracts.reads import KbRepo
from pyknora.services.ingest import parser, publish, splitter
from pyknora.services.ingest.embedder import EmbeddingProvider
from pyknora.services.ingest.embedding_service import EmbeddingService, digest_content
from pyknora.services.queue import jobs as queue
from pyknora.services.queue.jobs import ClaimedJob

logger = logging.getLogger(__name__)

# 这些错误重试也不会好，直接放弃本次尝试
_NO_RETRY = frozenset(
    {
        ErrorCode.STALE_ATTEMPT,
        ErrorCode.VERSION_SUPERSEDED,
        ErrorCode.UNSUPPORTED_FORMAT,
        ErrorCode.IDEMPOTENCY_CONFLICT,
    }
)


@dataclass(slots=True)
class ProcessResult:
    job_id: UUID
    status: str
    build_id: UUID | None = None
    chunk_count: int = 0
    error_code: str | None = None


async def _load_bytes(session: AsyncSession, *, scope: Scope, version_id: UUID) -> bytes:
    """从版本快照取正文。

    当前版本把正文直接放在 object_key 指向的对象存储里；本仓库尚未接对象存储，
    所以用一个可测试的替身表（document_blobs）。生产替换点只有这一个函数。
    """
    row = await session.execute(
        text(
            "SELECT content FROM document_blobs "
            "WHERE workspace_id = :ws AND document_version_id = :vid"
        ),
        {"ws": scope.workspace_id, "vid": version_id},
    )
    data = row.scalar_one_or_none()
    if data is None:
        raise AppError(ErrorCode.PARSE_FAILED, "document bytes missing for version")
    return data.encode("utf-8") if isinstance(data, str) else bytes(data)


async def _load_version(
    session: AsyncSession, *, scope: Scope, version_id: UUID
) -> DocumentVersionRow:
    row = (
        await session.execute(
            select(DocumentVersionRow).where(
                DocumentVersionRow.workspace_id == scope.workspace_id,
                DocumentVersionRow.document_version_id == version_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "document version not found")
    return row


async def process_job(
    session: AsyncSession,
    *,
    job: ClaimedJob,
    kb_repo: KbRepo,
    embedder: EmbeddingProvider,
) -> ProcessResult:
    """处理一件已 claim 的作业。调用方负责 commit。"""
    scope = Scope(workspace_id=job.workspace_id, principal_id=job.workspace_id)

    if job.job_type == "purge":
        from pyknora.services.ingest.documents import purge_document_chunks

        removed = await purge_document_chunks(session, scope=scope, document_id=job.document_id)
        await session.execute(
            text(
                "UPDATE ingestion_jobs SET status = 'succeeded', updated_at = now(), "
                "lease_expires_at = NULL WHERE job_id = :job_id AND lease_epoch = :epoch"
            ),
            {"job_id": job.job_id, "epoch": job.lease_epoch},
        )
        await queue.finish_attempt(session, attempt_id=job.attempt_id, outcome="succeeded")
        logger.info("purge removed %s chunks for %s", removed, job.document_id)
        return ProcessResult(job_id=job.job_id, status="succeeded", chunk_count=removed)

    if job.job_type != "ingest":
        raise AppError(ErrorCode.INVALID_REQUEST, f"unknown job type {job.job_type}")

    version_id = UUID(str(job.payload["document_version_id"]))
    filename = str(job.payload.get("filename") or "unnamed.txt")
    content_type = job.payload.get("content_type")
    kb_id = UUID(str(job.payload["kb_id"]))

    profile = await kb_repo.get_embedding_profile(scope, kb_id)
    if profile is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "knowledge base gone during ingestion")

    # 先确认版本快照仍在（给 404 而不是后面的 PARSE_FAILED，报错更准）
    await _load_version(session, scope=scope, version_id=version_id)
    data = await _load_bytes(session, scope=scope, version_id=version_id)

    # ---- 纯函数段：解析 + 切片 ----
    parsed = parser.parse(data, filename, content_type)
    chunks = splitter.chunk_parsed(
        parsed,
        chunk_size=getattr(profile, "chunk_size", settings.chunk_size),
        chunk_overlap=getattr(profile, "chunk_overlap", settings.chunk_overlap),
    )

    # ---- 分配 index_version（锁 documents 行） ----
    doc_row = await session.execute(
        text(
            "SELECT active_index_version FROM documents "
            "WHERE workspace_id = :ws AND document_id = :doc FOR UPDATE"
        ),
        {"ws": job.workspace_id, "doc": job.document_id},
    )
    row = doc_row.first()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "document gone during ingestion")
    next_version = (row[0] or 0) + 1

    # 重入保护：清掉本次 attempt 之前留下的同版本 staging 数据
    await session.execute(
        text(
            "DELETE FROM chunks WHERE workspace_id = :ws AND document_id = :doc "
            "AND attempt_id = :attempt"
        ),
        {"ws": job.workspace_id, "doc": job.document_id, "attempt": job.attempt_id},
    )
    await session.execute(
        text(
            "DELETE FROM index_builds WHERE workspace_id = :ws AND document_id = :doc "
            "AND attempt_id = :attempt AND state = 'staging'"
        ),
        {"ws": job.workspace_id, "doc": job.document_id, "attempt": job.attempt_id},
    )

    build = await publish.create_staging_build(
        session,
        workspace_id=job.workspace_id,
        document_id=job.document_id,
        attempt_id=job.attempt_id,
        index_version=next_version,
        embedding_model_key=embedder.model_key,
        embedding_dim=embedder.dim,
        chunk_size=getattr(profile, "chunk_size", settings.chunk_size),
        chunk_overlap=getattr(profile, "chunk_overlap", settings.chunk_overlap),
        normalize=getattr(profile, "normalize", True),
    )

    # ---- 向量化（带缓存） ----
    service = EmbeddingService(embedder, session)
    vectors = await service.embed_cached([c.content for c in chunks])
    await service.bump_hits([c.content for c in chunks])

    # ---- 写 staging chunk（视图不可见） ----
    for chunk, vec in zip(chunks, vectors, strict=True):
        session.add(
            ChunkRow(
                chunk_id=__import__("uuid").uuid4(),
                workspace_id=job.workspace_id,
                document_id=job.document_id,
                build_id=build.build_id,
                index_version=build.index_version,
                attempt_id=job.attempt_id,
                chunk_ordinal=chunk.ordinal,
                content=chunk.content,
                content_hash=digest_content(chunk.content),
                page_no=chunk.page_no,
                heading_path=chunk.heading_path,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                token_count=chunk.token_count,
                locator_ref=chunk.locator_ref,
                embedding=vec,
            )
        )
    await session.flush()

    # tsvector 由数据库算，避免 Python 侧维护第二套分词规则
    await session.execute(
        text(
            "UPDATE chunks SET tsvector = to_tsvector('simple', content) "
            "WHERE workspace_id = :ws AND build_id = :build"
        ),
        {"ws": job.workspace_id, "build": build.build_id},
    )

    # ---- 发布 ----
    published = await publish.publish(
        session,
        job=job,
        manifest=publish.PublishManifest(
            build_id=build.build_id,
            document_version_id=version_id,
            chunk_count=len(chunks),
            embedding_model_key=embedder.model_key,
            embedding_dim=embedder.dim,
        ),
    )
    return ProcessResult(
        job_id=job.job_id,
        status="succeeded",
        build_id=published.build_id,
        chunk_count=len(chunks),
    )


async def run_once(
    session: AsyncSession,
    *,
    worker_id: str,
    kb_repo: KbRepo,
    embedder: EmbeddingProvider,
) -> ProcessResult | None:
    """取一件、跑一件。返回 None 表示队列空。

    **作业体包在 SAVEPOINT 里**。这不只是"好看"：PG 里任何一条语句报错，
    整个事务立即进入 aborted 态，之后连 `UPDATE job_attempts` 都会被拒。
    如果不先回滚到保存点，`except` 里的清理语句自己也会失败，
    真正的错误就被 "transaction is aborted" 盖掉了——排错时极难定位。
    """
    job = await queue.claim(session, worker_id=worker_id, lease_seconds=settings.lease_seconds)
    if job is None:
        return None
    try:
        async with session.begin_nested():
            return await process_job(session, job=job, kb_repo=kb_repo, embedder=embedder)
    except AppError as exc:
        # 此时保存点已回滚，作业体写的 staging 数据都不在了，但外层事务还活着
        await _record_failure(session, job=job, code=exc.code)
        logger.warning("job %s failed with %s: %s", job.job_id, exc.code, exc.message)
        return ProcessResult(job_id=job.job_id, status="failed", error_code=exc.code)
    except Exception:
        await _record_failure(session, job=job, code=ErrorCode.INTERNAL_ERROR)
        raise


async def _record_failure(session: AsyncSession, *, job: ClaimedJob, code: str) -> None:
    """把失败记进 attempts 与 jobs。必须在保存点回滚之后调用。"""
    await queue.finish_attempt(session, attempt_id=job.attempt_id, outcome="failed")
    if code in _NO_RETRY:
        # 重试也不会好，直接终结，不再占队列
        await session.execute(
            text(
                "UPDATE ingestion_jobs SET status = 'superseded', error_code = :code, "
                "updated_at = now(), lease_expires_at = NULL "
                "WHERE job_id = :job_id AND lease_epoch = :epoch"
            ),
            {"code": code, "job_id": job.job_id, "epoch": job.lease_epoch},
        )
    else:
        await queue.mark_failed(
            session, job=job, error_code=code, max_attempts=settings.max_attempts
        )
