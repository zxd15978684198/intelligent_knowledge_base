"""任务12 的验收：非 owner 角色在业务表上只看到 / 只写自己工作区的行。

**为什么必须单独建一个 `pyknora_app` 角色来测**：迁移和应用平时连的都是 `postgres`
（超级用户），超级用户天然 BYPASSRLS。拿它去测 RLS，任何策略都会被绕过，
于是测试永远是绿的——这正是"RLS 装了但等于没装"的典型现场。
所以本文件所有断言都通过 `pyknora_app` 这个**非 owner、非 superuser、非 bypassrls**
的登录角色执行，只有造数据用 `session`（owner）。

顺带钉住三件事，它们是这套机制能成立的前提：
1. `pyknora_app` 确实不是 superuser / bypassrls / 表 owner；
2. 事务里**没设置** `app.workspace_id` 时读不到任何行（fail-closed），
   而不是"读不到就放行全部"；
3. 视图 `v_active_chunks` 打开了 `security_invoker`，否则非 owner 角色读视图会
   绕过底下各表的策略（PG 的视图默认按 owner 权限执行）。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B, Scope
from pyknora.core.settings import settings
from pyknora.db.repos.reads import SqlChunkSearchRepo
from pyknora.services.ingest.embedder import HashingEmbedder

KB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

# 与 migrations/0003_identity_acl.sql 里的 CREATE ROLE 一致（本地开发凭据）。
APP_USER = "pyknora_app"
APP_PASSWORD = "pyknora_app_dev"


def _app_engine():
    """用同一个库、换成一个非 owner 的登录角色。"""
    url = make_url(settings.database_url).set(username=APP_USER, password=APP_PASSWORD)
    return create_async_engine(url, pool_pre_ping=False)


async def _insert_document(session, scope: Scope, *, kb_id: UUID = KB_ID) -> UUID:
    """直接插一行 documents（绕开上传链路）——本文件只关心行级可见性。"""
    doc_id = uuid4()
    version_id = uuid4()
    await session.execute(
        text(
            """
            INSERT INTO documents
                (document_id, workspace_id, kb_id, desired_document_version_id)
            VALUES (:doc_id, :ws, :kb, :ver)
            """
        ),
        {"doc_id": doc_id, "ws": scope.workspace_id, "kb": kb_id, "ver": version_id},
    )
    await session.commit()
    return doc_id


async def _seed_chunk(session, scope: Scope, content: str, *, dim: int = 512) -> UUID:
    """插一条 active chunk（与 test_search_repo 的最小集一致），返回 doc_id。"""
    vec = (await HashingEmbedder(dim=dim).embed([content]))[0]
    vec_literal = "[" + ",".join(f"{x:.8f}" for x in vec) + "]"

    doc_id, build_id, version_id, attempt_id, chunk_id = (uuid4() for _ in range(5))

    await session.execute(
        text(
            """
            INSERT INTO documents
                (document_id, workspace_id, kb_id, desired_document_version_id,
                 active_document_version_id, active_build_id, active_index_version)
            VALUES (:doc, :ws, :kb, :ver, :ver, :build, 1)
            """
        ),
        {
            "doc": doc_id,
            "ws": scope.workspace_id,
            "kb": KB_ID,
            "ver": version_id,
            "build": build_id,
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO index_builds
                (build_id, workspace_id, document_id, index_version, attempt_id,
                 embedding_model_key, embedding_dim, chunk_size, chunk_overlap,
                 chunk_count, state, activated_at)
            VALUES (:build, :ws, :doc, 1, :attempt, 'test/hashing', :dim, 512, 64,
                    1, 'active', now())
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
        text(
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
            "content": content,
            "vec": vec_literal,
        },
    )
    await session.commit()
    return doc_id


@pytest.fixture
def scope_a() -> Scope:
    return Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A)


@pytest.fixture
def scope_b() -> Scope:
    return Scope(workspace_id=WORKSPACE_B, principal_id=WORKSPACE_B)


# ---------------------------------------------------------------- 前提：角色不是 owner


async def test_app_role_cannot_bypass_rls(session) -> None:
    """RLS 对 owner/superuser 无效，所以先证明测试用的角色三者都不是。"""
    row = (
        await session.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :role"),
            {"role": APP_USER},
        )
    ).one()
    assert row.rolsuper is False
    assert row.rolbypassrls is False

    owner = (
        await session.execute(
            text(
                """
                SELECT pg_get_userbyid(relowner)
                FROM pg_class
                WHERE relname = 'documents' AND relkind = 'r'
                """
            )
        )
    ).scalar_one()
    assert owner != APP_USER


# ---------------------------------------------------------------- 读：跨区 0 行


async def test_app_role_sees_only_its_own_workspace_rows(session, scope_a) -> None:
    doc_a = await _insert_document(session, scope_a, kb_id=KB_ID)

    engine = _app_engine()
    try:
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('app.workspace_id', :ws, true)"),
                    {"ws": str(WORKSPACE_B)},
                )
                rows_b = (await conn.execute(text("SELECT document_id FROM documents"))).all()
            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('app.workspace_id', :ws, true)"),
                    {"ws": str(WORKSPACE_A)},
                )
                rows_a = (await conn.execute(text("SELECT document_id FROM documents"))).all()
    finally:
        await engine.dispose()

    assert rows_b == [], "B 区角色读到了 A 区的文档——RLS 策略没生效"
    assert [row.document_id for row in rows_a] == [doc_a]


async def test_app_role_without_context_reads_nothing(session, scope_a) -> None:
    """没设 `app.workspace_id` 时必须 0 行。

    这条防的是"为了修某个 500 把策略改成 COALESCE(..., true)"那种放行补丁：
    fail-closed 是这套兜底唯一有价值的地方。
    """
    await _insert_document(session, scope_a, kb_id=KB_ID)

    engine = _app_engine()
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT document_id FROM documents"))).all()
    finally:
        await engine.dispose()

    assert rows == []


# ---------------------------------------------------------------- 写：跨区 INSERT 被拒


async def test_app_role_cannot_insert_into_another_workspace(session) -> None:
    engine = _app_engine()
    try:
        async with engine.connect() as conn:
            # 显式开事务（autobegin 也算）：set_config 的 is_local 只在事务内有效。
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"),
                {"ws": str(WORKSPACE_A)},
            )
            with pytest.raises(DBAPIError) as exc:
                await conn.execute(
                    text(
                        """
                        INSERT INTO documents
                            (document_id, workspace_id, kb_id, desired_document_version_id)
                        VALUES (:doc, :ws, :kb, :ver)
                        """
                    ),
                    {
                        "doc": uuid4(),
                        "ws": WORKSPACE_B,
                        "kb": KB_ID,
                        "ver": uuid4(),
                    },
                )
            # 语句失败后事务已进入 aborted 状态，必须显式回滚再还回连接池。
            await conn.rollback()
    finally:
        await engine.dispose()

    assert "row-level security" in str(exc.value).lower()


# ---------------------------------------------------------------- 视图也是 invoker 权限


async def test_active_chunks_view_uses_security_invoker(session) -> None:
    """视图不开 `security_invoker` 时按 owner 权限执行，底下的策略会被整体绕过。"""
    options = (
        await session.execute(
            text(
                """
                SELECT reloptions FROM pg_class
                WHERE relname = 'v_active_chunks' AND relkind = 'v'
                """
            )
        )
    ).scalar_one()
    assert "security_invoker=true" in [o.lower() for o in (options or [])]


# ---------------------------------------------------------------- A1：检索层跨区返回空


async def test_search_across_workspaces_returns_empty(session, scope_a, scope_b) -> None:
    """A1 的检索层验证：A 区的 chunk 在 B 的 scope 下必须一条都查不到。"""
    await _seed_chunk(session, scope_a, "银狐跨区 不应被检索到")
    repo = SqlChunkSearchRepo(session)
    query = (await HashingEmbedder(dim=512).embed(["银狐跨区 不应被检索到"]))[0]

    assert await repo.search_similar(scope_b, KB_ID, query, limit=5) == []
    assert len(await repo.search_similar(scope_a, KB_ID, query, limit=5)) == 1


# --------------------------------------- 0012 给视图加 KB join 后，非 owner 角色仍看得见


async def test_active_chunks_view_join_works_under_rls_and_soft_deleted_kb(
    session, scope_a
) -> None:
    """`0012` 给 `v_active_chunks` 加了 `JOIN knowledge_bases`——必须钉住两件事。

    ① **非 owner 角色不能被 join 误伤**：视图是 `security_invoker`，join 的
    `knowledge_bases` 自己也有 RLS。若策略写得不匹配（或哪天有人把 join 改成
    子查询并换了条件），非 owner 角色读到的会是**空集而不是报错**——检索静默
    变空，比报错更难查。所以断言必须走 `pyknora_app`（`session` 是超级用户，
    它测得再绿也证明不了这里）。
    ② **join 的过滤是双向的**：软删 KB 之后同一个角色必须立刻读不到切片，
    否则 `0012` 只是把「删库不影响检索」从文档层搬到了视图层。

    两条单独都做过反证：把 0012 的 join 换回 0001 的定义 → ② fail（软删后仍 1 行）；
    把 `security_invoker` 拿掉 → ① 在 B 区上下文下读到 A 区行。
    """
    doc_id = await _seed_chunk(session, scope_a, "银狐锚点报销标准 500 元")

    engine = _app_engine()

    async def count_visible(workspace_id: UUID) -> int:
        async with engine.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"),
                {"ws": str(workspace_id)},
            )
            return (
                await conn.execute(
                    text("SELECT count(*) FROM v_active_chunks WHERE document_id = :doc"),
                    {"doc": doc_id},
                )
            ).scalar_one()

    try:
        assert await count_visible(scope_a.workspace_id) == 1, (
            "非 owner 角色读不到自己区的切片——KB join 把行吃掉了（RLS 与 join 谓词不匹配）"
        )
        assert await count_visible(WORKSPACE_B) == 0, "跨区读到了 A 区切片——RLS 没生效"

        # 软删知识库（owner 会话）：视图必须立刻跟着变空。
        await session.execute(
            text(
                "UPDATE knowledge_bases SET deleted_at = now(), generation = generation + 1, "
                "row_version = row_version + 1 WHERE workspace_id = :ws AND kb_id = :kb"
            ),
            {"ws": scope_a.workspace_id, "kb": KB_ID},
        )
        await session.commit()

        assert await count_visible(scope_a.workspace_id) == 0, (
            "软删知识库后仍读得到切片——0012 的 k.deleted_at IS NULL 谓词没生效"
        )
    finally:
        await engine.dispose()
