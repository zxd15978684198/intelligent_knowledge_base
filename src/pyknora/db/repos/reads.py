"""C2 的真实实现：全部走 v_active_chunks 与 documents 指针。

为什么用视图而不是在 Python 里拼 WHERE：安全性来自视图内的
`WHERE d.active_build_id = b.build_id` 一行。调用方忘了加条件也读不到旧 build。
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.models.tables import DocumentRow, IndexBuildRow
from pyknora.schemas.document import ActiveChunk, ActiveDocument, IndexBuild, ScoredChunk
from pyknora.services.ingest.embedder import EmbeddingDimensionError

# 按文档列 chunk 时取的列。不含 embedding/tsvector：见 ActiveChunk 的 docstring。
_CHUNK_COLUMNS = """
    chunk_id, workspace_id, kb_id, document_id, build_id, index_version,
    attempt_id, chunk_ordinal, content, page_no, heading_path,
    char_start, char_end, token_count, locator_ref,
    embedding_model_key, embedding_dim, chunk_size,
    active_document_version_id
"""


class SqlDocumentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(self, scope: Scope, document_id: UUID) -> ActiveDocument | None:
        stmt = select(DocumentRow).where(
            DocumentRow.workspace_id == scope.workspace_id,
            DocumentRow.document_id == document_id,
            DocumentRow.deleted_at.is_(None),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return ActiveDocument(
            document_id=row.document_id,
            workspace_id=row.workspace_id,
            kb_id=row.kb_id,
            active_document_version_id=row.active_document_version_id,
            active_build_id=row.active_build_id,
            active_index_version=row.active_index_version,
            generation=row.generation,
            row_version=row.row_version,
            created_at=row.created_at,
        )


class SqlChunkRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active_chunks(
        self, scope: Scope, document_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[ActiveChunk]:
        # 视图已经强制 active build，这里只叠 workspace 与 document 两个谓词
        stmt = text(
            f"""
            SELECT {_CHUNK_COLUMNS}
            FROM v_active_chunks
            WHERE workspace_id = :workspace_id AND document_id = :document_id
            ORDER BY chunk_ordinal
            LIMIT :limit OFFSET :offset
            """
        )
        result = await self._session.execute(
            stmt,
            {
                "workspace_id": scope.workspace_id,
                "document_id": document_id,
                "limit": limit,
                "offset": offset,
            },
        )
        return [_to_active_chunk(r) for r in result.mappings()]


class SqlChunkSearchRepo:
    """余弦距离 top-k。成员3 的检索入口，也是维度校验的唯一发生地。

    两个 PG 细节决定了这里的写法：

    1. `embedding <=> :vec` 要求两侧维度相同，不符时 PG 抛
       `different vector dimensions`，会被 FastAPI 转成 500。所以先查维度再查向量。
    2. 一个库里若并存多个 `embedding_dim`（换模型后没重建索引），任何 top-k
       都是错的——不同空间的向量不可比。这种情况直接拒，不返回部分结果。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search_similar(
        self,
        scope: Scope,
        kb_id: UUID,
        query_vector: Sequence[float],
        *,
        limit: int = 10,
        document_id: UUID | None = None,
    ) -> list[ScoredChunk]:
        dims = await self._kb_dims(scope, kb_id)
        if not dims:
            # 空库：没有可比的东西。返回空而不是抛错，否则新库首次检索就 500。
            return []
        if len(dims) > 1:
            raise AppError(
                ErrorCode.INDEX_INCOMPLETE,
                "knowledge base holds vectors from more than one embedding space; reindex required",
                details={"dimensions": sorted(dims)},
            )
        actual_dim = next(iter(dims))
        if len(query_vector) != actual_dim:
            raise EmbeddingDimensionError(actual_dim, len(query_vector), "query_vector")

        params: dict[str, object] = {
            "workspace_id": scope.workspace_id,
            "kb_id": kb_id,
            "vec": _to_pg_vector(query_vector),
            "limit": limit,
        }
        doc_clause = ""
        if document_id is not None:
            doc_clause = "AND document_id = :document_id"
            params["document_id"] = document_id

        stmt = text(
            f"""
            SELECT {_CHUNK_COLUMNS},
                   embedding <=> CAST(:vec AS vector) AS distance
            FROM v_active_chunks
            WHERE workspace_id = :workspace_id
              AND kb_id = :kb_id
              {doc_clause}
            ORDER BY embedding <=> CAST(:vec AS vector)
            LIMIT :limit
            """
        )
        result = await self._session.execute(stmt, params)
        return [_to_scored_chunk(r) for r in result.mappings()]

    async def _kb_dims(self, scope: Scope, kb_id: UUID) -> set[int]:
        """本 scope 内该库现存活动向量用到的维度集合。"""
        stmt = text(
            """
            SELECT DISTINCT embedding_dim
            FROM v_active_chunks
            WHERE workspace_id = :workspace_id AND kb_id = :kb_id
            """
        )
        result = await self._session.execute(
            stmt, {"workspace_id": scope.workspace_id, "kb_id": kb_id}
        )
        return {row.embedding_dim for row in result}


class SqlIndexBuildRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, scope: Scope, build_id: UUID) -> IndexBuild | None:
        stmt = select(IndexBuildRow).where(
            IndexBuildRow.workspace_id == scope.workspace_id,
            IndexBuildRow.build_id == build_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return IndexBuild(
            build_id=row.build_id,
            workspace_id=row.workspace_id,
            document_id=row.document_id,
            index_version=row.index_version,
            attempt_id=row.attempt_id,
            embedding_model_key=row.embedding_model_key,
            embedding_dim=row.embedding_dim,
            chunk_size=row.chunk_size,
            chunk_overlap=row.chunk_overlap,
            normalize=row.normalize,
            chunk_count=row.chunk_count,
            state=row.state,
            created_at=row.created_at,
            activated_at=row.activated_at,
            retired_at=row.retired_at,
        )


class FakeKbRepo:
    """成员1 尚未交付时的替身。

    数据是假的，schema 是真的：返回 `EmbeddingProfile`，与成员1 共用同一个模型。
    替换成真实实现时，改动应当只有注册那一行。

    **scope 必须真的生效。** 第一版这里只有 `return self._profiles.get(kb_id)`——
    scope 参数收下就丢掉，于是 B 区能用 A 区的 kb_id 拿到 profile。后果不是报错，
    而是"跨区测试永远通过"：拿替身写的越权测试会给出错误的安全感。
    现在 profile 按 workspace 分开存，跨区一律返回 None。
    """

    def __init__(self, profiles: dict[tuple[UUID, UUID], object] | None = None) -> None:
        self._profiles: dict[tuple[UUID, UUID], object] = profiles or {}

    def add(self, scope: Scope, kb_id: UUID, profile: object) -> None:
        """按 (workspace_id, kb_id) 登记。签名故意要求传 Scope，逼调用方想清归属。"""
        self._profiles[(scope.workspace_id, kb_id)] = profile

    async def get_embedding_profile(self, scope: Scope, kb_id: UUID) -> object | None:
        return self._profiles.get((scope.workspace_id, kb_id))


def _to_active_chunk(row) -> ActiveChunk:
    return ActiveChunk(
        chunk_id=row.chunk_id,
        workspace_id=row.workspace_id,
        kb_id=row.kb_id,
        document_id=row.document_id,
        build_id=row.build_id,
        index_version=row.index_version,
        attempt_id=row.attempt_id,
        chunk_ordinal=row.chunk_ordinal,
        content=row.content,
        page_no=row.page_no,
        heading_path=row.heading_path,
        char_start=row.char_start,
        char_end=row.char_end,
        token_count=row.token_count,
        locator_ref=row.locator_ref,
        embedding_model_key=row.embedding_model_key,
        embedding_dim=row.embedding_dim,
        chunk_size=row.chunk_size,
        active_document_version_id=row.active_document_version_id,
    )


def _to_scored_chunk(row) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=row.chunk_id,
        workspace_id=row.workspace_id,
        kb_id=row.kb_id,
        document_id=row.document_id,
        build_id=row.build_id,
        index_version=row.index_version,
        attempt_id=row.attempt_id,
        chunk_ordinal=row.chunk_ordinal,
        content=row.content,
        page_no=row.page_no,
        heading_path=row.heading_path,
        locator_ref=row.locator_ref,
        embedding_model_key=row.embedding_model_key,
        embedding_dim=row.embedding_dim,
        active_document_version_id=row.active_document_version_id,
        distance=float(row.distance),
    )


def _to_pg_vector(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"
