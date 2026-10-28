"""C1：成员1向其他模块提供的知识库读取契约。"""

from __future__ import annotations

from uuid import UUID

from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B, Scope
from pyknora.db.repos.knowledge_bases import SqlKnowledgeBaseRepo
from pyknora.db.repos.reads import FakeKbRepo
from pyknora.schemas.kb import EmbeddingProfile, KnowledgeBase
from pyknora.services.contracts.reads import KbRepo

KB_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
KB_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _profile() -> EmbeddingProfile:
    return EmbeddingProfile(
        embedding_model_key="test/hashing",
        embedding_dim=512,
        chunk_size=512,
        chunk_overlap=64,
        normalize=True,
    )


def test_real_and_fake_repositories_implement_c1() -> None:
    assert isinstance(FakeKbRepo(), KbRepo)
    assert isinstance(SqlKnowledgeBaseRepo(object()), KbRepo)  # type: ignore[arg-type]


async def test_fake_c1_enforces_scope_and_returns_schema() -> None:
    scope_a = Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A)
    scope_b = Scope(workspace_id=WORKSPACE_B, principal_id=WORKSPACE_B)
    repo = FakeKbRepo()
    repo.add(scope_a, KB_A, _profile())
    repo.add(scope_b, KB_B, _profile())

    got = await repo.get(scope_a, KB_A)
    assert isinstance(got, KnowledgeBase)
    assert got.profile.embedding_dim == 512
    assert await repo.get(scope_b, KB_A) is None

    items, has_more = await repo.list(scope_a, limit=20)
    assert [item.kb_id for item in items] == [KB_A]
    assert has_more is False
