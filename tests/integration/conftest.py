"""集成测试的公共夹具：真连 PG 容器，每个测试清库后跑。

不做 SQLite 替身。理由：本模块的正确性大量依赖 PG 特有语义
（`FOR UPDATE SKIP LOCKED`、`vector`、部分索引、视图），
换库跑等于没测。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from sqlalchemy import text

from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B, Scope
from pyknora.db.repos.reads import FakeKbRepo
from pyknora.db.session import dispose_engine, get_session_factory
from pyknora.schemas.kb import EmbeddingProfile
from pyknora.services.ingest.embedder import HashingEmbedder

TABLES = (
    # 成员4 的身份表：不在清单里就 TRUNCATE 不到，上一个用例的成员关系会漏给下一个。
    "role_grants",
    "roles",
    "workspace_memberships",
    "workspaces",
    "principals",
    "outbox",
    "job_attempts",
    "ingestion_jobs",
    "chunks",
    "index_builds",
    "document_versions",
    "document_blobs",
    "documents",
    "embedding_cache",
    "knowledge_bases",
    "index_migrations",
)

KB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
KB_ID_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


async def _seed_knowledge_bases(session) -> None:
    await session.execute(
        text(
            """
            INSERT INTO knowledge_bases (
                kb_id, workspace_id, name, embedding_model_key, embedding_dim,
                chunk_size, chunk_overlap, normalize
            ) VALUES
                (:kb_a, :ws_a, '测试知识库 A', 'test/hashing', 512, 512, 64, true),
                (:kb_b, :ws_b, '测试知识库 B', 'test/hashing', 512, 512, 64, true)
            """
        ),
        {"kb_a": KB_ID, "ws_a": WORKSPACE_A, "kb_b": KB_ID_B, "ws_b": WORKSPACE_B},
    )


@pytest.fixture(autouse=True)
async def _engine_ready() -> AsyncIterator[None]:
    """每个测试用完就释放连接池。

    必须是 function 作用域，不能是 session：
    1. pytest 配置里 `asyncio_default_fixture_loop_scope = "function"`，
       每个测试一个新事件循环，session 作用域的异步夹具会 ScopeMismatch。
    2. 更要紧的是 asyncpg 的连接池绑定在创建它的循环上。循环一关，
       池里的连接就是死的；不 dispose 的话下一个测试会拿到坏连接。
    """
    yield
    await dispose_engine()


@pytest.fixture
async def session():
    factory = get_session_factory()
    async with factory() as s:
        for table in TABLES:
            await s.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        await _seed_knowledge_bases(s)
        await s.commit()
        yield s


@pytest.fixture
def scope_a() -> Scope:
    return Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A)


@pytest.fixture
def scope_b() -> Scope:
    return Scope(workspace_id=WORKSPACE_B, principal_id=WORKSPACE_B)


@pytest.fixture
def kb_repo(scope_a: Scope) -> FakeKbRepo:
    profile = EmbeddingProfile(
        embedding_model_key="test/hashing",
        embedding_dim=512,
        chunk_size=512,
        chunk_overlap=64,
        normalize=True,
    )
    repo = FakeKbRepo()
    # 必须登记在 A 区上：FakeKbRepo 现在按 (workspace_id, kb_id) 存，
    # 漏了归属就等于这条 KB 谁都不属于——这会掩盖跨区越权的 bug。
    repo.add(scope_a, KB_ID, profile)
    return repo


@pytest.fixture
def kb_id():
    return KB_ID


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder(dim=512)
