"""成员5：文档版本链路的只读实现（C2 的 `DocumentVersionRepo`）。

单独一个文件，而不是追加到 `db/repos/reads.py`：那个文件归成员2（AGENTS §5），
成员5 不该往里面塞自己的实现，否则每次合并都要动同一处。

只按 `workspace_id` + `document_id` 过滤：跨区与不存在的文档都返回空序列，
与 C2 的 R2 一致——拿不到就是"没有"，不回落成"读到什么算什么"。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.scope import Scope
from pyknora.models.tables import DocumentVersionRow
from pyknora.schemas.lineage import DocumentVersion


class SqlDocumentVersionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_versions(self, scope: Scope, document_id: UUID) -> list[DocumentVersion]:
        stmt = (
            select(DocumentVersionRow)
            .where(
                DocumentVersionRow.workspace_id == scope.workspace_id,
                DocumentVersionRow.document_id == document_id,
            )
            .order_by(
                DocumentVersionRow.created_at,
                DocumentVersionRow.document_version_id,
            )
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            DocumentVersion(
                document_version_id=row.document_version_id,
                document_id=row.document_id,
                content_hash=row.content_hash,
                byte_size=row.byte_size,
                parse_status=row.parse_status,
                created_by_principal_id=row.created_by_principal_id,
                ingestion_source=row.ingestion_source,
                created_at=row.created_at,
            )
            for row in rows
        ]
