"""成员5 的 HTTP 交付面：来源追踪（任务8）。

只有一个端点，返回**版本链路 + 每条版本的创建者与来源**；不含正文，也不含
`object_key`（正文另走成员2 的切片接口）。

身份只来自 `get_scope`：跨区、不存在、已删除统一 404，不用 403 承认"这个 id 存在"。
`SqlDocumentRepo` / `SqlDocumentVersionRepo` 在这里就地构造，不改 `api/deps.py`
（那是成员4 的权限唯一入口）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.api.deps import get_scope, get_session
from pyknora.core.errors import not_found
from pyknora.core.scope import Scope
from pyknora.db.repos.lineage import SqlDocumentVersionRepo
from pyknora.db.repos.reads import SqlDocumentRepo
from pyknora.schemas.lineage import DocumentLineage
from pyknora.services.lineage.service import build_lineage

router = APIRouter(tags=["lineage"])


@router.get(
    "/documents/{document_id}/lineage",
    response_model=DocumentLineage,
    summary="文档版本链路（含每版创建者与来源）",
)
async def get_document_lineage(
    document_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentLineage:
    """先确认文档在 scope 内，再读它的版本快照。

    `active_version_id` 取自 `documents.active_document_version_id`，而不是
    "最新那一版"——指针由发布事务切换，最新写入的版本可能还没发布。
    """
    document = await SqlDocumentRepo(session).get_active(scope, document_id)
    if document is None:
        raise not_found("document")

    versions = await SqlDocumentVersionRepo(session).list_versions(scope, document_id)
    return build_lineage(
        document_id=document_id,
        versions=versions,
        active_version_id=document.active_document_version_id,
    )
