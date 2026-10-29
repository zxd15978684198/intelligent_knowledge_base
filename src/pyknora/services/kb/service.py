"""知识库写入语义：唯一性、乐观锁和软删除事件。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.errors import AppError, ErrorCode, not_found
from pyknora.core.scope import Scope
from pyknora.schemas.kb import EmbeddingProfile, KnowledgeBase


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
