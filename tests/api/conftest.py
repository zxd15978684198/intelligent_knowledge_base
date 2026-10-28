"""API 测试夹具：真 PG、真 FastAPI，只有一个事件循环。

**为什么用 `httpx.AsyncClient` 而不是 `TestClient`**：`TestClient` 内部自己起一个
事件循环（走 anyio portal），而 asyncpg 的连接池绑定在创建它的那个循环上。一旦
测试体里再用 `asyncio.run(...)` 去查一次库，就是第二个循环去借第一个循环的连接，
报的错是 `Event loop is closed` 或 `'NoneType' object has no attribute 'send'`
——看起来像连接池坏了，其实是两套循环。用 ASGI 传输让 app 和测试体共享 pytest
自己的循环，这类错误就整体消失。

令牌构造与常量在 `tests/api/tokens.py`，不在本文件里——理由写在那儿。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from sqlalchemy import text

from pyknora.api.main import configure, create_app, reset_state
from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B, Scope
from pyknora.db.repos.reads import FakeKbRepo
from pyknora.db.session import dispose_engine, get_session_factory
from pyknora.schemas.kb import EmbeddingProfile
from pyknora.services.ingest.embedder import HashingEmbedder
from tests.api.tokens import JWT_SECRET, KB_ID

TABLES = (
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
)


@pytest.fixture(autouse=True)
def _jwt_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """固定密钥。生产默认值是开发占位，不能拿来断言。"""
    monkeypatch.setenv("PYKNORA_JWT_SECRET", JWT_SECRET)
    yield
    reset_state()


@pytest.fixture(autouse=True)
async def _engine_ready() -> AsyncIterator[None]:
    """每个测试结束释放连接池。

    必须 function 作用域：`asyncio_default_fixture_loop_scope = "function"`，
    每个测试一个新循环，池里的连接跟着作废。不 dispose 的话下一个测试
    会借到上一个循环里的死连接。
    """
    yield
    await dispose_engine()


@pytest.fixture
async def clean_db() -> AsyncIterator[None]:
    factory = get_session_factory()
    async with factory() as session:
        for table in TABLES:
            await session.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        await session.execute(
            text(
                """
                INSERT INTO knowledge_bases (
                    kb_id, workspace_id, name, embedding_model_key, embedding_dim,
                    chunk_size, chunk_overlap, normalize
                ) VALUES (
                    :kb_id, :workspace_id, '上传测试知识库',
                    'test/hashing', 512, 512, 64, true
                )
                """
            ),
            {"kb_id": KB_ID, "workspace_id": WORKSPACE_A},
        )
        await session.commit()
    yield


@pytest.fixture
def kb_repo() -> FakeKbRepo:
    profile = EmbeddingProfile(
        embedding_model_key="test/hashing",
        embedding_dim=512,
        chunk_size=512,
        chunk_overlap=64,
    )
    repo = FakeKbRepo()
    # 两个区各自登记同一个 kb_id —— kb_id 跨区重复合法，靠
    # (workspace_id, kb_id) 区分归属。只登记一个区会掩盖跨区越权。
    repo.add(Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A), KB_ID, profile)
    repo.add(Scope(workspace_id=WORKSPACE_B, principal_id=WORKSPACE_B), KB_ID, profile)
    return repo


def _make_client(app, *, strict: bool) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=strict)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def client(kb_repo: FakeKbRepo) -> AsyncIterator[httpx.AsyncClient]:
    """默认客户端：应用内异常直接抛出来，不伪装成 500。

    这样任何意外的崩溃都会带着 traceback 失败，而不是变成一条
    "status 500" 的断言错误让人误以为是业务逻辑问题。
    """
    configure(kb_repo=kb_repo, embedder=HashingEmbedder(dim=512))
    async with _make_client(create_app(), strict=True) as c:
        yield c


@pytest.fixture
async def lenient_client() -> AsyncIterator[httpx.AsyncClient]:
    """宽松客户端：把框架级未捕获异常留在 500 响应里，供"不泄露 details"那条用。"""
    configure(kb_repo=FakeKbRepo(), embedder=HashingEmbedder(dim=512))
    async with _make_client(create_app(), strict=False) as c:
        yield c


@pytest.fixture
async def db_session():
    """直连库的会话，供测试断言"库里的真实值"用（不走 API）。"""
    factory = get_session_factory()
    async with factory() as session:
        yield session
