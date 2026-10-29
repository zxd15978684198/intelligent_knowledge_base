"""来源追踪（任务8）：把版本快照聚成一条版本链路。

**纯函数，不碰数据库**：读由 C2 的 `DocumentVersionRepo.list_versions` 提供，
实现是 `db/repos/lineage.py`，入口是 `api/v1/lineage.py`。
这样本模块的单测不需要 PG。

两条不变量：
1. 只允许出现 `document_id` 指定文档的版本——混入别人的版本是越权，必须报错
   而不是静默过滤（静默过滤会把 bug 变成"看起来正常"的响应）。
2. 顺序只看 `created_at`；时间相同的用 version_id 兜底，保证同一份数据
   每次读出来顺序一致（否则翻页/对账会出现假差异）。
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from pyknora.core.errors import AppError, ErrorCode
from pyknora.schemas.lineage import DocumentLineage, DocumentVersion, LineageEntry

# 来源标签的规范取值。写入方（上传、重建、迁库）必须先过 normalize_ingestion_source，
# 否则 "upload" / "Upload" / "uploaded" 会在聚合统计里变成三种来源。
ALLOWED_INGESTION_SOURCES = frozenset({"upload", "reindex", "import", "migration"})


def normalize_ingestion_source(raw: str) -> str:
    """写入前归一化来源标签；白名单外一律拒绝，避免来源字段退化成自由文本。"""
    value = (raw or "").strip().lower()
    if value not in ALLOWED_INGESTION_SOURCES:
        raise AppError(ErrorCode.INVALID_REQUEST, f"unsupported ingestion source: {raw!r}")
    return value


def build_lineage(
    *,
    document_id: UUID,
    versions: Iterable[DocumentVersion],
    active_version_id: UUID | None = None,
) -> DocumentLineage:
    """聚合成版本链路。

    `active_version_id` 直接取自 `documents.active_document_version_id`。
    若它不在 `versions` 里（指针指向了已不在列表中的版本），不会凭空造一条
    `is_active=True`：如实返回，由调用方判断，读路径不因此报 500。
    """
    ordered = sorted(versions, key=lambda v: (v.created_at, str(v.document_version_id)))

    if any(v.document_id != document_id for v in ordered):
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "lineage contains versions of another document",
        )

    entries = tuple(
        LineageEntry(
            **version.model_dump(),
            ordinal=ordinal,
            is_active=version.document_version_id == active_version_id,
        )
        for ordinal, version in enumerate(ordered, start=1)
    )

    return DocumentLineage(
        document_id=document_id,
        active_version_id=active_version_id,
        contributors=tuple(dict.fromkeys(v.created_by_principal_id for v in entries)),
        versions=entries,
    )
