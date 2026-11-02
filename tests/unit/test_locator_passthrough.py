"""`locator` 从数据库列一路透到引用（N6b）的读侧契约。

为什么单独一条：`locator` 是**结构化位置**（`{"kind":"page","page":3}` 这种），
而 `locator_ref` 是切分层用的文本偏移句柄（`offsets:120-380`）。两者长得像、
名字像，但一个给前端跳转、一个给切分器回拼。任何一层把它们搞混或漏传，
表现都是"接口能通、引用点不过去"——单元测试里只能靠这条钉住。

这里不连数据库：`_to_active_chunk` / `_to_scored_chunk` 是纯映射函数，
用 `SimpleNamespace` 造一行就够了；真库那一段由
`tests/integration/test_vision_ingest.py` 覆盖（扫描件 → 落库 → 视图读回）。
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pyknora.db.repos.reads import _to_active_chunk, _to_scored_chunk
from pyknora.schemas.chat import Citation
from pyknora.schemas.document import ActiveChunk, ScoredChunk
from pyknora.schemas.locator import PageLocator, RegionLocator

PAGE_LOCATOR = {"kind": "page", "page": 3, "heading": "报销流程"}
REGION_LOCATOR = {
    "kind": "region",
    "page": 1,
    "bbox": [21.24, 30.24, 184.68, 47.88],
    "inferred": False,
}


def _row(**overrides: object) -> SimpleNamespace:
    """造一行"视图行"：字段名与 `v_active_chunks` 的列名一致。"""
    base: dict[str, object] = {
        "chunk_id": uuid4(),
        "workspace_id": uuid4(),
        "kb_id": uuid4(),
        "document_id": uuid4(),
        "build_id": uuid4(),
        "index_version": 1,
        "attempt_id": uuid4(),
        "chunk_ordinal": 0,
        "content": "星桥科技费用报销填写说明",
        "page_no": 3,
        "page_end_no": 3,
        "heading_path": "报销流程",
        "char_start": 0,
        "char_end": 13,
        "token_count": 6,
        "locator_ref": "offsets:0-13",
        "locator": PAGE_LOCATOR,
        "embedding_model_key": "test/hashing",
        "embedding_dim": 512,
        "chunk_size": 512,
        "active_document_version_id": uuid4(),
        "distance": 0.25,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_active_chunk_carries_locator() -> None:
    """`list_active_chunks` / `get_chunks` 走的映射必须带出 locator。"""
    chunk = _to_active_chunk(_row())

    assert isinstance(chunk, ActiveChunk)
    assert isinstance(chunk.locator, PageLocator)
    assert chunk.locator.page == 3
    # 两者并存，互不取代（ADR 0008 D2a）：改一个不该动另一个。
    assert chunk.locator_ref == "offsets:0-13"


def test_scored_chunk_carries_region_locator() -> None:
    """检索结果同样要带 locator——扫描件引用只能靠 bbox 回指。"""
    chunk = _to_scored_chunk(_row(locator=REGION_LOCATOR, distance=0.5))

    assert isinstance(chunk, ScoredChunk)
    assert isinstance(chunk.locator, RegionLocator)
    assert chunk.locator.page == 1
    assert chunk.locator.bbox == (21.24, 30.24, 184.68, 47.88)


def test_mapping_accepts_null_locator() -> None:
    """老数据 / RTF 这类 B 档解析结果没有位置，必须是 None 而不是编一个。"""
    chunk = _to_active_chunk(_row(locator=None, locator_ref=None))

    assert chunk.locator is None
    assert chunk.locator_ref is None


def test_mapping_rejects_unknown_kind() -> None:
    """`locator` 是 discriminated union：脏 kind 必须在读的时候就炸。

    如果这一层只当普通 dict 透传，非法值会一路流到前端渲染时才炸，
    那时已经分不清是哪一层写坏的。
    """
    with pytest.raises(ValidationError):
        _to_active_chunk(_row(locator={"kind": "telepathy", "page": 1}))


def test_citation_carries_locator_and_serialises_kind() -> None:
    """引用里的 locator 要能原样进 JSON，前端按 `kind` 分支渲染。"""
    citation = Citation(
        chunk_id=uuid4(),
        document_id=uuid4(),
        page_no=1,
        snippet="星桥科技费用报销填写说明",
        locator=REGION_LOCATOR,
    )

    payload = citation.model_dump(mode="json")

    assert payload["locator"]["kind"] == "region"
    assert payload["locator"]["bbox"] == [21.24, 30.24, 184.68, 47.88]


def test_citation_locator_defaults_to_none() -> None:
    """没有位置时字段存在且为 None——不能让前端去猜"缺字段"是什么意思。"""
    citation = Citation(chunk_id=uuid4(), document_id=uuid4(), snippet="纯文本片段")

    assert "locator" in Citation.model_fields
    assert citation.locator is None
