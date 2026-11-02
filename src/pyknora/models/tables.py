"""SQLAlchemy 2.0 映射。七张表，与 migrations/0001_init.sql 一一对应。

约定：UUID 主键；每表带 workspace_id；可变资源有 deleted_at / generation。
视图 v_active_chunks 不映射——它只是查询入口，没有写入语义。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from pyknora.core.settings import settings


class Base(DeclarativeBase):
    pass


class DocumentRow(Base):
    __tablename__ = "documents"

    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kb_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    desired_document_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    active_document_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    active_build_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    active_index_version: Mapped[int | None] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("workspace_id", "document_id"),)


class DocumentVersionRow(Base):
    __tablename__ = "document_versions"

    document_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    ingestion_source: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "document_version_id"),
        Index("document_versions_hash_idx", "workspace_id", "document_id", "content_hash"),
    )


class IndexBuildRow(Base):
    __tablename__ = "index_builds"

    build_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    embedding_model_key: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False)
    normalize: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(16), nullable=False)  # staging|active|retired
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("workspace_id", "build_id"),
        UniqueConstraint("workspace_id", "document_id", "index_version", "attempt_id"),
    )


class ChunkRow(Base):
    __tablename__ = "chunks"

    chunk_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    build_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    chunk_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer)
    # 末字符所在页（P2-D2）：跨页切片 page_no < page_end_no，单页切片两者相等。
    # 保持可空——null 表示"这份文档没有页码概念"（txt/md），与"第 1 页到第 1 页"不同。
    page_end_no: Mapped[int | None] = mapped_column(Integer)
    heading_path: Mapped[str | None] = mapped_column(Text)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int | None] = mapped_column(Integer)
    locator_ref: Mapped[str | None] = mapped_column(Text)
    # 格式无关的定位（0014，ADR 0008 D2/D2a）：`kind` 决定形状，六种见
    # `schemas/locator.py`。与 `locator_ref` **分工并存**，不是它的替代：
    # `locator` 给前端渲染引用（页码 / 工作表+单元格 / 幻灯片号），`locator_ref` 仍是
    # 切分层的文本偏移句柄。可空表示"这份切片从未写入过位置"；历史行由 0014 回填成
    # `{"kind":"para","index":null,"inferred":true}`，所以线上不会有 NULL 之外的空洞。
    locator: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tsvector: Mapped[str | None] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dim))

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "document_id", "index_version", "attempt_id", "chunk_ordinal"
        ),
        Index("chunks_build_idx", "workspace_id", "build_id"),
    )


class EmbeddingCacheRow(Base):
    __tablename__ = "embedding_cache"

    content_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    embedding_model_key: Mapped[str] = mapped_column(Text, primary_key=True)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dim), nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentBlobRow(Base):
    """正文替身表。真实部署是对象存储，DB 只存 object_key。

    单独建表而不是塞进 document_versions，是为了让"换存储"只改
    `workers/ingest_worker._load_bytes` 一个函数。
    """

    __tablename__ = "document_blobs"

    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    document_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class IngestionJobRow(Base):
    __tablename__ = "ingestion_jobs"

    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "job_id"),
        UniqueConstraint("workspace_id", "idempotency_key"),
    )


class JobAttemptRow(Base):
    __tablename__ = "job_attempts"

    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    worker_id: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (UniqueConstraint("workspace_id", "attempt_id"),)


# ---------- 成员4：身份与权限（migrations/0003_identity_acl.sql） ----------
# 追加在文件末尾，不重排上面的类：`__tablename__` 是迁移的镜像，顺序不携带语义，
# 但重排会让别人的 diff 永远冲突。


class PrincipalRow(Base):
    """身份主体。表名不是 `users`——全仓按 principal 口径命名。"""

    __tablename__ = "principals"

    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # 只放 bcrypt 哈希。这个字段永远不进日志、不进响应体。
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeBaseRow(Base):
    """成员1拥有的知识库配置；追加在共享映射文件末尾以减少合并冲突。"""

    __tablename__ = "knowledge_bases"

    kb_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model_key: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False)
    normalize: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active_index_build_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "kb_id"),
        Index(
            "knowledge_bases_active_name_uq",
            "workspace_id",
            func.lower(name),
            unique=True,
            postgresql_where=deleted_at.is_(None),
        ),
    )


class IndexMigrationRow(Base):
    """成员5：重建 / 迁库的记账行（migrations/0004）。追加在共享映射文件末尾。"""

    __tablename__ = "index_migrations"

    # 复合主键 (workspace_id, migration_id)：与 documents / chunks 同口径——
    # 业务主键在 workspace 内唯一，跨区同 id 不算冲突。0004 曾误写成单列主键，
    # 由 migrations/0005_index_migrations_pk.sql 纠正。
    migration_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    kb_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_build_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    target_build_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # export|replay|reindex
    # pending|running|succeeded|failed
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # (workspace_id, migration_id) 的唯一性由复合主键保证，不再重复声明 UNIQUE。
        Index("index_migrations_kb_idx", "workspace_id", "kb_id", "created_at"),
    )


class WorkspaceRow(Base):
    __tablename__ = "workspaces"

    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    # 预留字段，当前**没有任何代码读取**它：可见性一律由 workspace_memberships 决定，
    # 没有"公开空间"语义。写在这里是为了迁移与远端分支的表结构一致，别当成已实现的功能。
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkspaceMembershipRow(Base):
    """用户↔工作区。`user_id` 引用 `principals.principal_id`，见迁移顶部第 2 条注释。"""

    __tablename__ = "workspace_memberships"

    membership_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)


class RoleRow(Base):
    __tablename__ = "roles"

    role_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    role_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)


class RoleGrantRow(Base):
    __tablename__ = "role_grants"

    grant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    role_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    permission: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (UniqueConstraint("role_id", "permission"),)
