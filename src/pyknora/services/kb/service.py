"""知识库写入语义：唯一性、乐观锁、软删除事件与删库的文档级联。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.errors import AppError, ErrorCode, not_found
from pyknora.core.scope import Scope
from pyknora.schemas.kb import EmbeddingProfile, KnowledgeBase
from pyknora.services.queue import jobs as queue


class KnowledgeBaseWriter(Protocol):
    async def create(
        self, scope: Scope, *, name: str, profile: EmbeddingProfile
    ) -> KnowledgeBase: ...

    async def get(self, scope: Scope, kb_id: UUID) -> KnowledgeBase | None: ...

    async def rename(
        self, scope: Scope, kb_id: UUID, *, name: str, expected_version: int
    ) -> KnowledgeBase | None: ...

    async def soft_delete(
        self, scope: Scope, kb_id: UUID, *, expected_version: int
    ) -> KnowledgeBase | None: ...


async def create_knowledge_base(
    repo: KnowledgeBaseWriter,
    scope: Scope,
    *,
    name: str,
    profile: EmbeddingProfile,
) -> KnowledgeBase:
    try:
        return await repo.create(scope, name=name.strip(), profile=profile)
    except IntegrityError as exc:
        # 用 CONFLICT 而不是 IDEMPOTENCY_CONFLICT：后者留给"同一个幂等键换了 payload"，
        # 名字撞唯一索引是普通冲突。core/errors.py:17-20 对注册撞唯一键已定过同一口径。
        raise AppError(ErrorCode.CONFLICT, "knowledge base name already exists") from exc


async def rename_knowledge_base(
    repo: KnowledgeBaseWriter,
    scope: Scope,
    kb_id: UUID,
    *,
    name: str,
    expected_version: int,
) -> KnowledgeBase:
    current = await repo.get(scope, kb_id)
    if current is None:
        raise not_found("knowledge base")
    if current.row_version != expected_version:
        raise AppError(ErrorCode.REVISION_CONFLICT, "knowledge base revision conflict")
    try:
        updated = await repo.rename(
            scope, kb_id, name=name.strip(), expected_version=expected_version
        )
    except IntegrityError as exc:
        raise AppError(ErrorCode.CONFLICT, "knowledge base name already exists") from exc
    if updated is None:
        raise AppError(ErrorCode.REVISION_CONFLICT, "knowledge base revision conflict")
    return updated


async def delete_knowledge_base(
    session: AsyncSession,
    repo: KnowledgeBaseWriter,
    scope: Scope,
    kb_id: UUID,
    *,
    expected_version: int,
) -> KnowledgeBase:
    current = await repo.get(scope, kb_id)
    if current is None:
        raise not_found("knowledge base")
    if current.row_version != expected_version:
        raise AppError(ErrorCode.REVISION_CONFLICT, "knowledge base revision conflict")
    deleted = await repo.soft_delete(scope, kb_id, expected_version=expected_version)
    if deleted is None:
        raise AppError(ErrorCode.REVISION_CONFLICT, "knowledge base revision conflict")
    await _cascade_delete_documents(session, scope=scope, kb_id=kb_id)
    await session.execute(
        text(
            """
            INSERT INTO outbox (
                event_id, workspace_id, aggregate_type, aggregate_id, event_type, payload
            ) VALUES (
                :event_id, :workspace_id, 'knowledge_base', :kb_id,
                'knowledge_base.deleted', CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "event_id": uuid4(),
            "workspace_id": scope.workspace_id,
            "kb_id": kb_id,
            "payload": f'{{"kb_id":"{kb_id}","generation":{deleted.generation}}}',
        },
    )
    return deleted


async def _cascade_delete_documents(session: AsyncSession, *, scope: Scope, kb_id: UUID) -> int:
    """删除知识库时把它的文档一起软删并逐篇入队 purge。

    **为什么必须在这里做**：`documents` 对 `knowledge_bases` 是
    `ON DELETE RESTRICT`，而且库本身也是软删（`deleted_at`），所以删库不会连带删文档。
    先前的结果是——库从列表里消失了，它的文档、chunk、向量全都还在：
    `v_active_chunks` 只过滤 `documents.deleted_at` 与活动 build，不看库的死活，
    于是这些 chunk 继续可被检索命中、继续占用 HNSW 索引，而 API 侧连"库不存在"
    都查不到（库已软删），表现为"检索到了没有归属的内容"。

    顺序与文档删除一致：先把可见性切断（`deleted_at` + `active_build_id = NULL`），
    再入队异步清理。反过来会让窗口期内的检索命中已删文档。
    """
    document_ids = (
        (
            await session.execute(
                text(
                    "SELECT document_id FROM documents "
                    "WHERE workspace_id = :ws AND kb_id = :kb AND deleted_at IS NULL "
                    "FOR UPDATE"
                ),
                {"ws": scope.workspace_id, "kb": kb_id},
            )
        )
        .scalars()
        .all()
    )
    if not document_ids:
        return 0

    now = datetime.now(UTC)
    await session.execute(
        text(
            "UPDATE documents SET deleted_at = :now, active_build_id = NULL, "
            "generation = generation + 1, row_version = row_version + 1 "
            "WHERE workspace_id = :ws AND kb_id = :kb AND deleted_at IS NULL"
        ),
        {"now": now, "ws": scope.workspace_id, "kb": kb_id},
    )
    for document_id in document_ids:
        # 不复用"每篇先查一次已有 purge 作业"：`queue.enqueue` 默认幂等键含 uuid4，
        # 必然新建。重复入队是安全的——purge 是幂等的（删不存在的行返回 0），
        # 而漏入队会让 blob 字节永久留在库里。
        await queue.enqueue(
            session,
            scope=scope,
            document_id=document_id,
            job_type="purge",
            payload={
                "document_id": str(document_id),
                "principal_id": str(scope.principal_id),
            },
        )
    return len(document_ids)
