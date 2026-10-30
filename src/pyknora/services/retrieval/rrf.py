"""Reciprocal Rank Fusion：只融合排名，不直接比较向量距离与 FTS 原始分数。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pyknora.schemas.document import ActiveChunk, ScoredChunk
from pyknora.schemas.query import RetrievedChunk


def reciprocal_rank_fusion(
    vector_results: Sequence[ScoredChunk],
    keyword_results: Sequence[ActiveChunk],
    *,
    limit: int = 10,
    k: int = 60,
) -> list[RetrievedChunk]:
    """按 RRF 分数融合两路已排序结果，重复 chunk 只保留一次。

    RRF 分数为每一路 `1 / (k + rank)` 的和，rank 从 1 开始。相同分数时按
    首次出现顺序稳定排序（先向量结果，再关键词结果）。
    """
    if limit < 1:
        raise ValueError("limit must be greater than zero")
    if k < 1:
        raise ValueError("k must be greater than zero")

    chunks: dict[UUID, ScoredChunk | ActiveChunk] = {}
    scores: dict[UUID, float] = {}
    vector_ranks: dict[UUID, int] = {}
    keyword_ranks: dict[UUID, int] = {}

    for rank, chunk in enumerate(vector_results, start=1):
        chunk_id = chunk.chunk_id
        if chunk_id in vector_ranks:
            continue
        chunks.setdefault(chunk_id, chunk)
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (k + rank)
        vector_ranks[chunk_id] = rank

    for rank, chunk in enumerate(keyword_results, start=1):
        chunk_id = chunk.chunk_id
        if chunk_id in keyword_ranks:
            continue
        chunks.setdefault(chunk_id, chunk)
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (k + rank)
        keyword_ranks[chunk_id] = rank

    ordered_ids = sorted(chunks, key=lambda chunk_id: scores[chunk_id], reverse=True)
    return [
        _to_retrieved_chunk(
            chunks[chunk_id],
            rrf_score=scores[chunk_id],
            vector_rank=vector_ranks.get(chunk_id),
            keyword_rank=keyword_ranks.get(chunk_id),
        )
        for chunk_id in ordered_ids[:limit]
    ]


def _to_retrieved_chunk(
    chunk: ScoredChunk | ActiveChunk,
    *,
    rrf_score: float,
    vector_rank: int | None,
    keyword_rank: int | None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk.chunk_id,
        workspace_id=chunk.workspace_id,
        kb_id=chunk.kb_id,
        document_id=chunk.document_id,
        index_version=chunk.index_version,
        chunk_ordinal=chunk.chunk_ordinal,
        content=chunk.content,
        page_no=chunk.page_no,
        page_end_no=chunk.page_end_no,
        heading_path=chunk.heading_path,
        locator_ref=chunk.locator_ref,
        rrf_score=rrf_score,
        vector_rank=vector_rank,
        keyword_rank=keyword_rank,
    )
