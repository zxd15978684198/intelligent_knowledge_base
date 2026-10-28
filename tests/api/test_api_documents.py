"""API 层集成测试：权限、状态码、错误体形状。

走真实的 FastAPI + 真实 PG，只把 `kb_repo` 换成 `FakeKbRepo`。因此它能挡住三类
问题：依赖注入写错与状态码语义错；**某个路由忘了 `Depends(get_scope)` 而变成匿名
可访问**；请求体里的伪造身份字段被真的读进去。

夹具见 `tests/api/conftest.py`——注意那里用的是 `httpx.AsyncClient` 而不是
`TestClient`，理由写在同一文件顶部（一句话：asyncpg 的连接池不能跨事件循环借）。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from tests.api.tokens import KB_ID, bearer, make_token

WORKSPACE_B = "22222222-2222-2222-2222-222222222222"
MISSING_ID = "00000000-0000-0000-0000-000000000001"


async def _upload(
    client: httpx.AsyncClient,
    *,
    filename: str = "note.txt",
    content: bytes = b"hello world",
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/knowledge-bases/{KB_ID}/documents",
        headers=headers or bearer(),
        files={"file": (filename, content, "text/plain")},
        data=data or {},
    )


# ---------------------------------------------------------------- 权限边界


async def test_no_token_is_401_with_unified_body(client: httpx.AsyncClient) -> None:
    r = await client.get(f"/api/v1/documents/{MISSING_ID}")
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert body["error"]["retryable"] is False
    assert body["request_id"]
    # 响应头与错误体里的 request_id 必须一致，否则排障时对不上日志。
    assert r.headers["x-request-id"] == body["request_id"]


async def test_body_cannot_forge_identity(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """请求体里塞 workspace_id / principal_id / user_id 必须被完全忽略。

    课件里的正面用例：伪造字段进入请求体，服务端只认 JWT，越权不会发生。
    """
    forged = {
        "workspace_id": WORKSPACE_B,
        "principal_id": WORKSPACE_B,
        "user_id": "admin",
    }
    r = await _upload(client, content=b"forged identity attempt", data=forged)
    assert r.status_code == 202, r.text
    doc_id = r.json()["document_id"]

    row = await db_session.execute(
        text("SELECT workspace_id FROM documents WHERE document_id = :d"), {"d": doc_id}
    )
    # 用 A 区身份上传、body 自称 B 区 —— 必须落在 A 区。
    assert str(row.scalar_one()) == "11111111-1111-1111-1111-111111111111"


async def test_cross_workspace_document_is_404_not_403(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    """B 区拿 A 区的 document_id 取详情 → 404。

    刻意不是 403：403 等于承认"这个 id 存在，只是你没权限"，
    那它就成了一个可以逐个试探的枚举接口。
    """
    r = await _upload(client, filename="a.txt", content=b"workspace a secret")
    doc_id = r.json()["document_id"]

    r2 = await client.get(f"/api/v1/documents/{doc_id}", headers=bearer(WORKSPACE_B))
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


async def test_alg_none_token_is_rejected(client: httpx.AsyncClient) -> None:
    """`alg: none` 伪造。解码器固定 HS256，不看头部自报算法。"""
    token = make_token(sub=WORKSPACE_B, workspace_id=WORKSPACE_B, alg="none")
    r = await client.get(
        f"/api/v1/documents/{MISSING_ID}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "unsupported alg"


async def test_expired_token_is_rejected(client: httpx.AsyncClient) -> None:
    token = make_token(sub=WORKSPACE_B, workspace_id=WORKSPACE_B, exp_offset=-10)
    r = await client.get(
        f"/api/v1/documents/{MISSING_ID}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "token expired"


async def test_bad_signature_is_rejected(client: httpx.AsyncClient) -> None:
    token = make_token(sub=WORKSPACE_B, workspace_id=WORKSPACE_B, secret="wrong")
    r = await client.get(
        f"/api/v1/documents/{MISSING_ID}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "bad signature"


async def test_malformed_token_is_rejected(client: httpx.AsyncClient) -> None:
    r = await client.get(
        f"/api/v1/documents/{MISSING_ID}", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "malformed token"


async def test_sub_must_be_uuid(client: httpx.AsyncClient) -> None:
    token = make_token(sub="admin", workspace_id=WORKSPACE_B)
    r = await client.get(
        f"/api/v1/documents/{MISSING_ID}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "sub is not a uuid"


# ---------------------------------------------------------------- 上传三分支


async def test_upload_unsupported_format_is_422(client: httpx.AsyncClient) -> None:
    r = await _upload(client, filename="malware.exe", content=b"MZ\x90\x00")
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "UNSUPPORTED_FORMAT"
    # details 给出可用格式，但不回显内容或路径。
    assert ".pdf" in body["details"]["supported"]


async def test_upload_twice_is_200_deduped_and_no_new_job(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    payload = "重复上传同一份内容。".encode()
    r1 = await _upload(client, filename="same.txt", content=payload)
    assert r1.status_code == 202
    assert r1.json()["deduped"] is False
    assert r1.json()["job_id"]

    r2 = await _upload(client, filename="same.txt", content=payload)
    # 第二次必须 200 而不是 202：没有新作业，前端不该去轮询。
    assert r2.status_code == 200, r2.text
    assert r2.json()["deduped"] is True
    assert r2.json()["job_id"] is None
    assert r2.json()["document_id"] == r1.json()["document_id"]

    row = await db_session.execute(
        text("SELECT count(*) FROM ingestion_jobs WHERE workspace_id = :w"),
        {"w": "11111111-1111-1111-1111-111111111111"},
    )
    assert row.scalar_one() == 1


async def test_upload_changed_content_creates_new_version(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    r1 = await _upload(client, filename="v.txt", content="第一版内容".encode())
    assert r1.status_code == 202
    v1 = r1.json()["document_version_id"]

    r2 = await _upload(client, filename="v.txt", content="第二版内容".encode())
    assert r2.status_code == 202
    assert r2.json()["deduped"] is False
    assert r2.json()["document_id"] == r1.json()["document_id"]
    # 新版本 id 必须与 v1 不同，否则"不可变快照"这个前提就破了。
    assert r2.json()["document_version_id"] != v1


async def test_upload_back_to_old_content_after_change(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    """A→B→A 第三次上传必须判成"无变更"并复用 v1。

    如果内容哈希存在 `documents` 上而不是 `document_versions`，这里会判成
    "有变更"再建一个版本——因为文档行上的 hash 还是 B 的。
    """
    a = "版本 A 的内容".encode()
    r1 = await _upload(client, filename="cycle.txt", content=a)
    v1 = r1.json()["document_version_id"]
    await _upload(client, filename="cycle.txt", content="版本 B 的内容".encode())
    r3 = await _upload(client, filename="cycle.txt", content=a)

    assert r3.status_code == 200
    assert r3.json()["deduped"] is True
    assert r3.json()["document_version_id"] == v1


async def test_upload_to_unknown_kb_is_404(client: httpx.AsyncClient, clean_db: None) -> None:
    other_kb = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    r = await client.post(
        f"/api/v1/knowledge-bases/{other_kb}/documents",
        headers=bearer(),
        files={"file": ("x.txt", b"data", "text/plain")},
    )
    assert r.status_code == 404


async def test_upload_without_kb_repo_is_503(clean_db: None) -> None:
    """依赖没配时报 503 而不是崩 500——错误定位靠这个区分。"""
    from pyknora.api.main import configure, create_app
    from pyknora.services.ingest.embedder import HashingEmbedder

    configure(kb_repo=None, embedder=HashingEmbedder(dim=512))
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            f"/api/v1/knowledge-bases/{KB_ID}/documents",
            headers=bearer(),
            files={"file": ("x.txt", b"data", "text/plain")},
        )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


async def test_upload_oversized_is_422(
    client: httpx.AsyncClient, clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pyknora.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "max_upload_bytes", 10)
    r = await _upload(client, filename="big.txt", content=b"x" * 100)
    assert r.status_code == 422
    assert r.json()["error"]["details"]["max_bytes"] == 10


async def test_idempotency_key_replay_returns_deduped_not_new_job(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """同 `Idempotency-Key` 同内容重放 → 200 deduped，作业数不变。

    注意走 HTTP 的重放**永远先命中 hash 去重**（内容相同），所以这里断言的是
    `deduped=true` + `job_id=None`，而不是"返回同一个 job_id"。
    真正的同 key 同参复用要绕过去重才碰得到，那条在
    `tests/integration/test_ingestion.py` 里直接调 `queue.enqueue` 验。
    """
    headers = {**bearer(), "Idempotency-Key": "fixed-key-001"}
    r1 = await _upload(client, filename="idem.txt", content=b"idempotent content", headers=headers)
    assert r1.status_code == 202
    assert r1.json()["job_id"] is not None

    r2 = await _upload(client, filename="idem.txt", content=b"idempotent content", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["deduped"] is True
    assert r2.json()["job_id"] is None

    row = await db_session.execute(
        text("SELECT count(*) FROM ingestion_jobs WHERE workspace_id = :w"),
        {"w": "11111111-1111-1111-1111-111111111111"},
    )
    assert row.scalar_one() == 1


async def test_idempotency_key_reuse_with_different_params_conflicts(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    """同 key 换了文档 → 409 IDEMPOTENCY_CONFLICT，而不是悄悄共用作业。"""
    headers = {**bearer(), "Idempotency-Key": "fixed-key-002"}
    await _upload(client, filename="k1.txt", content=b"first doc", headers=headers)
    r2 = await _upload(client, filename="k2.txt", content=b"second doc", headers=headers)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


# ---------------------------------------------------------------- 详情与切片


async def test_document_detail_before_index_is_not_searchable(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    """刚上传、Worker 还没跑 → `searchable=false`。

    前端靠这个布尔决定要不要显示"处理中"，不必自己推断
    `active_build_id is not None`（那个推断迟早有人写错）。
    """
    r = await _upload(client, filename="d.txt", content=b"some content here")
    doc_id = r.json()["document_id"]

    d = await client.get(f"/api/v1/documents/{doc_id}", headers=bearer())
    assert d.status_code == 200
    body = d.json()
    assert body["searchable"] is False
    assert body["active_build_id"] is None
    assert body["active_index_version"] is None
    # 新文档 generation/row_version 从 1 起。
    assert body["generation"] == 1
    assert body["row_version"] == 1


async def test_chunks_of_unknown_document_is_404(client: httpx.AsyncClient, clean_db: None) -> None:
    """文档不存在 vs 文档没有切片，必须能区分——都回空列表就是不能区分。"""
    r = await client.get(
        "/api/v1/documents/00000000-0000-0000-0000-000000000009/chunks", headers=bearer()
    )
    assert r.status_code == 404


async def test_chunks_of_fresh_document_is_empty_list(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    """文档存在但还没索引 → 200 + 空列表（不是 404，也不是 500）。"""
    r = await _upload(client, filename="fresh.txt", content=b"not indexed yet")
    doc_id = r.json()["document_id"]

    c = await client.get(f"/api/v1/documents/{doc_id}/chunks", headers=bearer())
    assert c.status_code == 200
    assert c.json()["items"] == []
    assert c.json()["total"] == 0


# ---------------------------------------------------------------- 删除


async def test_delete_returns_202_and_hides_immediately(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    r = await _upload(client, filename="del.txt", content=b"to be deleted")
    doc_id = r.json()["document_id"]

    d = await client.delete(f"/api/v1/documents/{doc_id}", headers=bearer())
    assert d.status_code == 202
    assert d.json()["job_id"]

    # 可见性立刻断开（不等 purge 跑完）：再查就是 404。
    assert (await client.get(f"/api/v1/documents/{doc_id}", headers=bearer())).status_code == 404


async def test_delete_twice_is_404(client: httpx.AsyncClient, clean_db: None) -> None:
    r = await _upload(client, filename="twice.txt", content=b"delete me twice")
    doc_id = r.json()["document_id"]
    assert (await client.delete(f"/api/v1/documents/{doc_id}", headers=bearer())).status_code == 202
    assert (await client.delete(f"/api/v1/documents/{doc_id}", headers=bearer())).status_code == 404


async def test_delete_enqueues_purge_job(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """删除必须入队 purge，否则 chunk 永远留着。"""
    r = await _upload(client, filename="purge.txt", content=b"purge me")
    doc_id = r.json()["document_id"]
    await client.delete(f"/api/v1/documents/{doc_id}", headers=bearer())

    row = await db_session.execute(
        text("SELECT count(*) FROM ingestion_jobs WHERE job_type = 'purge' AND document_id = :d"),
        {"d": doc_id},
    )
    assert row.scalar_one() == 1


# ---------------------------------------------------------------- 作业


async def test_job_detail_and_terminal_flag(client: httpx.AsyncClient, clean_db: None) -> None:
    r = await _upload(client, filename="j.txt", content=b"job status check")
    job_id = r.json()["job_id"]

    j = await client.get(f"/api/v1/jobs/{job_id}", headers=bearer())
    assert j.status_code == 200
    body = j.json()
    assert body["status"] == "pending"
    assert body["terminal"] is False
    assert body["job_type"] == "ingest"
    assert body["attempt"] == 0


async def test_cancel_pending_job(client: httpx.AsyncClient, clean_db: None) -> None:
    r = await _upload(client, filename="c.txt", content=b"cancel me")
    job_id = r.json()["job_id"]

    c = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=bearer())
    assert c.status_code == 200
    assert c.json()["status"] == "cancelled"
    assert c.json()["terminal"] is True


async def test_cancel_terminal_job_is_noop_not_error(
    client: httpx.AsyncClient, clean_db: None
) -> None:
    r = await _upload(client, filename="n.txt", content=b"cancel twice")
    job_id = r.json()["job_id"]
    await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=bearer())

    again = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=bearer())
    # 幂等：在轮询里顺手取消不该报错。
    assert again.status_code == 200
    assert again.json()["status"] == "cancelled"


async def test_cross_workspace_job_is_404(client: httpx.AsyncClient, clean_db: None) -> None:
    r = await _upload(client, filename="x.txt", content=b"cross job")
    job_id = r.json()["job_id"]
    got = await client.get(f"/api/v1/jobs/{job_id}", headers=bearer(WORKSPACE_B))
    assert got.status_code == 404


async def test_unknown_job_is_404(client: httpx.AsyncClient) -> None:
    r = await client.get(f"/api/v1/jobs/{MISSING_ID}", headers=bearer())
    assert r.status_code == 404


# ---------------------------------------------------------------- 元信息


async def test_health_reports_configured_deps(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["kb_repo"] == "FakeKbRepo"
    assert body["embedder"] == "test/hashing"


async def test_openapi_covers_all_member2_endpoints(client: httpx.AsyncClient) -> None:
    """钉住成员2 的交付面。少一个端点就是少一份契约。"""
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert "/api/v1/knowledge-bases/{kb_id}/documents" in paths
    assert "/api/v1/documents/{document_id}" in paths
    assert "/api/v1/documents/{document_id}/chunks" in paths
    assert "/api/v1/jobs/{job_id}" in paths
    assert "/api/v1/jobs/{job_id}/cancel" in paths


async def test_unhandled_exception_does_not_leak_details(
    lenient_client: httpx.AsyncClient, clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未捕获异常 → 500 INTERNAL_ERROR，details 为空。

    挡的是"把堆栈或 SQL 原文塞进响应体"这类事故。
    """
    from pyknora.api.v1 import jobs as jobs_mod

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret SQL: SELECT * FROM users")

    monkeypatch.setattr(jobs_mod.queue, "get_job", _boom)
    r = await lenient_client.get(f"/api/v1/jobs/{MISSING_ID}", headers=bearer())
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["details"] == {}
    assert "secret SQL" not in r.text


async def test_request_id_header_present_on_success(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.headers["x-request-id"]
    # 每个请求一个不同 ID：相同的就说明中间件没跑或用了常量。
    r2 = await client.get("/health")
    assert r2.headers["x-request-id"] != r.headers["x-request-id"]


async def test_openapi_schema_is_valid_json(client: httpx.AsyncClient) -> None:
    """OpenAPI 能序列化，本身就能挡住"响应模型里塞了不可序列化类型"这类问题。"""
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    spec = json.loads(r.text)
    assert spec["info"]["title"] == "PyKnora"


def test_kb_id_is_uuid() -> None:
    """夹具里的 KB_ID 必须真的是 UUID，防止改常量时手滑。"""
    assert isinstance(KB_ID, UUID)
