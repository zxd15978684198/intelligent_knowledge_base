"""任务8：版本链路聚合的纯函数语义（不需要数据库）。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.lineage import DocumentVersion
from pyknora.services.lineage.service import (
    build_lineage,
    normalize_ingestion_source,
)

DOC = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
DOC_OTHER = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
ALICE = UUID("11111111-1111-1111-1111-111111111111")
BOB = UUID("22222222-2222-2222-2222-222222222222")

V1 = UUID("a0000000-0000-0000-0000-000000000001")
V2 = UUID("a0000000-0000-0000-0000-000000000002")
V3 = UUID("a0000000-0000-0000-0000-000000000003")

T1 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
T3 = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)


def _version(
    version_id: UUID,
    *,
    creator: UUID = ALICE,
    created_at: datetime = T1,
    document_id: UUID = DOC,
    source: str = "upload",
) -> DocumentVersion:
    """造一条原始版本行（C2 读模型）：ordinal/is_active 由 build_lineage 负责派生。"""
    return DocumentVersion(
        document_version_id=version_id,
        document_id=document_id,
        content_hash=f"hash-{version_id}",
        byte_size=128,
        parse_status="ready",
        created_by_principal_id=creator,
        ingestion_source=source,
        created_at=created_at,
    )


def test_orders_versions_by_created_at() -> None:
    """乱序输入也要按时间升序输出，ordinal 从 1 起。"""
    lineage = build_lineage(
        document_id=DOC,
        versions=[_version(V3, created_at=T3), _version(V1, created_at=T1)],
    )

    assert [v.document_version_id for v in lineage.versions] == [V1, V3]
    assert [v.ordinal for v in lineage.versions] == [1, 2]


def test_marks_only_the_active_version() -> None:
    lineage = build_lineage(
        document_id=DOC,
        versions=[_version(V1, created_at=T1), _version(V2, created_at=T2)],
        active_version_id=V2,
    )

    assert [(v.document_version_id, v.is_active) for v in lineage.versions] == [
        (V1, False),
        (V2, True),
    ]
    assert lineage.active_version_id == V2


def test_contributors_follow_first_appearance_order() -> None:
    """S15 口径：A 传 v1、B 改 v2 后链路里必须出现两个创建者。"""
    lineage = build_lineage(
        document_id=DOC,
        versions=[
            _version(V1, creator=ALICE, created_at=T1),
            _version(V2, creator=BOB, created_at=T2),
            _version(V3, creator=ALICE, created_at=T3),
        ],
        active_version_id=V3,
    )

    assert lineage.contributors == (ALICE, BOB)


def test_missing_active_version_is_not_invented() -> None:
    """指针指向不在列表里的版本时如实返回，不造假一条 is_active。"""
    lineage = build_lineage(
        document_id=DOC,
        versions=[_version(V1, created_at=T1)],
        active_version_id=V2,
    )

    assert lineage.active_version_id == V2
    assert all(v.is_active is False for v in lineage.versions)


def test_rejects_versions_of_another_document() -> None:
    """混入别的文档的版本是越权，必须报错而不是静默过滤掉。"""
    with pytest.raises(AppError) as excinfo:
        build_lineage(
            document_id=DOC,
            versions=[_version(V1), _version(V2, document_id=DOC_OTHER)],
        )

    assert excinfo.value.code == ErrorCode.INTERNAL_ERROR


def test_tie_break_is_deterministic() -> None:
    """created_at 相同（批量导入常见）时顺序必须稳定，否则对账会出现假差异。"""
    first = build_lineage(document_id=DOC, versions=[_version(V1), _version(V2)])
    reversed_input = build_lineage(document_id=DOC, versions=[_version(V2), _version(V1)])

    assert [v.document_version_id for v in first.versions] == [
        v.document_version_id for v in reversed_input.versions
    ]


def test_empty_document_has_empty_lineage() -> None:
    lineage = build_lineage(document_id=DOC, versions=[])

    assert lineage.versions == ()
    assert lineage.contributors == ()
    assert lineage.active_version_id is None


def test_normalize_ingestion_source_accepts_known_tag() -> None:
    assert normalize_ingestion_source("  Upload ") == "upload"
    assert normalize_ingestion_source("REINDEX") == "reindex"


@pytest.mark.parametrize("raw", ["", "   ", "uploaded", "api"])
def test_normalize_ingestion_source_rejects_unknown_tag(raw: str) -> None:
    with pytest.raises(AppError) as excinfo:
        normalize_ingestion_source(raw)

    assert excinfo.value.code == ErrorCode.INVALID_REQUEST
