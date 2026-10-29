"""S11b：向量检索入口的维度护栏。

为什么单独测这一层：维度不符在 PG 里是**抛错**而不是返回空。若不做前置校验，
现象是"某些 KB 的检索 100% 500，另一些正常"——排查时第一反应会怀疑权限或数据，
而不是维度，因为错的是 512 与 768 这种看不见的数。

这组测试同时钉住三件事：
1. 查询向量维度与库内不一致 → 当场抛 EmbeddingDimensionError，不落到 PG。
2. 库内并存两种维度（换模型未重建）→ 抛 INDEX_INCOMPLETE，不返回部分结果。
3. 跨区检索返回空，且不泄漏 chun吗数量。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B, Scope
from pyknora.db.repos.reads import SqlChunkSearchRepo
from pyknora.services.ingest.embedder import EmbeddingDimensionError, HashingEmbedder

KB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


async def _vec(text: str, dim: int = 512) -> list[float]:
    """取一个确定性向量。走真实后端代码，不在测试里重实现哈希。"""
    return (await HashingEmbedder(dim=dim).embed([text]))[0]


@pytest.fixture
def scope_a() -> Scope:
    return Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A)


@pytest.fixture
def scope_b() -> Scope:
    return Scope(workspace_id=WORKSPACE_B, principal_id=WORKSPACE_B)


async def _seed_one(session, scope: Scope, kb_id: UUID, text: str, dim: int = 512):
    """直接插一条 active 状态的 chunk，绕开 worker。

    这里是检索层的测试，不该依赖摄取链路；插一条最小可用记录即可。
    """
    from sqlalchemy import text as sql

    doc_id = uuid4()
    build_id = uuid4()
    version_id = uuid4()
    attempt_id = uuid4()
    chunk_id = uuid4()
    vec = await _vec(text, dim)
    vec_literal = "[" + ",".join(f"{x:.8f}" for x in vec) + "]"

    await session.execute(
        sql(
            """
            INSERT INTO documents
                (document_id, workspace_id, kb_id, desired_document_version_id,
                 active_document_version_id, active_build_id, active_index_version)
            VALUES (:doc_id, :ws, :kb, :ver, :ver, :build, 1)
            """
        ),
        {
            "doc_id": doc_id,
            "ws": scope.workspace_id,
            "kb": kb_id,
            "ver": version_id,
            "build": build_id,
        },
    )
    await session.execute(
        sql(
            """
            INSERT INTO index_builds
                (build_id, workspace_id, document_id, index_version, attempt_id,
                 embedding_model_key, embedding_dim, chunk_size, chunk_overlap,
                 chunk_count, state, activated_at)
            VALUES (:build, :ws, :doc, 1, :attempt, 'test/hashing', :dim,
                    512, 64, 1, 'active', now())
            """
        ),
        {
            "build": build_id,
            "ws": scope.workspace_id,
            "doc": doc_id,
            "attempt": attempt_id,
            "dim": dim,
        },
    )
    await session.execute(
        sql(
            """
            INSERT INTO chunks
                (chunk_id, workspace_id, document_id, build_id, index_version,
                 attempt_id, chunk_ordinal, content, content_hash, embedding)
            VALUES (:chunk, :ws, :doc, :build, 1, :attempt, 0, :content,
                    md5(:content), CAST(:vec AS vector))
            """
        ),
        {
            "chunk": chunk_id,
            "ws": scope.workspace_id,
            "doc": doc_id,
            "build": build_id,
            "attempt": attempt_id,
            "content": text,
            "vec": vec_literal,
        },
    )
    await session.commit()
    return doc_id


async def test_search_returns_hits_with_distance(session, scope_a) -> None:
    await _seed_one(session, scope_a, KB_ID, "银狐锚点 检索测试")
    repo = SqlChunkSearchRepo(session)
    q = await _vec("银狐锚点 检索测试")

    hits = await repo.search_similar(scope_a, KB_ID, q, limit=5)
    assert len(hits) == 1
    assert hits[0].kb_id == KB_ID
    # distance 是距离不是相似度：同向量必须接近 0
    assert hits[0].distance == pytest.approx(0.0, abs=1e-5)


async def test_keyword_search_matches_full_chinese_token(session, scope_a) -> None:
    """simple 配置按连续中文词整词匹配；本测试查询完整锚点词。"""
    from sqlalchemy import text as sql

    doc_id = await _seed_one(session, scope_a, KB_ID, "银狐锚点 检索测试")
    await session.execute(
        sql(
            "UPDATE chunks SET tsvector = to_tsvector('simple', content) "
            "WHERE workspace_id = :ws AND document_id = :doc"
        ),
        {"ws": scope_a.workspace_id, "doc": doc_id},
    )
    await session.commit()

    hits = await SqlChunkSearchRepo(session).search_keyword(scope_a, KB_ID, "银狐锚点", limit=5)

    assert len(hits) == 1
    assert hits[0].document_id == doc_id
    assert hits[0].content == "银狐锚点 检索测试"


async def test_keyword_search_filters_workspace_in_sql(session, scope_a, scope_b) -> None:
    """相同关键词在另一 workspace 中不可见。"""
    from sqlalchemy import text as sql

    doc_id = await _seed_one(session, scope_a, KB_ID, "赤鹿锚点 权限测试")
    await session.execute(
        sql(
            "UPDATE chunks SET tsvector = to_tsvector('simple', content) "
            "WHERE workspace_id = :ws AND document_id = :doc"
        ),
        {"ws": scope_a.workspace_id, "doc": doc_id},
    )
    await session.commit()

    repo = SqlChunkSearchRepo(session)
    assert await repo.search_keyword(scope_a, KB_ID, "赤鹿锚点", limit=5)
    assert await repo.search_keyword(scope_b, KB_ID, "赤鹿锚点", limit=5) == []


async def test_dimension_mismatch_raises_before_reaching_pg(session, scope_a) -> None:
    """查询向量 1024 维，库内 512 维 → 抛 EmbeddingDimensionError（不是 500）。"""
    await _seed_one(session, scope_a, KB_ID, "维度不符")
    repo = SqlChunkSearchRepo(session)
    wrong = await _vec("维度不符", dim=1024)

    with pytest.raises(EmbeddingDimensionError) as exc:
        await repo.search_similar(scope_a, KB_ID, wrong, limit=5)
    assert exc.value.details["expected"] == 512
    assert exc.value.details["actual"] == 1024


async def test_mixed_dimensions_are_forbidden_by_ddl_not_by_app(session, scope_a) -> None:
    """同库并存两种维度在**当前 schema 下根本插不进去**——先钉住这条事实。

    我原本写的是"库内并存 512 与 1024 → 应用层抛 INDEX_INCOMPLETE"，
    跑出来是 `DataError: expected 512 dimensions, not 1024`。原因不在测试写错，
    而在 `chunks.embedding` 声明为 `vector(:dim)`——整张表的维度是一个常量。

    这是个好消息：DDL 比应用层校验更早、更强。所以 `search_similar` 里那段
    多维度检查是**当前不可达的纵深防御**，不是主防线；主防线是列类型。
    换模型要换维度时必须走迁移（新列或新表），而不是插入不同长度的向量。
    这条断言因此保护的是"DDL 不要被将来某次迁移悄悄放宽"。
    """
    from sqlalchemy import text as sql

    await _seed_one(session, scope_a, KB_ID, "第一版 512 维", dim=512)
    vec_1024 = "[" + ",".join("0.01" for _ in range(1024)) + "]"

    with pytest.raises(Exception) as exc:
        await session.execute(
            sql(
                """
                INSERT INTO chunks
                    (chunk_id, workspace_id, document_id, build_id, index_version,
                     attempt_id, chunk_ordinal, content, content_hash, embedding)
                SELECT gen_random_uuid(), workspace_id, document_id, build_id, 1,
                       attempt_id, 99, '第二版 1024 维', md5('x'), CAST(:vec AS vector)
                FROM chunks LIMIT 1
                """
            ),
            {"vec": vec_1024},
        )
    assert "expected 512 dimensions" in str(exc.value)
    await session.rollback()


async def test_cross_workspace_search_returns_empty(session, scope_a, scope_b) -> None:
    """B 区搜 A 区的库 → 空列表。不是"抛出未找到"，是空。

    空与"不存在"在这里不可区分，正是我们要的：不泄漏这个 kb_id 是否存在。
    """
    await _seed_one(session, scope_a, KB_ID, "A 区机密内容")
    repo = SqlChunkSearchRepo(session)
    q = await _vec("A 区机密内容")

    hits = await repo.search_similar(scope_b, KB_ID, q, limit=5)
    assert hits == []


async def test_empty_kb_returns_empty_not_error(session, scope_a) -> None:
    """空库（还没摄取任何文档）→ 空列表，不是抛错。

    抛错会让新库的第一次检索就 500，而这恰恰是最常见的一次调用。
    """
    repo = SqlChunkSearchRepo(session)
    q = await _vec("任意查询")
    assert await repo.search_similar(scope_a, KB_ID, q, limit=5) == []
