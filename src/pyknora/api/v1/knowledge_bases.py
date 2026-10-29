"""成员1的知识库 CRUD；身份与工作区只来自可信 Scope。"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.api.deps import get_kb_repo, get_scope, get_session
from pyknora.core.errors import AppError, ErrorCode, not_found
from pyknora.core.scope import Scope
from pyknora.schemas.kb import EmbeddingProfile, KnowledgeBase, KnowledgeBasePage
from pyknora.services.kb.cursor import decode_cursor, encode_cursor
from pyknora.services.kb.service import (
    create_knowledge_base,
    delete_knowledge_base,
    rename_knowledge_base,
)

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


class KnowledgeBaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    embedding_model_key: str = Field(min_length=1, max_length=500)
    embedding_dim: int = Field(gt=0)
    chunk_size: int = Field(gt=0)
    chunk_overlap: int = Field(ge=0)
    normalize: bool = True


class KnowledgeBasePatch(BaseModel):
    """S05 只开放改名；配置字段由 S06 明确固化。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class KnowledgeBaseDeleted(BaseModel):
    model_config = ConfigDict(frozen=True)

    kb_id: UUID
    status: str
    generation: int
    row_version: int


def _expected_version(if_match: str | None) -> int:
    if if_match is None:
        raise AppError(ErrorCode.REVISION_MISSING, "If-Match header is required")
    value = if_match.strip()
    if value.startswith("W/"):
        value = value[2:].strip()
    value = value.strip('"')
    try:
        version = int(value)
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, "If-Match must contain a row version") from exc
    if version <= 0:
        raise AppError(ErrorCode.INVALID_REQUEST, "If-Match must contain a positive row version")
    return version


@router.post("", status_code=status.HTTP_201_CREATED, response_model=KnowledgeBase)
async def create(
    body: KnowledgeBaseCreate,
    scope: Annotated[Scope, Depends(get_scope)],
    repo: Annotated[Any, Depends(get_kb_repo)],
) -> KnowledgeBase:
    if body.chunk_overlap >= body.chunk_size:
        raise AppError(ErrorCode.INVALID_REQUEST, "chunk_overlap must be less than chunk_size")
    return await create_knowledge_base(
        repo,
        scope,
        name=body.name,
        profile=EmbeddingProfile(
            embedding_model_key=body.embedding_model_key,
            embedding_dim=body.embedding_dim,
            chunk_size=body.chunk_size,
            chunk_overlap=body.chunk_overlap,
            normalize=body.normalize,
        ),
    )


@router.get("", response_model=KnowledgeBasePage)
async def list_knowledge_bases(
    scope: Annotated[Scope, Depends(get_scope)],
    repo: Annotated[Any, Depends(get_kb_repo)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> KnowledgeBasePage:
    position = decode_cursor(scope, cursor) if cursor else None
    items, has_more = await repo.list(
        scope,
        limit=limit,
        after_created_at=position.created_at if position else None,
        after_kb_id=position.kb_id if position else None,
    )
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(
            scope,
            created_at=last.created_at,
            kb_id=last.kb_id,
        )
    return KnowledgeBasePage(items=list(items), next_cursor=next_cursor, limit=limit)


@router.get("/{kb_id}", response_model=KnowledgeBase)
async def get_one(
    kb_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    repo: Annotated[Any, Depends(get_kb_repo)],
) -> KnowledgeBase:
    kb = await repo.get(scope, kb_id)
    if kb is None:
        raise not_found("knowledge base")
    return kb


@router.patch("/{kb_id}", response_model=KnowledgeBase)
async def patch(
    kb_id: UUID,
    body: KnowledgeBasePatch,
    scope: Annotated[Scope, Depends(get_scope)],
    repo: Annotated[Any, Depends(get_kb_repo)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> KnowledgeBase:
    return await rename_knowledge_base(
        repo,
        scope,
        kb_id,
        name=body.name,
        expected_version=_expected_version(if_match),
    )


@router.delete(
    "/{kb_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=KnowledgeBaseDeleted,
)
async def remove(
    kb_id: UUID,
    response: Response,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[Any, Depends(get_kb_repo)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> KnowledgeBaseDeleted:
    deleted = await delete_knowledge_base(
        session,
        repo,
        scope,
        kb_id,
        expected_version=_expected_version(if_match),
    )
    response.headers["ETag"] = f'"{deleted.row_version}"'
    return KnowledgeBaseDeleted(
        kb_id=deleted.kb_id,
        status="purging",
        generation=deleted.generation,
        row_version=deleted.row_version,
    )
