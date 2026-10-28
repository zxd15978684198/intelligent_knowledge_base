"""S10 / A4：Embedding 后端的维度校验。

A4 断言：配置维度与模型输出不符时抛异常，而不是截断。
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from pyknora.core.settings import Settings
from pyknora.services.ingest.embedder import (
    EmbeddingDimensionError,
    HashingEmbedder,
    build_embedder,
)


async def test_hashing_embedder_returns_configured_dim() -> None:
    emb = HashingEmbedder(dim=512)
    vectors = await emb.embed(["a", "b"])
    assert len(vectors) == 2
    assert all(len(v) == 512 for v in vectors)


class _FixedOutputModel:
    """模拟一个真实模型：它输出的维度由权重决定，不受配置影响。

    这正是 A4 要防的场景——配置写 1024，模型只出 512。
    """

    def __init__(self, actual_dim: int) -> None:
        self.model_key = "fixed/model"
        self.dim = actual_dim
        self.actual_dim = actual_dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        from pyknora.services.ingest.embedder import _check_dim

        out = [[0.0] * self.actual_dim for _ in texts]
        _check_dim(out, self.dim, self.model_key)
        return out


async def test_dimension_mismatch_raises_not_truncates() -> None:
    """A4：配置 1024，模型出 512 → 必须抛 EmbeddingDimensionError。

    注意 `_check_dim` 用的是**配置维度**而非模型自报维度，所以这里
    把 dim（配置）设成 1024、actual_dim（实际输出）设成 512。
    """
    model = _FixedOutputModel(actual_dim=512)
    model.dim = 1024  # 配置写错：期望 1024
    with pytest.raises(EmbeddingDimensionError) as exc:
        await model.embed(["一段文本"])
    assert exc.value.details["expected"] == 1024
    assert exc.value.details["actual"] == 512


async def test_build_embedder_respects_explicit_backend() -> None:
    s = Settings(embedding_dim=512)
    emb = build_embedder(s, backend="hashing")
    assert emb.dim == 512
    vectors = await emb.embed(["x"])
    assert len(vectors[0]) == 512


async def test_unknown_backend_raises() -> None:
    s = Settings()
    with pytest.raises(Exception) as exc:
        build_embedder(s, backend="nope")
    assert "unknown embedding backend" in str(exc.value)


async def test_empty_input_returns_empty() -> None:
    emb = HashingEmbedder(dim=8)
    assert await emb.embed([]) == []


async def test_vectors_are_normalized() -> None:
    emb = HashingEmbedder(dim=16)
    (vec,) = await emb.embed(["归一化检查"])
    norm = sum(x * x for x in vec) ** 0.5
    assert abs(norm - 1.0) < 1e-6
