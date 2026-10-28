"""成员1任务1 / S05：知识库 CRUD 的 API 验收。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from pyknora.api.main import create_app, reset_state
from tests.api.conftest import _make_client
from tests.api.tokens import bearer


def _payload(name: str = "研发知识库") -> dict[str, Any]:
    return {
        "name": name,
        "embedding_model_key": "test/hashing",
        "embedding_dim": 512,
        "chunk_size": 512,
        "chunk_overlap": 64,
        "normalize": True,
    }


async def _real_client():
    reset_state()
    return _make_client(create_app(), strict=True)


async def test_create_duplicate_patch_and_soft_delete(clean_db: None, db_session: Any) -> None:
    async with await _real_client() as client:
        created = await client.post("/api/v1/knowledge-bases", headers=bearer(), json=_payload())
        assert created.status_code == 201, created.text
        body = created.json()
        kb_id = body["kb_id"]
        assert body["row_version"] == 1
        assert body["generation"] == 1

        duplicate = await client.post(
            "/api/v1/knowledge-bases", headers=bearer(), json=_payload("研发知识库")
        )
        assert duplicate.status_code == 409

        missing_revision = await client.patch(
            f"/api/v1/knowledge-bases/{kb_id}", headers=bearer(), json={"name": "新名称"}
        )
        assert missing_revision.status_code == 428
        assert missing_revision.json()["error"]["code"] == "REVISION_MISSING"

        stale = await client.patch(
            f"/api/v1/knowledge-bases/{kb_id}",
            headers={**bearer(), "If-Match": '"9"'},
            json={"name": "新名称"},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"

        updated = await client.patch(
            f"/api/v1/knowledge-bases/{kb_id}",
            headers={**bearer(), "If-Match": '"1"'},
            json={"name": "新名称"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["row_version"] == 2

        deleted = await client.delete(
            f"/api/v1/knowledge-bases/{kb_id}",
            headers={**bearer(), "If-Match": '"2"'},
        )
        assert deleted.status_code == 202, deleted.text
        assert deleted.json()["generation"] == 2

        hidden = await client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=bearer())
        assert hidden.status_code == 404

    row = await db_session.execute(
        text("SELECT deleted_at, generation FROM knowledge_bases WHERE kb_id = :kb_id"),
        {"kb_id": kb_id},
    )
    deleted_at, generation = row.one()
    assert deleted_at is not None
    assert generation == 2
    event = await db_session.execute(
        text(
            "SELECT count(*) FROM outbox "
            "WHERE aggregate_id = :kb_id AND event_type = 'knowledge_base.deleted'"
        ),
        {"kb_id": kb_id},
    )
    assert event.scalar_one() == 1


async def test_cross_workspace_is_404_and_config_patch_is_422(clean_db: None) -> None:
    async with await _real_client() as client:
        created = await client.post(
            "/api/v1/knowledge-bases", headers=bearer(), json=_payload("隔离库")
        )
        kb_id = created.json()["kb_id"]

        cross_scope = await client.get(
            f"/api/v1/knowledge-bases/{kb_id}",
            headers=bearer("22222222-2222-2222-2222-222222222222"),
        )
        assert cross_scope.status_code == 404

        immutable = await client.patch(
            f"/api/v1/knowledge-bases/{kb_id}",
            headers={**bearer(), "If-Match": '"1"'},
            json={"name": "隔离库", "chunk_size": 1024},
        )
        assert immutable.status_code == 422


async def test_soft_deleted_name_can_be_reused(clean_db: None) -> None:
    async with await _real_client() as client:
        first = await client.post(
            "/api/v1/knowledge-bases", headers=bearer(), json=_payload("可复用")
        )
        kb_id = first.json()["kb_id"]
        await client.delete(
            f"/api/v1/knowledge-bases/{kb_id}",
            headers={**bearer(), "If-Match": '"1"'},
        )
        second = await client.post(
            "/api/v1/knowledge-bases", headers=bearer(), json=_payload("可复用")
        )
        assert second.status_code == 201, second.text
        assert second.json()["kb_id"] != kb_id
