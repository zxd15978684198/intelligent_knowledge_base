"""`POST /api/v1/admin/reindex` 的 API 层验收（任务16 / F8）。

覆盖的断言直接对应任务书四条：

1. 成功重建后 `documents.active_index_build_id` 变了，新 build `state='active'`、
   旧 build `state='retired'`，`active_index_version` 严格 +1；
2. **正文与切片边界不变**：重建前后走 `GET /documents/{id}/chunks` 拿到的
   `content` 与 `chunk_ordinal` 逐项相同，切片数相同；
3. 他人的 kb_id → 404（不是 403，也不是 202）；同 kb_id 的他人 workspace
   看不到本区文档；
4. 连续调用两次仍然"每个文档恰好一个 active build"，且带同一 `idempotency_key`
   的重试不会把版本号刷上去。

另外两条边界：`mode=export` 明确 422 且不写记账行（本轮未实现，不许静默降级）；
"没有任何已发布文档"的 KB 回 202 且 `documents=0`（幂等空跑，不是 404）。

**为什么要真跑一次 worker**：重建的源数据是"已发布 build 的切片"，所以必须先有
active build。这里不绕开 worker 自己造数据——那样测的就不是真实的前置状态了。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text

from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B, Scope
from pyknora.schemas.kb import EmbeddingProfile
from pyknora.services.ingest.embedder import HashingEmbedder
from pyknora.workers import ingest_worker
from tests.api.tokens import KB_ID, bearer

REINDEX_URL = "/api/v1/admin/reindex"

TXT = ("PyKnora 重建索引测试正文：切片边界必须逐字节保持。" * 40).encode()

_DOC_SQL = text(
    """
    SELECT active_build_id, active_index_version, active_document_version_id,
           desired_document_version_id, generation, row_version
    FROM documents
    WHERE workspace_id = :workspace_id AND document_id = :document_id
    """
)

_BUILDS_SQL = text(
    """
    SELECT build_id, state, index_version, chunk_count, chunk_size, chunk_overlap,
           embedding_model_key, embedding_dim
    FROM index_builds
    WHERE workspace_id = :workspace_id AND document_id = :document_id
    ORDER BY index_version, build_id
    """
)

_MIGRATIONS_SQL = text(
    """
    SELECT migration_id, workspace_id, kb_id, mode, state, manifest, error_code, finished_at
    FROM index_migrations
    WHERE workspace_id = :workspace_id
    ORDER BY created_at DESC
    """
)


# ------------------------------------------------------------------ 夹具/助手


async def _upload(
    client: httpx.AsyncClient,
    *,
    filename: str = "reindex.txt",
    content: bytes = TXT,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    r = await client.post(
        f"/api/v1/knowledge-bases/{KB_ID}/documents",
        headers=headers or bearer(),
        files={"file": (filename, content, "text/plain")},
    )
    assert r.status_code == 202, r.text
    return r.json()


async def _ingest_pending(db_session, kb_repo) -> None:
    """把当前待处理的摄取作业跑完——重建的前置条件是一个 active build。"""
    result = await ingest_worker.run_once(
        db_session,
        worker_id="w-api-reindex",
        kb_repo=kb_repo,
        embedder=HashingEmbedder(dim=512),
    )
    assert result is not None, "应当有一个待处理的摄取作业"
    assert result.status == "succeeded", result
    await db_session.commit()


async def _chunks(
    client: httpx.AsyncClient, document_id: str, *, headers: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    r = await client.get(
        f"/api/v1/documents/{document_id}/chunks",
        params={"limit": 500},
        headers=headers or bearer(),
    )
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def _doc_state(db_session, document_id: str) -> dict[str, Any]:
    row = (
        (
            await db_session.execute(
                _DOC_SQL, {"workspace_id": WORKSPACE_A, "document_id": UUID(document_id)}
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def _builds(db_session, document_id: str) -> list[dict[str, Any]]:
    rows = (
        await db_session.execute(
            _BUILDS_SQL, {"workspace_id": WORKSPACE_A, "document_id": UUID(document_id)}
        )
    ).mappings()
    return [dict(row) for row in rows]


async def _migrations(db_session) -> list[dict[str, Any]]:
    rows = (await db_session.execute(_MIGRATIONS_SQL, {"workspace_id": WORKSPACE_A})).mappings()
    return [dict(row) for row in rows]


async def _active_build_id(db_session, document_id: str) -> UUID | None:
    return (await _doc_state(db_session, document_id))["active_build_id"]


async def _seeded_document(client: httpx.AsyncClient, db_session, kb_repo) -> str:
    """上传 + 跑 worker，返回一个"已发布"的 document_id。"""
    uploaded = await _upload(client)
    await _ingest_pending(db_session, kb_repo)
    document_id = uploaded["document_id"]
    state = await _doc_state(db_session, document_id)
    assert state["active_build_id"] is not None, "前置失败：文档没有被发布成 active build"
    return document_id


# ---------------------------------------------------------------- 权限与形状


async def test_no_token_is_401(client: httpx.AsyncClient) -> None:
    r = await client.post(REINDEX_URL, json={"kb_id": str(KB_ID)})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_unknown_kb_is_404(client: httpx.AsyncClient) -> None:
    r = await client.post(REINDEX_URL, json={"kb_id": str(uuid4())}, headers=bearer())
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


async def test_other_workspace_kb_is_404_not_403(
    clean_db: None, client: httpx.AsyncClient, kb_repo
) -> None:
    """A 区独有的 kb_id 用 B 区令牌调用：必须 404。

    `clean_db` 里 KB_ID 在两个区都登记给 FakeKbRepo（那是为了测"同 kb_id 不越权取
    文档"），所以这里另外造一个只属于 A 的 KB——否则测不出 404。404 而不是 403
    是刻意的：状态码不能当 kb_id 是否存在探针用。
    """
    a_only = uuid4()
    profile = EmbeddingProfile(
        embedding_model_key="test/hashing",
        embedding_dim=512,
        chunk_size=512,
        chunk_overlap=64,
    )
    kb_repo.add(Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A), a_only, profile)

    r = await client.post(
        REINDEX_URL, json={"kb_id": str(a_only)}, headers=bearer(str(WORKSPACE_B))
    )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


# ------------------------------------------------------------------- 重建本身


async def test_reindex_rebuilds_active_build_and_retires_old(
    clean_db: None, client: httpx.AsyncClient, kb_repo, db_session
) -> None:
    document_id = await _seeded_document(client, db_session, kb_repo)
    before_doc = await _doc_state(db_session, document_id)
    before_builds = await _builds(db_session, document_id)
    assert len(before_builds) == 1
    old_build = before_builds[0]
    assert old_build["state"] == "active"

    r = await client.post(REINDEX_URL, json={"kb_id": str(KB_ID)}, headers=bearer())
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["state"] == "succeeded"
    assert body["documents"] == 1
    assert body["chunks"] == old_build["chunk_count"]
    assert body["skipped"] == 0
    assert body["failed"] == 0
    assert body["replayed"] is False

    after_doc = await _doc_state(db_session, document_id)
    assert after_doc["active_build_id"] != before_doc["active_build_id"], "指针必须换到新 build"
    assert after_doc["active_index_version"] == before_doc["active_index_version"] + 1
    assert after_doc["generation"] == before_doc["generation"] + 1
    assert after_doc["active_document_version_id"] == before_doc["active_document_version_id"]

    builds = {row["build_id"]: row for row in await _builds(db_session, document_id)}
    new_build = builds[after_doc["active_build_id"]]
    assert new_build["state"] == "active"
    assert new_build["index_version"] == after_doc["active_index_version"]
    assert new_build["chunk_count"] == old_build["chunk_count"]
    retired = builds[old_build["build_id"]]
    assert retired["state"] == "retired"

    migrations = await _migrations(db_session)
    assert len(migrations) == 1
    migration = migrations[0]
    assert migration["migration_id"] == UUID(body["migration_id"])
    assert migration["kb_id"] == KB_ID
    assert migration["mode"] == "reindex"
    assert migration["state"] == "succeeded"
    assert migration["error_code"] is None
    assert migration["finished_at"] is not None
    assert migration["manifest"]["documents_rebuilt"] == 1
    assert migration["manifest"]["chunks_written"] == old_build["chunk_count"]


async def test_reindex_preserves_chunk_content_and_ordinals(
    clean_db: None, client: httpx.AsyncClient, kb_repo, db_session
) -> None:
    document_id = await _seeded_document(client, db_session, kb_repo)
    before = await _chunks(client, document_id)
    assert before, "前置失败：已发布文档应当有切片"

    r = await client.post(REINDEX_URL, json={"kb_id": str(KB_ID)}, headers=bearer())
    assert r.status_code == 202, r.text

    after = await _chunks(client, document_id)
    assert len(after) == len(before), "重建不得增删切片——它只重算向量与倒排"
    assert [c["chunk_ordinal"] for c in after] == [c["chunk_ordinal"] for c in before]
    assert [c["content"] for c in after] == [c["content"] for c in before]
    # 定位字段也要原样保住，否则引用高亮会错位。
    assert [c["char_start"] for c in after] == [c["char_start"] for c in before]
    assert [c["char_end"] for c in after] == [c["char_end"] for c in before]


async def test_reindex_applies_new_profile_params_without_reslicing(
    clean_db: None, client: httpx.AsyncClient, kb_repo, db_session
) -> None:
    """KB profile 改了切片参数时，新 build 要如实记下新参数。

    重建**不重新切片**（不重跑解析），所以 chunk_size 只写进 build 元数据；
    这一条同时钉住两件事：新参数真的生效，正文没被悄悄切一遍。
    """
    document_id = await _seeded_document(client, db_session, kb_repo)
    before = await _chunks(client, document_id)

    kb_repo.add(
        Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A),
        KB_ID,
        EmbeddingProfile(
            embedding_model_key="test/hashing",
            embedding_dim=512,
            chunk_size=1024,
            chunk_overlap=128,
        ),
    )

    r = await client.post(REINDEX_URL, json={"kb_id": str(KB_ID)}, headers=bearer())
    assert r.status_code == 202, r.text
    assert r.json()["state"] == "succeeded"

    active_id = await _active_build_id(db_session, document_id)
    builds = {row["build_id"]: row for row in await _builds(db_session, document_id)}
    active = builds[active_id]
    assert active["chunk_size"] == 1024
    assert active["chunk_overlap"] == 128
    assert active["embedding_model_key"] == "test/hashing"

    after = await _chunks(client, document_id)
    assert [c["content"] for c in after] == [c["content"] for c in before]


# ---------------------------------------------------------------- 重复与幂等


async def test_repeated_reindex_keeps_single_active_build(
    clean_db: None, client: httpx.AsyncClient, kb_repo, db_session
) -> None:
    document_id = await _seeded_document(client, db_session, kb_repo)
    before = await _chunks(client, document_id)

    first = await client.post(REINDEX_URL, json={"kb_id": str(KB_ID)}, headers=bearer())
    second = await client.post(REINDEX_URL, json={"kb_id": str(KB_ID)}, headers=bearer())
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["migration_id"] != second.json()["migration_id"]

    builds = await _builds(db_session, document_id)
    active = [row for row in builds if row["state"] == "active"]
    assert len(active) == 1, "任何时刻每个文档最多一个 active build"
    assert [row["state"] for row in builds].count("retired") == 2

    state = await _doc_state(db_session, document_id)
    assert state["active_index_version"] == 3
    assert state["active_build_id"] == active[0]["build_id"]

    after = await _chunks(client, document_id)
    assert [c["content"] for c in after] == [c["content"] for c in before]


async def test_idempotency_key_replays_without_new_build(
    clean_db: None, client: httpx.AsyncClient, kb_repo, db_session
) -> None:
    document_id = await _seeded_document(client, db_session, kb_repo)
    payload = {"kb_id": str(KB_ID), "idempotency_key": "reindex-once-2026-10-29"}

    first = await client.post(REINDEX_URL, json=payload, headers=bearer())
    assert first.status_code == 202, first.text
    assert first.json()["replayed"] is False
    version_after_first = (await _doc_state(db_session, document_id))["active_index_version"]

    second = await client.post(REINDEX_URL, json=payload, headers=bearer())
    assert second.status_code == 202, second.text
    assert second.json()["replayed"] is True
    assert second.json()["migration_id"] == first.json()["migration_id"]
    assert second.json()["documents"] == first.json()["documents"]

    assert (await _doc_state(db_session, document_id))[
        "active_index_version"
    ] == version_after_first, "重放不得再建一个 build"
    assert len(await _builds(db_session, document_id)) == 2  # 原始 1 + 重建 1
    assert len(await _migrations(db_session)) == 1


# ---------------------------------------------------------------- 未实现/空跑


async def test_unimplemented_mode_is_422_and_writes_no_migration_row(
    clean_db: None, client: httpx.AsyncClient, kb_repo, db_session
) -> None:
    await _seeded_document(client, db_session, kb_repo)

    r = await client.post(
        REINDEX_URL, json={"kb_id": str(KB_ID), "mode": "export"}, headers=bearer()
    )
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert "not implemented" in error["message"]
    assert await _migrations(db_session) == [], "未实现的模式不得留下记账行"


async def test_kb_without_published_documents_is_noop(
    clean_db: None, client: httpx.AsyncClient, db_session
) -> None:
    r = await client.post(REINDEX_URL, json={"kb_id": str(KB_ID)}, headers=bearer())
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["state"] == "succeeded"
    assert body["documents"] == 0
    assert body["chunks"] == 0
    assert body["failed"] == 0
    assert len(await _migrations(db_session)) == 1


# -------------------------------------------------------------- 跨区隔离（写路径）


async def test_other_workspace_cannot_touch_this_workspace_documents(
    clean_db: None, client: httpx.AsyncClient, kb_repo, db_session
) -> None:
    """B 区重建自己的库：看到 0 个文档，A 区指针一动不动。

    这是 `_INDEXED_DOCUMENTS_SQL` 里 `documents.workspace_id` 谓词的守门用例：
    少了它，B 区一次"空重建"就能把 A 区的索引重算一遍（甚至抢走 active 指针）。

    注意 `knowledge_bases.kb_id` 是**全局主键**（`migrations/0002`），
    "同一个 kb_id 登记在两个区"只存在于 `FakeKbRepo`，库里做不到；所以这里给
    B 区一个不同的 kb_id、同一个嵌入配方 —— 测的是 `workspace_id` 谓词，不是外键。
    """
    document_id = await _seeded_document(client, db_session, kb_repo)
    before_build = await _active_build_id(db_session, document_id)

    # B 区真的拥有自己的 KB（否则外键会拦住记账行；那是另一条防线）。
    kb_id_b = uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO knowledge_bases (
                kb_id, workspace_id, name, embedding_model_key, embedding_dim,
                chunk_size, chunk_overlap, normalize
            ) VALUES (:kb_id, :workspace_id, 'B 区自己的库', 'test/hashing', 512, 512, 64, true)
            """
        ),
        {"kb_id": kb_id_b, "workspace_id": WORKSPACE_B},
    )
    await db_session.commit()
    kb_repo.add(
        Scope(workspace_id=WORKSPACE_B, principal_id=WORKSPACE_B),
        kb_id_b,
        EmbeddingProfile(
            embedding_model_key="test/hashing",
            embedding_dim=512,
            chunk_size=512,
            chunk_overlap=64,
        ),
    )

    r = await client.post(
        REINDEX_URL, json={"kb_id": str(kb_id_b)}, headers=bearer(str(WORKSPACE_B))
    )
    assert r.status_code == 202, r.text
    assert r.json()["documents"] == 0

    assert await _active_build_id(db_session, document_id) == before_build
    assert (
        len([row for row in await _builds(db_session, document_id) if row["state"] == "active"])
        == 1
    )
    # 记账行落在 B 区，A 区查不到。
    assert await _migrations(db_session) == []
    rows_b = (await db_session.execute(_MIGRATIONS_SQL, {"workspace_id": WORKSPACE_B})).mappings()
    assert [row["kb_id"] for row in rows_b] == [kb_id_b]
