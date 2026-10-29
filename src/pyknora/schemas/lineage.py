"""来源追踪（任务8）：文档版本链路的数据形态。

分两层，别混：
- `DocumentVersion` 是 C2 的**原始读模型**，库里有什么就是什么；
- `LineageEntry` 才是链路里的一条，多出 `ordinal` / `is_active` 两个**读时派生**字段。

`document_versions` 表没有 version 整数列，所以顺序只能由 `created_at` 决定；
`ordinal` 是展示序号（1 起），不是库里的事实。

刻意不含 `object_key`：版本快照的存储定位属于摄取模块，写进对外契约等于把
对象存储的路径结构固化下来。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentVersion(BaseModel):
    """一条不可变版本快照（C2 `DocumentVersionRepo.list_versions` 的返回项）。"""

    model_config = ConfigDict(frozen=True)

    document_version_id: UUID
    document_id: UUID
    content_hash: str
    byte_size: int
    parse_status: str
    created_by_principal_id: UUID
    ingestion_source: str
    created_at: datetime


class LineageEntry(DocumentVersion):
    """链路里的一条：原始版本 + 它在链路里的位置。"""

    ordinal: int
    is_active: bool


class DocumentLineage(BaseModel):
    """一个文档的完整版本链路。

    `contributors` 是按首次出现顺序去重的创建者——A 传 v1、B 改 v2 时它有两项，
    这正是任务8 要证明的"来源可追踪"。`active_version_id` 直接透传 `documents`
    指针，不做推断。
    """

    model_config = ConfigDict(frozen=True)

    document_id: UUID
    active_version_id: UUID | None
    contributors: tuple[UUID, ...]
    versions: tuple[LineageEntry, ...]
