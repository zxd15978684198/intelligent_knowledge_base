"""成员5：任务16 重建索引（`POST /api/v1/admin/reindex` 的服务层）。

## 重建的是什么，不是什么

重建 = **保留正文与切片边界，用 KB 当前固化参数重新算向量与倒排**。
所以这里不重新解析原始文件、也不重新切片：源数据来自源 build 的 `chunks`
（内容、序号、页码、字符区间原样搬运），`content_hash` / `char_start` / `char_end`
因此逐字节不变——引用与高亮不会因为一次重建而错位。

这也解释了 `chunk_size` / `chunk_overlap` 的处理：它们照 KB profile 写进新 build
（那是"本次索引的参数"），但**不会改变切片结果**。真正的"换切片参数重建"要重跑
解析与切片，属于 `ingest` 作业，不在本接口范围内（已在交付说明里列为未实现/风险）。

同一 KB 的记录行语义：一次调用 = 一条 `index_migrations` 记录，KB 里每个"有 active
build 的文档"各建一个 **新的 `index_version`**（`active_index_version + 1`）与一个
新的 staging build，然后逐文档调用 `publish()` 激活。`publish()` 是唯一的状态切换
入口，这里不改它的语义（AGENTS.md I3）。

## 为什么在请求内同步跑

`publish()` 的 fencing 依赖一次"已 claim"的 `(job_id, lease_epoch, attempt_id)`。
现有 worker 只认 `ingest` / `purge` 两种 job_type（`workers/ingest_worker.py:147-148`），
要让重建异步化必须改 worker——那是成员2 的文件，本轮不动。于是本模块自己造一个
**只被本事务持有的作业凭据**：入队 → 立刻把租约收到自己名下（`lease_epoch + 1`、
`status='processing'`、`lease_expires_at` 拉到 1 小时后），worker 便不会来抢
（`claim()` 只取 pending 或租约已过期的行）。作业行与本次重建同事务，请求回滚则
作业行一起消失，不会留下"永远 processing"的僵尸。

代价写进风险：大 KB 的重建会占住一个 HTTP 请求（含 embedding 计算）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.db.repos.index_migrations import (
    MODE_REINDEX,
    STATE_FAILED,
    STATE_RUNNING,
    STATE_SUCCEEDED,
    SqlIndexMigrationRepo,
)
from pyknora.models.tables import ChunkRow, JobAttemptRow
from pyknora.services.ingest import publish
from pyknora.services.ingest.embedder import check_profile_compat
from pyknora.services.ingest.embedding_service import EmbeddingService, digest_content
from pyknora.services.queue import jobs as queue

logger = logging.getLogger(__name__)

#: 本接口支持的迁移模式。`export` / `replay` 在记账表里是合法值，但本轮没有实现，
#: 收到就明确报错——静默当成 reindex 跑会烧掉一次全量重建，且调用方以为它导出了。
SUPPORTED_MODES = frozenset({MODE_REINDEX})

#: 作业类型。刻意与 `ingest` 区分：队列里一眼能看出这行不是摄取作业。
JOB_TYPE_REINDEX = "reindex"

#: 自己持有租约时的 worker 标识（`job_attempts.worker_id` 非空）。
_SYNCHRONOUS_WORKER_ID = "admin-reindex"

_LEASE = timedelta(hours=1)

#: 源 build 的切片正文与定位信息。**不取 embedding**：重建的意义就是用新参数重算它。
_SOURCE_CHUNKS_SQL = text(
    """
    SELECT chunk_ordinal, content, page_no, heading_path, char_start, char_end,
           token_count, locator_ref
    FROM chunks
    WHERE workspace_id = :workspace_id
      AND document_id = :document_id
      AND build_id = :build_id
    ORDER BY chunk_ordinal
    """
)

_LOCK_DOCUMENT_SQL = text(
    """
    SELECT document_id, active_build_id, active_index_version,
           active_document_version_id, desired_document_version_id
    FROM documents
    WHERE workspace_id = :workspace_id AND document_id = :document_id
      AND deleted_at IS NULL
    FOR UPDATE
    """
)

_INDEXED_DOCUMENTS_SQL = text(
    """
    SELECT document_id
    FROM documents
    WHERE workspace_id = :workspace_id
      AND kb_id = :kb_id
      AND deleted_at IS NULL
      AND active_build_id IS NOT NULL
    ORDER BY document_id
    """
)

#: 把刚入队的作业立刻收进本事务：worker 的 claim() 只取 pending 或租约过期的行，
#: 这一步之后它不会再碰这行，而 `publish()` 要求的 lease_epoch 也就与库内一致了。
_OWN_LEASE_SQL = text(
    """
    UPDATE ingestion_jobs
    SET status = 'processing',
        attempt = attempt + 1,
        lease_epoch = lease_epoch + 1,
        lease_expires_at = :expires_at,
        worker_id = :worker_id,
        updated_at = :now
    WHERE workspace_id = :workspace_id
      AND job_id = :job_id
      AND status = 'pending'
    RETURNING attempt, lease_epoch, payload
    """
)


@dataclass(slots=True)
class _Recipe:
    """一次重建要写进新 build 的参数（来自 KB profile，创建后不再变）。"""

    model_key: str
    dim: int
    chunk_size: int
    chunk_overlap: int
    normalize: bool


@dataclass(slots=True)
class _DocumentRebuild:
    document_id: UUID
    source_build_id: UUID
    target_build_id: UUID
    index_version: int
    chunk_count: int


@dataclass(slots=True)
class ReindexOutcome:
    """一次调用的结果。字段名字与 `/admin/reindex` 的响应体一一对应。"""

    migration_id: UUID
    kb_id: UUID
    mode: str
    state: str
    documents: int = 0
    chunks: int = 0
    skipped: int = 0
    failed: int = 0
    replayed: bool = False
    source_build_ids: list[UUID] = field(default_factory=list)
    target_build_ids: list[UUID] = field(default_factory=list)


def _recipe_from(profile: Any) -> _Recipe:
    """从 `EmbeddingProfile` 取参数。

    用 `getattr` 而不是直接取属性：`KbRepo` 是契约协议，返回类型是 `object | None`，
    不同实现（DB / Fake）不必是同一个类，但必须带这几个字段。
    """
    return _Recipe(
        model_key=str(profile.embedding_model_key),
        dim=int(profile.embedding_dim),
        chunk_size=int(profile.chunk_size),
        chunk_overlap=int(profile.chunk_overlap),
        normalize=bool(profile.normalize),
    )


async def reindex_knowledge_base(
    session: AsyncSession,
    *,
    scope: Scope,
    kb_repo: Any,
    kb_id: UUID,
    embedder: Any,
    mode: str = MODE_REINDEX,
    idempotency_key: str | None = None,
) -> ReindexOutcome:
    """把一个 KB 下所有已发布文档按当前 profile 重建一遍索引。

    幂等：带 `idempotency_key` 且此前已有同键**成功**的记账行时，直接复用那条记录
    （`replayed=True`），不新建 build——重试请求不会把版本号刷上去。
    不带键时"安全重复执行"：每次调用都是全量重建，旧 build 被 `publish()` 退休，
    任何时刻每个文档最多一个 active build。
    """
    if mode not in SUPPORTED_MODES:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"mode '{mode}' is not implemented in this release",
            details={"supported_modes": sorted(SUPPORTED_MODES)},
        )

    profile = await kb_repo.get_embedding_profile(scope, kb_id)
    if profile is None:
        # 走到这一步说明 KB 存在但 profile 读不到；调用方拿到 404 而不是空跑。
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "knowledge base embedding profile not found")

    recipe = _recipe_from(profile)
    # I7 的同一条闸门（与上传入口、worker 复用同一实现）：KB 配方与当前后端不一致就
    # 报错，绝不截断/补零。这里不手写比较——两处各写一套迟早会漂移。
    check_profile_compat(
        profile,
        dim=int(getattr(embedder, "dim", recipe.dim)),
        model_key=str(getattr(embedder, "model_key", recipe.model_key)),
    )

    repo = SqlIndexMigrationRepo(session)

    if idempotency_key:
        previous = await repo.find_succeeded_by_idempotency_key(
            scope, kb_id=kb_id, mode=mode, idempotency_key=idempotency_key
        )
        if previous is not None:
            logger.info(
                "reindex replayed from migration %s (kb=%s key=%s)",
                previous.migration_id,
                kb_id,
                idempotency_key,
            )
            return _outcome_from_row(previous)

    document_ids = [
        row[0]
        for row in (
            await session.execute(
                _INDEXED_DOCUMENTS_SQL, {"workspace_id": scope.workspace_id, "kb_id": kb_id}
            )
        ).all()
    ]

    migration = await repo.create(
        scope,
        kb_id=kb_id,
        mode=mode,
        state=STATE_RUNNING,
        manifest=_manifest(
            mode=mode,
            idempotency_key=idempotency_key,
            recipe=recipe,
            documents_total=len(document_ids),
        ),
    )

    rebuilt: list[_DocumentRebuild] = []
    skipped = 0
    failed = 0
    error_code: str | None = None

    for document_id in document_ids:
        try:
            # 每个文档一个 SAVEPOINT：一个文档失败（比如被并发删掉、维度异常）只回滚它
            # 自己，已成功的文档保持已发布。不留半截 staging 数据在库里。
            async with session.begin_nested():
                outcome = await _reindex_document(
                    session,
                    scope=scope,
                    document_id=document_id,
                    recipe=recipe,
                    embedder=embedder,
                    migration_id=migration.migration_id,
                )
        except AppError as exc:
            failed += 1
            error_code = error_code or exc.code
            logger.warning(
                "reindex skipped document %s of migration %s: %s (%s)",
                document_id,
                migration.migration_id,
                exc.code,
                exc.message,
            )
            continue
        if outcome is None:
            # 没正文 / 有更新版本在等发布：重建它只会产出过时索引，如实跳过。
            skipped += 1
            continue
        rebuilt.append(outcome)

    chunk_total = sum(item.chunk_count for item in rebuilt)
    state = STATE_SUCCEEDED if failed == 0 else STATE_FAILED
    final_manifest = _manifest(
        mode=mode,
        idempotency_key=idempotency_key,
        recipe=recipe,
        documents_total=len(document_ids),
    ) | {
        "documents_rebuilt": len(rebuilt),
        "documents_skipped": skipped,
        "documents_failed": failed,
        "chunks_written": chunk_total,
        "source_build_ids": [str(item.source_build_id) for item in rebuilt],
        "target_build_ids": [str(item.target_build_id) for item in rebuilt],
    }
    await repo.finish(
        scope,
        migration.migration_id,
        state=state,
        manifest=final_manifest,
        error_code=error_code,
        target_build_id=rebuilt[-1].target_build_id if rebuilt else None,
    )

    logger.info(
        "reindex %s finished: state=%s rebuilt=%d skipped=%d failed=%d chunks=%d",
        migration.migration_id,
        state,
        len(rebuilt),
        skipped,
        failed,
        chunk_total,
    )
    return ReindexOutcome(
        migration_id=migration.migration_id,
        kb_id=kb_id,
        mode=mode,
        state=state,
        documents=len(rebuilt),
        chunks=chunk_total,
        skipped=skipped,
        failed=failed,
        replayed=False,
        source_build_ids=[item.source_build_id for item in rebuilt],
        target_build_ids=[item.target_build_id for item in rebuilt],
    )


async def _reindex_document(
    session: AsyncSession,
    *,
    scope: Scope,
    document_id: UUID,
    recipe: _Recipe,
    embedder: Any,
    migration_id: UUID,
) -> _DocumentRebuild | None:
    """重建单个文档；返回 None 表示"跳过"（调用方据此计数）。

    流程与 `ingest_worker.process_job` 的发布段一致（建 staging build → 写 chunk →
    `publish()`），差别只有一个：chunk 正文来自源 build 而不是解析结果。
    """
    document = (
        (
            await session.execute(
                _LOCK_DOCUMENT_SQL,
                {"workspace_id": scope.workspace_id, "document_id": document_id},
            )
        )
        .mappings()
        .first()
    )
    if document is None:
        return None

    source_build_id = document["active_build_id"]
    source_version = document["active_index_version"] or 0
    document_version_id = document["active_document_version_id"]
    if source_build_id is None or document_version_id is None:
        return None
    desired = document["desired_document_version_id"]
    if desired is not None and desired != document_version_id:
        # 有新版本在等发布（publish 的 VERSION_SUPERSEDED 条件）。此时重建旧版本等于
        # 给一份马上要被替换的内容算向量，跳过并在记账里如实计数。
        return None

    chunks = (
        (
            await session.execute(
                _SOURCE_CHUNKS_SQL,
                {
                    "workspace_id": scope.workspace_id,
                    "document_id": document_id,
                    "build_id": source_build_id,
                },
            )
        )
        .mappings()
        .all()
    )
    if not chunks:
        return None

    job = await _own_reindex_job(
        session, scope=scope, document_id=document_id, migration_id=migration_id
    )
    build = await publish.create_staging_build(
        session,
        workspace_id=scope.workspace_id,
        document_id=document_id,
        attempt_id=job.attempt_id,
        # 与 `publish.allocate_index_version()` 同一口径：锁内取 active+1，
        # 这里 documents 行已经 FOR UPDATE 锁住（上面那条 SELECT），所以不会抢号。
        index_version=source_version + 1,
        embedding_model_key=recipe.model_key,
        embedding_dim=recipe.dim,
        chunk_size=recipe.chunk_size,
        chunk_overlap=recipe.chunk_overlap,
        normalize=recipe.normalize,
    )

    service = EmbeddingService(embedder, session)
    texts = [row["content"] for row in chunks]
    vectors = await service.embed_cached(texts)
    await service.bump_hits(texts)

    for row, vec in zip(chunks, vectors, strict=True):
        session.add(
            ChunkRow(
                chunk_id=uuid4(),
                workspace_id=scope.workspace_id,
                document_id=document_id,
                build_id=build.build_id,
                index_version=build.index_version,
                attempt_id=job.attempt_id,
                chunk_ordinal=row["chunk_ordinal"],
                content=row["content"],
                content_hash=digest_content(row["content"]),
                page_no=row["page_no"],
                heading_path=row["heading_path"],
                char_start=row["char_start"],
                char_end=row["char_end"],
                token_count=row["token_count"],
                locator_ref=row["locator_ref"],
                embedding=vec,
            )
        )
    await session.flush()

    # tsvector 由数据库算（与 worker 同一句），避免 Python 侧维护第二套分词规则。
    await session.execute(
        text(
            "UPDATE chunks SET tsvector = to_tsvector('simple', content) "
            "WHERE workspace_id = :workspace_id AND build_id = :build_id"
        ),
        {"workspace_id": scope.workspace_id, "build_id": build.build_id},
    )

    published = await publish.publish(
        session,
        job=job,
        manifest=publish.PublishManifest(
            build_id=build.build_id,
            document_version_id=document_version_id,
            chunk_count=len(chunks),
            embedding_model_key=recipe.model_key,
            embedding_dim=recipe.dim,
        ),
    )
    return _DocumentRebuild(
        document_id=document_id,
        source_build_id=source_build_id,
        target_build_id=published.build_id,
        index_version=published.index_version,
        chunk_count=len(chunks),
    )


async def _own_reindex_job(
    session: AsyncSession, *, scope: Scope, document_id: UUID, migration_id: UUID
) -> queue.ClaimedJob:
    """给 `publish()` 造一份本事务独占的作业凭据。

    幂等键带上 `migration_id`：同一迁移内同一文档只会有一个作业；不同迁移是不同作业，
    与 `ingestion_jobs (workspace_id, idempotency_key)` 的唯一约束匹配。
    """
    row = await queue.enqueue(
        session,
        scope=scope,
        document_id=document_id,
        job_type=JOB_TYPE_REINDEX,
        payload={
            "migration_id": str(migration_id),
            "mode": MODE_REINDEX,
            "source": "admin/reindex",
        },
        idempotency_key=f"reindex:{migration_id}:{document_id}",
    )
    now = datetime.now(UTC)
    owned = (
        (
            await session.execute(
                _OWN_LEASE_SQL,
                {
                    "expires_at": now + _LEASE,
                    "worker_id": _SYNCHRONOUS_WORKER_ID,
                    "now": now,
                    "workspace_id": scope.workspace_id,
                    "job_id": row.job_id,
                },
            )
        )
        .mappings()
        .one()
    )

    attempt_id = uuid4()
    session.add(
        JobAttemptRow(
            attempt_id=attempt_id,
            workspace_id=scope.workspace_id,
            job_id=row.job_id,
            lease_epoch=owned["lease_epoch"],
            worker_id=_SYNCHRONOUS_WORKER_ID,
        )
    )
    await session.flush()

    payload = owned["payload"]
    return queue.ClaimedJob(
        job_id=row.job_id,
        workspace_id=scope.workspace_id,
        document_id=document_id,
        job_type=JOB_TYPE_REINDEX,
        attempt=owned["attempt"],
        lease_epoch=owned["lease_epoch"],
        attempt_id=attempt_id,
        payload=payload if isinstance(payload, dict) else {},
    )


def _manifest(
    *,
    mode: str,
    idempotency_key: str | None,
    recipe: _Recipe,
    documents_total: int,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "idempotency_key": idempotency_key,
        "documents_total": documents_total,
        "recipe": {
            "embedding_model_key": recipe.model_key,
            "embedding_dim": recipe.dim,
            "chunk_size": recipe.chunk_size,
            "chunk_overlap": recipe.chunk_overlap,
            "normalize": recipe.normalize,
        },
    }


def _outcome_from_row(row: Any) -> ReindexOutcome:
    """把一条已成功的记账行还原成响应（幂等重放路径）。"""
    manifest = row.manifest or {}
    return ReindexOutcome(
        migration_id=row.migration_id,
        kb_id=row.kb_id,
        mode=row.mode,
        state=row.state,
        documents=int(manifest.get("documents_rebuilt", 0)),
        chunks=int(manifest.get("chunks_written", 0)),
        skipped=int(manifest.get("documents_skipped", 0)),
        failed=int(manifest.get("documents_failed", 0)),
        replayed=True,
        source_build_ids=[UUID(str(value)) for value in manifest.get("source_build_ids", [])],
        target_build_ids=[UUID(str(value)) for value in manifest.get("target_build_ids", [])],
    )
