"""C1 接缝闸门的单元测试：KB 的 embedding 配方必须与进程 embedder 一致。

纯函数、不碰数据库，所以放 `tests/unit`。这条对应红线 I7（维度不匹配必须抛错、
绝不截断、绝不静默接受）：一旦放过，`index_builds` 会记下与真实向量来源不符的
元数据，之后用正确维度查询反而查不到任何东西，且没有任何报错。
"""

from __future__ import annotations

import pytest

from pyknora.core.settings import Settings
from pyknora.schemas.kb import EmbeddingProfile
from pyknora.services.ingest.embedder import (
    HASHING_MODEL_KEY,
    EmbeddingDimensionError,
    EmbeddingProfileMismatch,
    build_embedder,
    check_profile_compat,
    resolve_embedding_identity,
)


def _profile(dim: int, model_key: str = "BAAI/bge-small-zh-v1.5") -> EmbeddingProfile:
    return EmbeddingProfile(
        embedding_model_key=model_key,
        embedding_dim=dim,
        chunk_size=800,
        chunk_overlap=100,
    )


def test_compat_passes_when_dims_match() -> None:
    check_profile_compat(_profile(512), dim=512, model_key="BAAI/bge-small-zh-v1.5")


def test_mismatch_raises_and_reports_both_dims() -> None:
    with pytest.raises(EmbeddingDimensionError) as excinfo:
        check_profile_compat(_profile(1024), dim=512, model_key="hashing")
    message = str(excinfo.value)
    assert "expected 1024" in message
    assert "got 512" in message


def test_same_dim_but_different_model_is_rejected() -> None:
    """512 维的模型不止一个：维度相同、模型不同也必须拒。"""
    with pytest.raises(EmbeddingProfileMismatch) as excinfo:
        check_profile_compat(
            _profile(512, "BAAI/bge-small-zh-v1.5"),
            dim=512,
            model_key="test/hashing",
        )
    details = excinfo.value.details
    assert details["field"] == "embedding_model_key"
    assert details["expected"] == "BAAI/bge-small-zh-v1.5"
    assert details["actual"] == "test/hashing"


def test_same_dim_and_model_passes() -> None:
    check_profile_compat(
        _profile(512, "BAAI/bge-small-zh-v1.5"),
        dim=512,
        model_key="BAAI/bge-small-zh-v1.5",
    )


def test_legacy_profile_without_dim_is_tolerated() -> None:
    """老 profile 不带维度时由调用方兜底并告警，不在这里炸。"""

    check_profile_compat(object(), dim=512, model_key="hashing")


def test_resolve_identity_matches_build_embedder_for_hashing() -> None:
    """上传入口拿到的身份必须与真正跑 embedding 的后端一致。

    这是 P0 的真身：`settings.embedding_model_key` 只在 local 后端权威，
    hashing 后端的身份是 `test/hashing`。两处逻辑一旦漂移，上传口会在非 local
    部署下拿 model_key 误伤每一份配方（真库集成测试里已经撞过一次）。
    """
    cfg = Settings(embedding_backend="hashing", embedding_dim=512)
    provider = build_embedder(cfg)
    assert resolve_embedding_identity(cfg) == (provider.model_key, provider.dim)
    assert resolve_embedding_identity(cfg) == (HASHING_MODEL_KEY, 512)


def test_resolve_identity_follows_openai_model_not_embedding_model_key() -> None:
    cfg = Settings(
        embedding_backend="openai",
        embedding_dim=1024,
        openai_embedding_model="text-embedding-3-large",
    )
    assert resolve_embedding_identity(cfg) == ("text-embedding-3-large", 1024)


def test_resolve_identity_uses_profile_model_key_for_local() -> None:
    cfg = Settings(
        embedding_backend="local",
        embedding_dim=512,
        embedding_model_key="BAAI/bge-small-zh-v1.5",
    )
    assert resolve_embedding_identity(cfg) == ("BAAI/bge-small-zh-v1.5", 512)
