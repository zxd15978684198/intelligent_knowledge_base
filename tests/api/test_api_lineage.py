"""任务8 的 HTTP 面：版本链路必须能看出"谁、什么时候、通过什么途径传了哪一版"。

需要真 PG（`clean_db` 建种子知识库）。断言口径对应 `docs/plans/全员模块实现路径.md`
的 S15：**A 传 v1、B 改 v2 后 lineage 里两条记录的创建者不同**。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text

from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B
from tests.api.tokens import KB_ID, make_token

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


def _auth(sub: str, workspace_id: UUID = WORKSPACE_A) -> dict[str, str]:
    token = make_token(sub=sub, workspace_id=str(workspace_id))
    return {"Authorization": f"Bearer {token}"}


async def _upload(
    client: httpx.AsyncClient,
    *,
    sub: str,
    filename: str,
    content: bytes,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/knowledge-bases/{KB_ID}/documents",
        headers=_auth(sub),
        files={"file": (filename, content, "text/plain")},
    )


async def test_single_version_lineage_records_creator_and_source(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    r = await _upload(client, sub=ALICE, filename="v.txt", content=b"first")
    assert r.status_code == 202
    doc_id = r.json()["document_id"]

    lineage = await client.get(f"/api/v1/documents/{doc_id}/lineage", headers=_auth(ALICE))
    assert lineage.status_code == 200

    body = lineage.json()
    assert [v["ordinal"] for v in body["versions"]] == [1]
    version = body["versions"][0]
    assert version["created_by_principal_id"] == ALICE
    assert version["ingestion_source"] == "upload"
    assert version["document_version_id"] == r.json()["document_version_id"]
    assert body["contributors"] == [ALICE]


async def test_second_contributor_shows_up_with_active_pointer(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """S15：两条版本的创建者不同；活动标记只看 documents 指针，不看"最新一版"。"""
    r1 = await _upload(client, sub=ALICE, filename="v.txt", content=b"first")
    doc_id = r1.json()["document_id"]
    v1 = r1.json()["document_version_id"]

    # worker 没跑，发布事务不会切指针；这里直接指到 v1，验证"活动标记来自指针"。
    await db_session.execute(
        text(
            "UPDATE documents SET active_document_version_id = :vid "
            "WHERE workspace_id = :ws AND document_id = :doc"
        ),
        {"vid": v1, "ws": WORKSPACE_A, "doc": doc_id},
    )
    await db_session.commit()

    r2 = await _upload(client, sub=BOB, filename="v.txt", content=b"second")
    assert r2.status_code == 202
    assert r2.json()["document_id"] == doc_id
    v2 = r2.json()["document_version_id"]

    body = (await client.get(f"/api/v1/documents/{doc_id}/lineage", headers=_auth(BOB))).json()

    assert body["active_version_id"] == v1
    assert body["contributors"] == [ALICE, BOB]
    assert [(v["document_version_id"], v["ordinal"], v["is_active"]) for v in body["versions"]] == [
        (v1, 1, True),
        (v2, 2, False),
    ]


async def test_unknown_document_is_404(client: httpx.AsyncClient, clean_db: None) -> None:
    r = await client.get(
        "/api/v1/documents/00000000-0000-0000-0000-000000000000/lineage",
        headers=_auth(ALICE),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


async def test_cross_workspace_lineage_is_404_not_403(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    """B 区拿 A 区的 document_id → 404：403 等于承认"这个 id 存在"。"""
    r = await _upload(client, sub=ALICE, filename="v.txt", content=b"workspace a secret")
    doc_id = r.json()["document_id"]

    r2 = await client.get(
        f"/api/v1/documents/{doc_id}/lineage",
        headers=_auth(BOB, WORKSPACE_B),
    )
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
