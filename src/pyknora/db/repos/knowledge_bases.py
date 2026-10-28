"""知识库持久化；所有读写都把可信 Scope 放进 SQL 谓词。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.scope import Scope
from pyknora.models.tables import KnowledgeBaseRow
from pyknora.schemas.kb import EmbeddingProfile, KnowledgeBase


class SqlKnowledgeBaseRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        scope: Scope,
        *,
        name: str,
        profile: EmbeddingProfile,
    ) -> KnowledgeBase:
        row = KnowledgeBaseRow(
            kb_id=uuid4(),
            workspace_id=scope.workspace_id,
            name=name,
            embedding_model_key=profile.embedding_model_key,
            embedding_dim=profile.embedding_dim,
            chunk_size=profile.chunk_size,
            chunk_overlap=profile.chunk_overlap,
            normalize=profile.normalize,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _to_schema(row)

    async def get(self, scope: Scope, kb_id: UUID) -> KnowledgeBase | None:
        result = await self._session.execute(
            select(KnowledgeBaseRow).where(
                KnowledgeBaseRow.workspace_id == scope.workspace_id,
                KnowledgeBaseRow.kb_id == kb_id,
                KnowledgeBaseRow.deleted_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        return _to_schema(row) if row is not None else None

    async def get_embedding_profile(self, scope: Scope, kb_id: UUID) -> EmbeddingProfile | None:
        kb = await self.get(scope, kb_id)
        return kb.profile if kb is not None else None

    async def rename(
        self,
        scope: Scope,
        kb_id: UUID,
        *,
        name: str,
        expected_version: int,
    ) -> KnowledgeBase | None:
        result = await self._session.execute(
            update(KnowledgeBaseRow)
            .where(
                KnowledgeBaseRow.workspace_id == scope.workspace_id,
                KnowledgeBaseRow.kb_id == kb_id,
                KnowledgeBaseRow.deleted_at.is_(None),
                KnowledgeBaseRow.row_version == expected_version,
            )
            .values(
                name=name,
                row_version=KnowledgeBaseRow.row_version + 1,
                updated_at=datetime.now(UTC),
            )
            .returning(KnowledgeBaseRow)
        )
        row = result.scalar_one_or_none()
        return _to_schema(row) if row is not None else None

    async def soft_delete(
        self, scope: Scope, kb_id: UUID, *, expected_version: int
    ) -> KnowledgeBase | None:
        now = datetime.now(UTC)
        result = await self._session.execute(
            update(KnowledgeBaseRow)
            .where(
                KnowledgeBaseRow.workspace_id == scope.workspace_id,
                KnowledgeBaseRow.kb_id == kb_id,
                KnowledgeBaseRow.deleted_at.is_(None),
                KnowledgeBaseRow.row_version == expected_version,
            )
            .values(
                deleted_at=now,
                generation=KnowledgeBaseRow.generation + 1,
                row_version=KnowledgeBaseRow.row_version + 1,
                updated_at=now,
            )
            .returning(KnowledgeBaseRow)
        )
        row = result.scalar_one_or_none()
        return _to_schema(row) if row is not None else None


def _to_schema(row: KnowledgeBaseRow) -> KnowledgeBase:
    return KnowledgeBase(
        kb_id=row.kb_id,
        workspace_id=row.workspace_id,
        name=row.name,
        profile=EmbeddingProfile(
            embedding_model_key=row.embedding_model_key,
            embedding_dim=row.embedding_dim,
            chunk_size=row.chunk_size,
            chunk_overlap=row.chunk_overlap,
            normalize=row.normalize,
        ),
        active_index_build_id=row.active_index_build_id,
        row_version=row.row_version,
        generation=row.generation,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
