"""S10b：向量缓存。

目的：同一段正文在同一模型下不重复调用后端。
缓存键必须含 `embedding_model_key`——否则换模型后会命中旧向量空间的数，
等于把 A 模型的输出当成 B 模型的（PRD 明确禁止混用 embedding 空间）。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.services.ingest.embedder import EmbeddingProvider

logger = logging.getLogger(__name__)


def digest_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class EmbeddingService:
    """带缓存的向量化。后端可替换，缓存层与后端解耦。"""

    def __init__(self, provider: EmbeddingProvider, session: AsyncSession) -> None:
        self._provider = provider
        self._session = session

    @property
    def model_key(self) -> str:
        return self._provider.model_key

    @property
    def dim(self) -> int:
        return self._provider.dim

    async def embed_cached(self, texts: Sequence[str]) -> list[list[float]]:
        """按内容哈希复用向量。返回顺序与入参严格一致。"""
        if not texts:
            return []

        hashes = [digest_content(t) for t in texts]
        unique = {h: t for h, t in zip(hashes, texts, strict=True)}

        cached: dict[str, list[float]] = {}
        if unique:
            rows = await self._session.execute(
                text(
                    """
                    SELECT content_hash, embedding
                    FROM embedding_cache
                    WHERE embedding_model_key = :model_key
                      AND content_hash = ANY(:hashes)
                    """
                ),
                {"model_key": self.model_key, "hashes": list(unique)},
            )
            for row in rows:
                cached[row.content_hash] = _to_float_list(row.embedding)

        missing = [h for h in unique if h not in cached]
        if missing:
            fresh = await self._provider.embed([unique[h] for h in missing])
            for h, vec in zip(missing, fresh, strict=True):
                cached[h] = vec
            await self._store(missing, [cached[h] for h in missing])

        return [cached[h] for h in hashes]

    async def _store(self, hashes: list[str], vectors: list[list[float]]) -> None:
        if not hashes:
            return
        stmt = text(
            """
            INSERT INTO embedding_cache
                (content_hash, embedding_model_key, embedding_dim, embedding, hit_count)
            VALUES (:content_hash, :model_key, :dim, :embedding, 0)
            ON CONFLICT (content_hash, embedding_model_key) DO NOTHING
            """
        )
        payload = [
            {
                "content_hash": h,
                "model_key": self.model_key,
                "dim": len(vec),
                "embedding": _to_pg_vector(vec),
            }
            for h, vec in zip(hashes, vectors, strict=True)
        ]
        await self._session.execute(stmt, payload)

    async def bump_hits(self, texts: Sequence[str]) -> None:
        """命中计数单独更新，避免热路径上的写放大。"""
        if not texts:
            return
        hashes = [digest_content(t) for t in texts]
        await self._session.execute(
            text(
                """
                UPDATE embedding_cache SET hit_count = hit_count + 1
                WHERE embedding_model_key = :model_key AND content_hash = ANY(:hashes)
                """
            ),
            {"model_key": self.model_key, "hashes": hashes},
        )


def _to_float_list(value) -> list[float]:
    """asyncpg 对 pgvector 列返回字符串或已解析列表，两种都要吃得下。"""
    if isinstance(value, str):
        return [float(x) for x in value.strip("[]").split(",")]
    return [float(x) for x in value]


def _to_pg_vector(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"
