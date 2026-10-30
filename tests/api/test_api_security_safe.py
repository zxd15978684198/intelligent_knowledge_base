"""SAFE-05/10：跨工作区不可见与"建流前失败"的可执行 API 用例。

- SAFE-05 用 A 区的 token 访问 B 区的知识库（检索 / 问答）与 B 区的文档详情：
  三个入口一律 404，错误体与"资源根本不存在"逐字相同，不泄漏目标资源的任何标识；
- SAFE-10 建流前就能判定的失败（没配 chat key / 成员没有 ``chat.run``）必须返回
  503 / 403 的普通 JSON，**不能**先回 200 ``text/event-stream`` 再把错误塞进流里
  （流一旦开始，状态码就改不了，客户端只会看到连接被掐断）。

本模块用模块级 ``kb_repo`` 覆盖 conftest 的同名夹具：A 区只登记 ``KB_ID``，B 区登记
一个 A 区不可见的 ``FOREIGN_KB_ID``。B 区文档走真实上传接口创建（真实 documents 行），
这样"文档详情"那条 404 验证的是真实资源而不是一个随手编的 UUID。

作者未在本地执行本文件（``tests/api`` 需要真 PG，由 Lead 串行跑）；只保证
``ruff check`` / ``ruff format --check`` 与 ``pytest --collect-only`` 通过。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from pyknora.api.v1 import chat as chat_routes
from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B, Scope
from pyknora.core.settings import settings
from pyknora.db.repos.principals import SqlIdentityRepo
from pyknora.db.repos.reads import FakeKbRepo
from pyknora.schemas.kb import EmbeddingProfile
from pyknora.services.auth.permissions import CHAT_RUN
from tests.api.tokens import KB_ID, bearer, make_token

CHAT_URL = f"/api/v1/knowledge-bases/{KB_ID}/chat"
CHAT_BODY = {"query": "一线城市住宿报销上限是多少？"}

# 只存在于 B 区、A 区从未见过的知识库：A 的 kb_repo 里没有它的登记。
FOREIGN_KB_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
WORKSPACE_A_ID = UUID(str(WORKSPACE_A))


@pytest.fixture
def kb_repo() -> FakeKbRepo:
    """A 区只登记自己的 KB；B 区登记一个 A 区不可见的知识库。"""
    profile = EmbeddingProfile(
        embedding_model_key="test/hashing",
        embedding_dim=512,
        chunk_size=512,
        chunk_overlap=64,
    )
    repo = FakeKbRepo()
    repo.add(Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A), KB_ID, profile)
    repo.add(
        Scope(workspace_id=WORKSPACE_B, principal_id=WORKSPACE_B),
        FOREIGN_KB_ID,
        profile,
    )
    return repo


async def _seed_foreign_kb(db_session: Any) -> None:
    """给 B 区的 FOREIGN_KB_ID 落一行真实 KB（上传时 documents.kb_id 的外键需要它）。"""
    await db_session.execute(
        text(
            """
            INSERT INTO knowledge_bases (
                kb_id, workspace_id, name, embedding_model_key, embedding_dim,
                chunk_size, chunk_overlap, normalize
            ) VALUES (
                :kb_id, :workspace_id, 'B 区私有知识库',
                'test/hashing', 512, 512, 64, true
            )
            """
        ),
        {"kb_id": FOREIGN_KB_ID, "workspace_id": WORKSPACE_B},
    )
    await db_session.commit()


def _as(principal_id: UUID) -> dict[str, str]:
    token = make_token(sub=str(principal_id), workspace_id=str(WORKSPACE_A))
    return {"Authorization": f"Bearer {token}"}


async def _add_member(db_session: Any, role: str = "member") -> UUID:
    """造一个已注册、已加入 A 区的成员，返回 principal_id。"""
    principal_id = uuid4()
    suffix = principal_id.hex[:8]
    await db_session.execute(
        text(
            "INSERT INTO principals (principal_id, username, email, password_hash) "
            "VALUES (:pid, :u, :e, 'test-fixture-not-a-login-hash')"
        ),
        {"pid": principal_id, "u": f"user_{suffix}", "e": f"{suffix}@test.local"},
    )
    await SqlIdentityRepo(db_session).add_member(
        workspace_id=WORKSPACE_A_ID, user_id=principal_id, role=role
    )
    await db_session.commit()
    return principal_id


# --------------------------------------------------- SAFE-05 跨工作区一律 404
async def test_safe_05_cross_workspace_entries_are_indistinguishable_from_missing(
    client: httpx.AsyncClient, clean_db: None, db_session: Any
) -> None:
    """检索 / 问答 / 文档详情三入口：A 看 B 的资源一律 404，且不泄漏任何标识。"""
    await _seed_foreign_kb(db_session)

    # 先由 B 区真实上传一篇文档，拿到一个"确实存在、但 A 区无权"的 document_id。
    upload = await client.post(
        f"/api/v1/knowledge-bases/{FOREIGN_KB_ID}/documents",
        headers=bearer(str(WORKSPACE_B)),
        files={"file": ("b-secret.txt", b"workspace-b secret body", "text/plain")},
    )
    assert upload.status_code == 202, upload.text
    doc_id = upload.json()["document_id"]

    # 反向自证：B 区自己能看见这篇文档，说明 404 不是"资源没建成"。
    own = await client.get(f"/api/v1/documents/{doc_id}", headers=bearer(str(WORKSPACE_B)))
    assert own.status_code == 200, own.text

    a_headers = bearer()
    query = await client.post(
        f"/api/v1/knowledge-bases/{FOREIGN_KB_ID}/query",
        json={"query": "住宿报销上限是多少"},
        headers=a_headers,
    )
    chat = await client.post(
        f"/api/v1/knowledge-bases/{FOREIGN_KB_ID}/chat",
        json={"query": "住宿报销上限是多少"},
        headers=a_headers,
    )
    document = await client.get(f"/api/v1/documents/{doc_id}", headers=a_headers)

    for name, response in (("query", query), ("chat", chat), ("document", document)):
        assert response.status_code == 404, (name, response.status_code, response.text)
        assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND", name
        # 与"目标根本不存在"逐字同形：不带 kb_id / document_id / 片段内容。
        assert str(FOREIGN_KB_ID) not in response.text, name
        assert doc_id not in response.text, name
        assert "workspace-b secret" not in response.text, name
        assert "event:" not in response.text, name

    assert query.json()["error"]["message"] == "knowledge base not found"
    assert chat.json()["error"]["message"] == "knowledge base not found"
    assert document.json()["error"]["message"] == "document not found"
    assert not chat.headers["content-type"].startswith("text/event-stream")


# ------------------------------- SAFE-10 建流前失败：503（没配模型）不落进 SSE 流
async def test_safe_10_missing_chat_key_fails_before_streaming(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没配 chat key：普通 503 JSON，不是 200 的 SSE 流。"""
    monkeypatch.setattr(settings, "chat_api_key", "")

    async def must_not_answer(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("chat model was never configured; stream_answer must not run")

    monkeypatch.setattr(chat_routes, "stream_answer", must_not_answer)

    response = await client.post(CHAT_URL, json=CHAT_BODY, headers=bearer())
    assert response.status_code == 503, response.text
    assert not response.headers["content-type"].startswith("text/event-stream")

    body = response.json()
    assert body["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "chat model" in body["error"]["message"]
    assert "event:" not in response.text


# ----------------------------- SAFE-10 建流前失败：403（无权限）不落进 SSE 流
async def test_safe_10_missing_chat_permission_is_403_before_streaming(
    client: httpx.AsyncClient,
    clean_db: None,
    db_session: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成员被撤掉 ``chat.run``：路由依赖先回 403，不进入流式生成。"""
    monkeypatch.setattr(settings, "chat_api_key", "test-key")

    async def must_not_answer(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("permission gate must run before stream_answer")

    monkeypatch.setattr(chat_routes, "stream_answer", must_not_answer)

    member_id = await _add_member(db_session)
    repo = SqlIdentityRepo(db_session)
    assert await repo.remove_role_grant(role_key="member", permission=CHAT_RUN) is True
    await db_session.commit()

    response = await client.post(CHAT_URL, json=CHAT_BODY, headers=_as(member_id))
    assert response.status_code == 403, response.text
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert CHAT_RUN in response.json()["error"]["message"]
    assert "event:" not in response.text
