"""成员2 的 HTTP 交付面：文档上传、查询、切片列表、删除。

四个端点的共同点：**全部先过 `get_scope`**。它不是装饰性的依赖——`Scope` 是这些
SQL 里 `workspace_id` 谓词的唯一来源，路由里没有任何一处从请求体或查询串读身份。

响应码的选择有讲究，不是为了好看：

- 上传返回 **202**（不是 201）：这一刻只有"作业已入队"，文档还不可检索。
  回 201 会让前端以为可以立刻查了。
- 删除也返回 **202**：`deleted_at` 已经挡住可见性，但 chunk 还在，purge 是异步的。
- 上传去重（hash 相同）返回 **200** 且 `deduped=true`：没有新作业，什么都没变。
  如果这里也回 202，前端会去轮询一个不存在的 job。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.api.deps import (
    get_build_repo,
    get_chunk_repo,
    get_document_repo,
    get_kb_repo,
    get_scope,
    get_session,
    require_kb_access,
    require_permission,
)
from pyknora.core.errors import AppError, ErrorCode, not_found
from pyknora.core.scope import Scope
from pyknora.core.settings import settings
from pyknora.db.repos.reads import SqlChunkRepo, SqlDocumentRepo, SqlIndexBuildRepo
from pyknora.models.tables import DEFAULT_AUTHORITY
from pyknora.services.auth.permissions import (
    CHUNK_READ,
    DOCUMENT_DELETE,
    DOCUMENT_READ,
    DOCUMENT_WRITE,
)
from pyknora.services.ingest.documents import delete_document, upload_document
from pyknora.services.ingest.parser import SUPPORTED_SUFFIXES

logger = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])

_UPLOAD_CHUNK_BYTES = 1 << 20


async def _read_bounded(file: UploadFile, limit: int) -> bytes:
    """分块读上传体，一超过 `limit` 立刻中断。

    直接 `await file.read()` 再比大小的话，一个 10GB 的请求会先把内存吃光；而
    `UploadFile.size` 只反映 multipart 头里声明的长度，客户端可以不声明或谎报。
    所以两道闸都要：声明长度先挡一次，实际读取再按块封顶。
    """
    declared = file.size
    if declared is not None and declared > limit:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "file too large",
            details={"max_bytes": limit, "declared_bytes": declared},
        )

    buffer = bytearray()
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "file too large",
                details={"max_bytes": limit, "at_least_bytes": len(buffer)},
            )
    return bytes(buffer)


class DocumentAccepted(BaseModel):
    """上传/删除的受理回执。"""

    model_config = ConfigDict(frozen=True)

    document_id: UUID
    deduped: bool
    job_id: UUID | None = None
    document_version_id: UUID | None = None
    status: str


class DocumentDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: UUID
    workspace_id: UUID
    kb_id: UUID
    # 只读派生字段（不落库）：由 active_document_version 的 object_key 末段解析。
    # None 表示还没有版本，或历史行的对象键里不含文件名 —— 前端回落显示短 document_id。
    filename: str | None = None
    active_document_version_id: UUID | None = None
    active_build_id: UUID | None = None
    active_index_version: int | None = None
    generation: int
    row_version: int
    # 显式给前端一个"现在能不能搜到"的布尔，而不是让它去推断
    # `active_build_id is not None`——那个推断迟早有人写错。
    searchable: bool


class ChunkPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[dict[str, Any]]
    limit: int
    offset: int
    total: int


class DocumentSummary(BaseModel):
    """文档列表里的一行。是 `DocumentDetail` 的真子集，指针字段一一对应。"""

    model_config = ConfigDict(frozen=True)

    document_id: UUID
    kb_id: UUID
    # 同 DocumentDetail.filename：只读派生，来源是活动版本的 object_key 末段。
    filename: str | None = None
    active_document_version_id: UUID | None
    active_build_id: UUID | None
    active_index_version: int | None
    generation: int
    row_version: int
    # 判据与详情页**必须一致**（build.state == "active"），否则同一篇文档在两个
    # 接口里一个说可搜、一个说不可搜，前端无从判断该信哪个。
    searchable: bool
    created_at: datetime


class DocumentPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[DocumentSummary]
    limit: int
    offset: int
    total: int


def _suffix_of(filename: str) -> str:
    idx = filename.rfind(".")
    return filename[idx:].lower() if idx >= 0 else ""


@router.post(
    "/knowledge-bases/{kb_id}/documents",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DocumentAccepted,
    summary="上传文档（hash 三分支）",
    dependencies=[Depends(require_permission(DOCUMENT_WRITE))],
)
async def upload(
    kb_id: UUID,
    response: Response,
    file: Annotated[UploadFile, File()],
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
    kb_repo: Annotated[Any, Depends(get_kb_repo)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    # 有效期与权威度（migrations/0016）：两个时间都可空，语义见 ingest.documents。
    # 用表单字段而不是 JSON：这个端点是 multipart，没有可复用的 body 模型。
    effective_from: Annotated[datetime | None, Form()] = None,
    effective_until: Annotated[datetime | None, Form()] = None,
    authority: Annotated[int | None, Form(ge=0, le=32767)] = None,
) -> DocumentAccepted:
    if kb_repo is None:
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "knowledge base repo not configured")

    filename = file.filename or "unnamed"
    suffix = _suffix_of(filename)
    if suffix not in SUPPORTED_SUFFIXES:
        raise AppError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"unsupported file type: {suffix or '(none)'}",
            details={"supported": sorted(SUPPORTED_SUFFIXES)},
        )

    data = await _read_bounded(file, settings.max_upload_bytes)

    result = await upload_document(
        session,
        scope=scope,
        kb_repo=kb_repo,
        kb_id=kb_id,
        filename=filename,
        data=data,
        content_type=file.content_type,
        principal_id=scope.principal_id,
        idempotency_key=idempotency_key,
        effective_from=effective_from,
        effective_until=effective_until,
        authority=authority if authority is not None else DEFAULT_AUTHORITY,
    )
    await session.commit()

    if result.deduped:
        # 没有新作业，改成 200：让前端知道"不用等"。
        response.status_code = status.HTTP_200_OK

    return DocumentAccepted(
        document_id=result.document_id,
        deduped=result.deduped,
        job_id=result.job_id,
        document_version_id=result.document_version_id,
        status=result.status,
    )


@router.get(
    "/knowledge-bases/{kb_id}/documents",
    response_model=DocumentPage,
    summary="知识库下的文档列表",
    dependencies=[Depends(require_permission(DOCUMENT_READ))],
)
async def list_documents(
    kb_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    doc_repo: Annotated[SqlDocumentRepo, Depends(get_document_repo)],
    build_repo: Annotated[SqlIndexBuildRepo, Depends(get_build_repo)],
    _kb: Annotated[Any, Depends(require_kb_access)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentPage:
    """列出一个知识库里仍在册的文档。

    没有这个接口时，客户端只能靠上传时记下的 `document_id` 反查详情——重启一次前端
    就再也找不回自己传过什么，`document_id` 无法枚举等于文档列表不可用。

    `total` 是**全量计数**，不是本页条数：列表要能显示分页器。切片列表那边
    `ChunkPage.total` 目前是页内条数（历史口径），两者语义不同，别互相套用。
    """
    docs = await doc_repo.list_for_kb(scope, kb_id, limit=limit, offset=offset)
    # 一次批量取构建状态，避免每行一次查询（列表页最多 500 行）。
    builds = await build_repo.get_many(
        scope, [doc.active_build_id for doc in docs if doc.active_build_id is not None]
    )

    items: list[DocumentSummary] = []
    for doc in docs:
        build = builds.get(doc.active_build_id) if doc.active_build_id is not None else None
        items.append(
            DocumentSummary(
                document_id=doc.document_id,
                kb_id=doc.kb_id,
                filename=doc.filename,
                active_document_version_id=doc.active_document_version_id,
                active_build_id=doc.active_build_id,
                active_index_version=doc.active_index_version,
                generation=doc.generation,
                row_version=doc.row_version,
                searchable=build is not None and build.state == "active",
                created_at=doc.created_at,
            )
        )

    total = await doc_repo.count_for_kb(scope, kb_id)
    return DocumentPage(items=items, limit=limit, offset=offset, total=total)


@router.get(
    "/documents/{document_id}",
    response_model=DocumentDetail,
    summary="文档详情（含当前活动索引指针）",
    dependencies=[Depends(require_permission(DOCUMENT_READ))],
)
async def get_document(
    document_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    doc_repo: Annotated[SqlDocumentRepo, Depends(get_document_repo)],
    build_repo: Annotated[SqlIndexBuildRepo, Depends(get_build_repo)],
) -> DocumentDetail:
    doc = await doc_repo.get_active(scope, document_id)
    if doc is None:
        # 跨区与不存在同为 404，不让状态码成为存在性探针。
        raise not_found("document")

    build_state = None
    if doc.active_build_id is not None:
        build = await build_repo.get(scope, doc.active_build_id)
        build_state = build.state if build else None

    return DocumentDetail(
        document_id=doc.document_id,
        workspace_id=doc.workspace_id,
        kb_id=doc.kb_id,
        filename=doc.filename,
        active_document_version_id=doc.active_document_version_id,
        active_build_id=doc.active_build_id,
        active_index_version=doc.active_index_version,
        generation=doc.generation,
        row_version=doc.row_version,
        searchable=build_state == "active",
    )


@router.get(
    "/documents/{document_id}/chunks",
    response_model=ChunkPage,
    summary="当前活动构建的切片列表",
    dependencies=[Depends(require_permission(CHUNK_READ))],
)
async def list_chunks(
    document_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    doc_repo: Annotated[SqlDocumentRepo, Depends(get_document_repo)],
    chunk_repo: Annotated[SqlChunkRepo, Depends(get_chunk_repo)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ChunkPage:
    # 先确认文档存在且在本 scope 内，否则"文档不存在"与"文档没有切片"
    # 都会返回空列表，调用方无法区分。
    doc = await doc_repo.get_active(scope, document_id)
    if doc is None:
        raise not_found("document")

    chunks = await chunk_repo.list_active_chunks(scope, document_id, limit=limit, offset=offset)
    return ChunkPage(
        items=[c.model_dump(mode="json") for c in chunks],
        limit=limit,
        offset=offset,
        total=len(chunks),
    )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DocumentAccepted,
    summary="删除文档（先阻断可见性，再异步清理）",
    dependencies=[Depends(require_permission(DOCUMENT_DELETE))],
)
async def remove(
    document_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentAccepted:
    job_id = await delete_document(session, scope=scope, document_id=document_id)
    await session.commit()
    return DocumentAccepted(
        document_id=document_id,
        deduped=False,
        job_id=job_id,
        status="purging",
    )
