"""C1 接缝闸门的单元测试：KB 的 embedding 配方必须与进程 embedder 一致。

纯函数、不碰数据库，所以放 `tests/unit`。这条对应红线 I7（维度不匹配必须抛错、
绝不截断、绝不静默接受）：一旦放过，`index_builds` 会记下与真实向量来源不符的
元数据，之后用正确维度查询反而查不到任何东西，且没有任何报错。
"""

from __future__ import annotations

import pytest

from pyknora.schemas.kb import EmbeddingProfile
from pyknora.services.ingest.embedder import EmbeddingDimensionError, check_profile_compat


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


def test_legacy_profile_without_dim_is_tolerated() -> None:
    """老 profile 不带维度时由调用方兜底并告警，不在这里炸。"""

    check_profile_compat(object(), dim=512, model_key="hashing")
