"""替身 repo 的 scope 语义。

这组测试的存在理由：第一版 `FakeKbRepo.get_embedding_profile` 收下 scope 就丢掉，
只做 `self._profiles.get(kb_id)`。于是 B 区能用 A 区的 kb_id 读到 profile，
而所有跨区测试照样通过——测试给出的是**错误的安全感**。

替身漏掉的越权，在真实现里可能同样漏掉（替身通常是照着真实现写的，
或者真实现是照着替身写的）。所以替身本身的权限语义必须单独钉住。
"""

from __future__ import annotations

from uuid import UUID

import pytest

from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B, Scope
from pyknora.db.repos.reads import FakeKbRepo
from pyknora.schemas.kb import EmbeddingProfile

KB_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
KB_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


@pytest.fixture
def profile() -> EmbeddingProfile:
    return EmbeddingProfile(
        embedding_model_key="test/hashing",
        embedding_dim=512,
        chunk_size=512,
        chunk_overlap=64,
        normalize=True,
    )


@pytest.fixture
def scope_a() -> Scope:
    return Scope(workspace_id=WORKSPACE_A, principal_id=WORKSPACE_A)


@pytest.fixture
def scope_b() -> Scope:
    return Scope(workspace_id=WORKSPACE_B, principal_id=WORKSPACE_B)


async def test_same_workspace_hits(scope_a, profile) -> None:
    repo = FakeKbRepo()
    repo.add(scope_a, KB_A, profile)
    assert await repo.get_embedding_profile(scope_a, KB_A) is profile


async def test_cross_workspace_returns_none(scope_a, scope_b, profile) -> None:
    """A 区的 KB，用 B 区的 scope 查，必须拿不到。

    这正是第一版会返回 profile 的那一行。修之前本断言会失败。
    """
    repo = FakeKbRepo()
    repo.add(scope_a, KB_A, profile)
    assert await repo.get_embedding_profile(scope_b, KB_A) is None


async def test_same_kb_id_in_two_workspaces_are_distinct(scope_a, scope_b, profile) -> None:
    """同一个 kb_id 在两个区各自存在时，不能串味。

    kb_id 是调用方传进来的，跨区重复完全合法；用 kb_id 单独做键就会互相覆盖。
    """
    other = EmbeddingProfile(
        embedding_model_key="test/other",
        embedding_dim=1024,
        chunk_size=256,
        chunk_overlap=0,
        normalize=False,
    )
    repo = FakeKbRepo()
    repo.add(scope_a, KB_A, profile)
    repo.add(scope_b, KB_A, other)

    got_a = await repo.get_embedding_profile(scope_a, KB_A)
    got_b = await repo.get_embedding_profile(scope_b, KB_A)
    assert got_a is profile
    assert got_b is other
    assert got_a is not got_b


async def test_unknown_kb_returns_none(scope_a) -> None:
    repo = FakeKbRepo()
    assert await repo.get_embedding_profile(scope_a, KB_B) is None
