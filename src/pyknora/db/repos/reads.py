"""C2 的真实实现：全部走 v_active_chunks 与 documents 指针。

为什么用视图而不是在 Python 里拼 WHERE：安全性来自视图内的
`WHERE d.active_build_id = b.build_id` 一行。调用方忘了加条件也读不到旧 build。
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.models.tables import DocumentRow, DocumentVersionRow, IndexBuildRow
from pyknora.schemas.document import (
    ActiveChunk,
    ActiveDocument,
    DocumentVersionInfo,
    IndexBuild,
    ScoredChunk,
    filename_from_object_key,
)
from pyknora.services.ingest.embedder import EmbeddingDimensionError

# 按文档列 chunk 时取的列。不含 embedding/tsvector：见 ActiveChunk 的 docstring。
_CHUNK_COLUMNS = """
    chunk_id, workspace_id, kb_id, document_id, build_id, index_version,
    attempt_id, chunk_ordinal, content, page_no, page_end_no, heading_path,
    char_start, char_end, token_count, locator_ref,
    -- 带上 locator：结构化位置（页码/工作表+单元格/幻灯片/坐标框），供引用渲染回指
    locator,
    embedding_model_key, embedding_dim, chunk_size,
    active_document_version_id
"""

# 历史 build 的列清单。**不能复用 _CHUNK_COLUMNS**：那张是给 v_active_chunks 用的，
# 视图里的 kb_id / embedding_model_key / embedding_dim / chunk_size /
# active_document_version_id 都是 join 出来的，基表 chunks 上并没有这些列
# （见 migrations/0001_init.sql:100-124）。
_CHUNK_COLUMNS_BY_BUILD = """
    c.chunk_id, c.workspace_id, d.kb_id, c.document_id, c.build_id,
    c.index_version, c.attempt_id, c.chunk_ordinal, c.content,
    c.page_no, c.page_end_no, c.heading_path, c.char_start, c.char_end,
    c.token_count, c.locator_ref,
    -- 历史 build 也要带 locator：引用回指与活动 build 保持同一形状
    c.locator,
    b.embedding_model_key, b.embedding_dim, b.chunk_size,
    d.active_document_version_id
"""

#: 关键词检索允许的最大查询字符数。
#:
#: 中文按字符数≈词元数（1 个汉字对应约 1 个二元组），而 `to_tsquery` 是递归下降解析：
#: 实测在 pyknora_cjk_bigram 口径下 1000 字 → 999 词元/13.6ms，5000 字 → 4999 词元/200ms，
#: **20000 字直接 `stack depth limit exceeded`**（SQL 报错 → API 500）。
#: 旧口径（`plainto_tsquery('simple', content)`）把整段连续汉字当 1 个词元，所以同样的
#: 2 万字输入不会炸；引入 bigram 后词元数随字符数线性增长，必须在这一层封顶，
#: 否则任何调用方（不只是 HTTP）都能用一条超长查询把请求打成 500。
#:
#: 256 字对真实问句足够宽松，且最坏只有 ~255 个词元（parse 约 2.3ms），比崩溃边界低两个数量级。
#: 超长时**截前缀**而不是报错：关键词路只是召回通道之一，向量路仍然拿到完整问题。
_MAX_KEYWORD_QUERY_CHARS = 256

_KEYWORD_STMT_CACHE: dict[str, TextClause] = {}


def _keyword_stmt(join_op: str) -> TextClause:
    """按 and/or 两种 tsquery 组合方式各编译一次 SQL。

    `join_op` 会进 SQL 文本，所以只接受白名单值（不允许把调用方字符串直接拼进去）。
    缓存是因为同一进程里 and/or 两条语句是固定的，不必每次重新构造 TextClause。
    """
    if join_op not in ("and", "or"):
        raise ValueError(f"join_op 只接受 'and' 或 'or'，收到 {join_op!r}")
    cached = _KEYWORD_STMT_CACHE.get(join_op)
    if cached is not None:
        return cached
    stmt = text(
        f"""
        WITH query AS (
            SELECT to_tsquery('simple', pyknora_cjk_tsquery(:query_text, '{join_op}')) AS terms
        )
        SELECT {_CHUNK_COLUMNS}
        FROM v_active_chunks AS c
        CROSS JOIN query AS q
        WHERE c.workspace_id = :workspace_id
          AND c.kb_id = :kb_id
          AND c.tsvector @@ q.terms
        ORDER BY ts_rank_cd(c.tsvector, q.terms) DESC, c.chunk_id ASC
        LIMIT :limit
        """
    )
    _KEYWORD_STMT_CACHE[join_op] = stmt
    return stmt


def _active_document(row: DocumentRow, object_key: str | None = None) -> ActiveDocument:
    """把 `documents` 行映射成只读快照。

    单独抽出来是因为"详情"和"列表"必须给出**同一个形状**：两处各写一遍的话，
    迟早有一处漏掉新加的指针列，前端就会看到有的接口有、有的接口没有。

    `object_key` 来自 `active_document_version_id` 指向的那一版，只用来派生
    `filename`（前端只读展示）。它是可选参数：调用方没有 join 版本表时传 None，
    `filename` 就是 None —— 而不是编一个假名字。
    """
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
        filename=filename_from_object_key(object_key),
    )


def _version_key_join() -> tuple:
    """`documents` 左连它"当前该显示的那一版"的 join 条件。

    指针取 `coalesce(desired, active)`，不是只看 `active`：
    上传只写 `desired_document_version_id`（202 Accepted，真正的
    `active_document_version_id` 要等 Worker 发布后才写）。只看 active 的话，
    "刚上传完、还在排队"的文档在列表和详情里都会变成 `filename=None`，
    前端只能显示一串短 id —— 而文件名在**上传那一刻就已确定**，没有理由等发布。

    `desired` 为空（已发布且指针一致、或指针被 purge 置空）时退回 `active`；
    两者都为空就 join 不到行，LEFT OUTER 保证文档仍出现在列表里（`filename=None`），
    不会因为缺版本行就被 INNER JOIN 吃掉。
    """
    return (
        DocumentVersionRow,
        and_(
            DocumentVersionRow.workspace_id == DocumentRow.workspace_id,
            DocumentVersionRow.document_version_id
            == func.coalesce(
                DocumentRow.desired_document_version_id,
                DocumentRow.active_document_version_id,
            ),
        ),
    )


class SqlDocumentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(self, scope: Scope, document_id: UUID) -> ActiveDocument | None:
        stmt = (
            select(DocumentRow, DocumentVersionRow.object_key)
            .outerjoin(*_version_key_join())
            .where(
                DocumentRow.workspace_id == scope.workspace_id,
                DocumentRow.document_id == document_id,
                DocumentRow.deleted_at.is_(None),
            )
        )
        result = (await self._session.execute(stmt)).first()
        if result is None:
            return None
        return _active_document(result[0], result[1])

    async def list_for_kb(
        self, scope: Scope, kb_id: UUID, *, limit: int = 50, offset: int = 0
    ) -> list[ActiveDocument]:
        """知识库下的文档列表（不含已软删的）。

        排序带 `document_id` 兜底：`created_at` 是 `now()`，同一事务里连续插入的几篇
        会拿到**完全相同**的时间戳，只按时间排的话翻页会出现重复/漏行。
        """
        stmt = (
            select(DocumentRow, DocumentVersionRow.object_key)
            .outerjoin(*_version_key_join())
            .where(
                DocumentRow.workspace_id == scope.workspace_id,
                DocumentRow.kb_id == kb_id,
                DocumentRow.deleted_at.is_(None),
            )
            .order_by(DocumentRow.created_at.desc(), DocumentRow.document_id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).all()
        return [_active_document(row[0], row[1]) for row in rows]

    async def count_for_kb(self, scope: Scope, kb_id: UUID) -> int:
        """知识库下仍在册的文档数，供列表接口回 `total`。"""
        stmt = (
            select(func.count())
            .select_from(DocumentRow)
            .where(
                DocumentRow.workspace_id == scope.workspace_id,
                DocumentRow.kb_id == kb_id,
                DocumentRow.deleted_at.is_(None),
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def get_version(self, scope: Scope, version_id: UUID) -> DocumentVersionInfo | None:
        """取不可变版本快照的元数据。

        跨区与不存在统一返回 None——不区分"无权"和"没有"，否则这个方法就成了
        存在性探针（错误码规范 I8 的同一条理由）。
        """
        stmt = select(DocumentVersionRow).where(
            DocumentVersionRow.workspace_id == scope.workspace_id,
            DocumentVersionRow.document_version_id == version_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return DocumentVersionInfo(
            document_version_id=row.document_version_id,
            workspace_id=row.workspace_id,
            document_id=row.document_id,
            content_hash=row.content_hash,
            byte_size=row.byte_size,
            object_key=row.object_key,
            parse_status=row.parse_status,
            created_by_principal_id=row.created_by_principal_id,
            ingestion_source=row.ingestion_source,
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

    async def get_chunks(self, scope: Scope, chunk_ids: Sequence[UUID]) -> list[ActiveChunk]:
        """按 id 批量取**当前活动**版本的切片。

        成员3/5 用它做引用二次授权：模型吐出的 chunk_id 必须重新查一次活动视图，
        才知道它此刻还算不算数（文档可能已被替换或删除）。

        **失效的 id 直接缺席，不补 None、不抛错。** 调用方用"返回条数比请求少"
        判定引用失效；补占位会把"失效"伪装成"存在但内容为空"，引用校验就废了。
        """
        ids = list(dict.fromkeys(chunk_ids))  # 去重并保留入参顺序
        if not ids:
            return []
        params: dict[str, object] = {"workspace_id": scope.workspace_id}
        placeholders: list[str] = []
        for index, chunk_id in enumerate(ids):
            key = f"chunk_{index}"
            placeholders.append(f":{key}")
            params[key] = chunk_id
        in_clause = ", ".join(placeholders)
        stmt = text(
            f"""
            SELECT {_CHUNK_COLUMNS}
            FROM v_active_chunks
            WHERE workspace_id = :workspace_id
              AND chunk_id IN ({in_clause})
            ORDER BY document_id, chunk_ordinal
            """
        )
        result = await self._session.execute(stmt, params)
        return [_to_active_chunk(r) for r in result.mappings()]

    async def list_chunks_by_build(
        self, scope: Scope, build_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[ActiveChunk]:
        """读**历史** build 的切片——`v_active_chunks` 的受控例外（I1）。

        为什么必须开这个例外：视图只暴露 `documents.active_build_id` 指向的那一个
        build，而成员5 的引用追溯要看"当时的版本"，那个 build 早已 retired，
        视图里根本不存在，不开这个口就永远读不到历史。

        例外成立的前提是四重谓词，缺一不可：
        1. `c.workspace_id = :workspace_id` —— 跨区读不到（R3）。
        2. `JOIN index_builds ON (workspace_id, build_id)` —— 拿别的 workspace 的
           build_id 进来 join 不上，返回空而不是读到别人的切片。
        3. `JOIN documents ... AND d.deleted_at IS NULL` —— 文档删了就整体不可见，
           历史版本不能成为绕过删除的旁路。
        4. `JOIN knowledge_bases ... AND k.deleted_at IS NULL` —— 知识库软删同理。
           少了这条，删库之后再走历史追溯就能把切片读回来，与视图里的修复
           （`migrations/0012_v_active_chunks_skip_deleted_kb.sql`）自相矛盾；
           文档的删除早就这么做了，知识库没有理由更宽松。
        """
        stmt = text(
            f"""
            SELECT {_CHUNK_COLUMNS_BY_BUILD}
            FROM chunks AS c
            JOIN documents AS d
              ON d.workspace_id = c.workspace_id AND d.document_id = c.document_id
            JOIN index_builds AS b
              ON b.workspace_id = c.workspace_id AND b.build_id = c.build_id
            JOIN knowledge_bases AS k
              ON k.workspace_id = d.workspace_id AND k.kb_id = d.kb_id
            WHERE c.workspace_id = :workspace_id
              AND c.build_id = :build_id
              AND d.deleted_at IS NULL
              AND k.deleted_at IS NULL
            ORDER BY c.chunk_ordinal
            LIMIT :limit OFFSET :offset
            """
        )
        result = await self._session.execute(
            stmt,
            {
                "workspace_id": scope.workspace_id,
                "build_id": build_id,
                "limit": limit,
                "offset": offset,
            },
        )
        return [_to_active_chunk(r) for r in result.mappings()]


class SqlChunkSearchRepo:
    """向量与全文检索入口，也是向量维度校验的唯一发生地。

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

    async def search_keyword(
        self,
        scope: Scope,
        kb_id: UUID,
        query_text: str,
        *,
        limit: int = 10,
    ) -> list[ActiveChunk]:
        """按 PostgreSQL FTS 相关度取 top-k 活动 chunk（中文可用）。

        分词在数据库侧的 `pyknora_cjk_bigram` 里做（见 `migrations/0007_cjk_bigram_fts.sql`）：
        Worker 写 `tsvector` 时把连续汉字切成重叠二元组，这里用同一个函数把查询也切成
        二元组，两边词元口径一致，`@@` 才对中文有意义。

        取数分两级，先精度后召回：

        1. ``and``：要求命中查询里的**全部**二元组。注意这不是短语匹配——`&` 只保证
           每个词元都出现，不保证相邻；相邻性只通过 `ts_rank_cd` 的覆盖密度间接影响
           排序。专有名词、编号、错误码（"银狐"、"KB-001"）走这一级能拿到最准的结果。
        2. ``or``：只要求任一命中，靠 `ts_rank_cd` 排序。自然语言问句（"一线城市住宿
           报销上限是多少钱"）走 AND 会全落空，必须退到这一级。

        实测依据：文档"银狐锚点报销标准"对查询"银狐报销标准"（不连续）AND=false、
        OR=true；长问句同理。所以只做 AND 会漏掉一类正常提问，只做 OR 会丢掉短语精度。
        AND 结果先返回、OR 结果只用来补足到 ``limit``，同一 chunk 不重复出现。

        参数化 query 防止把用户文本当 SQL 执行；查询串里的 tsquery 元字符
        （``& | ! : * ( )``）在分词阶段被当分隔符丢弃，不构成 tsquery 语法注入。
        纯标点/空查询在 SQL 层得到 NULL 词元，`@@` 为 NULL，自然 0 行、不报错。

        查询超长时截到 ``_MAX_KEYWORD_QUERY_CHARS`` 字符再分词。这是必需的防御：
        中文二元组下词元数随字符数线性增长，2 万字的查询会让 `to_tsquery` 报
        `stack depth limit exceeded`（实测），把请求变成 500。截断只影响关键词这一路，
        向量路仍用完整问题，融合结果不受影响。
        """
        normalized_query = query_text.strip()
        if not normalized_query or limit <= 0:
            return []

        # 截前缀而非报错：见 _MAX_KEYWORD_QUERY_CHARS 的说明（超长会炸 to_tsquery）。
        body = normalized_query[:_MAX_KEYWORD_QUERY_CHARS]

        collected: dict[UUID, ActiveChunk] = {}
        for mode in ("and", "or"):
            result = await self._session.execute(
                _keyword_stmt(mode),
                {
                    "workspace_id": scope.workspace_id,
                    "kb_id": kb_id,
                    "query_text": body,
                    "limit": limit,
                },
            )
            for row in result.mappings():
                chunk = _to_active_chunk(row)
                collected.setdefault(chunk.chunk_id, chunk)
            if len(collected) >= limit:
                break
        return list(collected.values())[:limit]

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
        return _to_index_build(row)

    async def get_many(self, scope: Scope, build_ids: Sequence[UUID]) -> dict[UUID, IndexBuild]:
        """按 id 批量取构建记录（一次查询）。

        文档列表要每行给一个 `searchable`，逐个 `get` 就是 N+1；列表页最多 500 行，
        那一轮就是 500 次往返，还会把连接池排队时间算进接口延迟。
        """
        if not build_ids:
            return {}
        stmt = select(IndexBuildRow).where(
            IndexBuildRow.workspace_id == scope.workspace_id,
            IndexBuildRow.build_id.in_(list(build_ids)),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return {row.build_id: _to_index_build(row) for row in rows}

    async def list_builds(
        self, scope: Scope, document_id: UUID, *, limit: int = 50, offset: int = 0
    ) -> list[IndexBuild]:
        """一个文档的**全部**构建记录（staging/active/retired），最新的在前。

        成员5 的引用追溯要从"当前 active"往回翻历史，所以必须包含 retired；
        顺序给 created_at 倒序，正序会让每个调用方自己记得反转。
        """
        stmt = (
            select(IndexBuildRow)
            .where(
                IndexBuildRow.workspace_id == scope.workspace_id,
                IndexBuildRow.document_id == document_id,
            )
            .order_by(IndexBuildRow.created_at.desc(), IndexBuildRow.build_id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_index_build(row) for row in rows]


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
        from datetime import UTC, datetime

        from pyknora.schemas.kb import KnowledgeBase

        now = datetime.now(UTC)
        self._knowledge_bases: dict[tuple[UUID, UUID], KnowledgeBase] = {}
        for (workspace_id, kb_id), profile in (profiles or {}).items():
            self._knowledge_bases[(workspace_id, kb_id)] = KnowledgeBase(
                kb_id=kb_id,
                workspace_id=workspace_id,
                name=f"fake-{kb_id}",
                profile=profile,
                row_version=1,
                generation=1,
                created_at=now,
                updated_at=now,
            )

    def add(self, scope: Scope, kb_id: UUID, profile: object) -> None:
        """按 (workspace_id, kb_id) 登记。签名故意要求传 Scope，逼调用方想清归属。"""
        from datetime import UTC, datetime

        from pyknora.schemas.kb import KnowledgeBase

        now = datetime.now(UTC)
        self._knowledge_bases[(scope.workspace_id, kb_id)] = KnowledgeBase(
            kb_id=kb_id,
            workspace_id=scope.workspace_id,
            name=f"fake-{kb_id}",
            profile=profile,
            row_version=1,
            generation=1,
            created_at=now,
            updated_at=now,
        )

    async def get(self, scope: Scope, kb_id: UUID):
        return self._knowledge_bases.get((scope.workspace_id, kb_id))

    async def list(
        self,
        scope: Scope,
        *,
        limit: int,
        after_created_at=None,
        after_kb_id=None,
    ):
        rows = sorted(
            (
                kb
                for (workspace_id, _), kb in self._knowledge_bases.items()
                if workspace_id == scope.workspace_id and kb.deleted_at is None
            ),
            key=lambda kb: (kb.created_at, kb.kb_id),
        )
        if after_created_at is not None and after_kb_id is not None:
            rows = [
                kb for kb in rows if (kb.created_at, kb.kb_id) > (after_created_at, after_kb_id)
            ]
        return rows[:limit], len(rows) > limit

    async def get_embedding_profile(self, scope: Scope, kb_id: UUID) -> object | None:
        kb = await self.get(scope, kb_id)
        return kb.profile if kb is not None else None


def _to_index_build(row: IndexBuildRow) -> IndexBuild:
    """ORM 行 -> 只读快照。集中一份，避免 `get` 与 `list_builds` 两处字段慢慢漂移。"""
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
        page_end_no=row.page_end_no,
        heading_path=row.heading_path,
        char_start=row.char_start,
        char_end=row.char_end,
        token_count=row.token_count,
        locator_ref=row.locator_ref,
        # 带上 locator：结构化位置（页码/单元格/坐标框），引用渲染要回指原文
        locator=row.locator,
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
        page_end_no=row.page_end_no,
        heading_path=row.heading_path,
        locator_ref=row.locator_ref,
        # 带上 locator：结构化位置（页码/单元格/坐标框），引用渲染要回指原文
        locator=row.locator,
        embedding_model_key=row.embedding_model_key,
        embedding_dim=row.embedding_dim,
        active_document_version_id=row.active_document_version_id,
        distance=float(row.distance),
    )


def _to_pg_vector(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"
