"""schema 级护栏：表间复合外键必须真的在库里拦住脏数据。

这些断言测的不是业务函数，而是 `migrations/0001_init.sql` 里的约束本身——
业务代码怎么重构都不该让它们变绿，它们只认 DDL。

为什么必须用复合外键（带 workspace_id）而不是单列：
单列 FK 只校验"父行存在"，A 区的行可以指向 B 区的父行照样通过；
只有 (workspace_id, 父键) 组合的 FK 才同时校验"存在"与"同区"。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pyknora.core.scope import Scope

FK_CHUNKS_DOC = "chunks_workspace_id_document_id_fkey"
FK_VERSION_DOC = "document_versions_workspace_id_document_id_fkey"
FK_DOCUMENT_KB = "documents_kb_fk"

_SKIP_HINT = (
    "当前库是旧 schema（没有外键）：重建容器后再跑迁移 —— "
    "docker rm -f pyknora-pg && uv run python scripts/migrate.py"
)


async def _fk_present(session, name: str) -> bool:
    count = await session.scalar(
        text("SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conname = :name"),
        {"name": name},
    )
    return bool(count)


async def _insert_document(session, scope: Scope, doc_id, kb_id) -> None:
    await session.execute(
        text(
            """
            INSERT INTO knowledge_bases (
                kb_id, workspace_id, name, embedding_model_key, embedding_dim,
                chunk_size, chunk_overlap, normalize
            ) VALUES (:kb_id, :ws, :name, 'test/hashing', 512, 512, 64, true)
            """
        ),
        {"kb_id": kb_id, "ws": scope.workspace_id, "name": f"约束测试-{kb_id}"},
    )
    await session.execute(
        text(
            """
            INSERT INTO documents (document_id, workspace_id, kb_id)
            VALUES (:doc_id, :ws, :kb_id)
            """
        ),
        {"doc_id": doc_id, "ws": scope.workspace_id, "kb_id": kb_id},
    )
    await session.commit()


async def test_orphan_chunk_is_rejected_by_the_database(session) -> None:
    """chunk 指向不存在的文档 → 数据库拒绝，不能靠应用层补检查。"""
    if not await _fk_present(session, FK_CHUNKS_DOC):
        pytest.skip(_SKIP_HINT)

    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                """
                INSERT INTO chunks
                    (chunk_id, workspace_id, document_id, build_id, index_version,
                     attempt_id, chunk_ordinal, content, content_hash)
                VALUES (gen_random_uuid(), :ws, gen_random_uuid(), gen_random_uuid(),
                        1, gen_random_uuid(), 0, '孤儿切片', 'h')
                """
            ),
            {"ws": uuid4()},
        )


async def test_document_cannot_reference_kb_from_another_workspace(
    session, scope_a, scope_b
) -> None:
    """documents 的新增复合外键必须拒绝跨工作区 KB 引用。"""
    assert await _fk_present(session, FK_DOCUMENT_KB), "成员1的 0002 必须创建 documents_kb_fk"

    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                """
                INSERT INTO documents (document_id, workspace_id, kb_id)
                VALUES (gen_random_uuid(), :workspace_id, :kb_id)
                """
            ),
            {
                "workspace_id": scope_b.workspace_id,
                "kb_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            },
        )


async def test_version_cannot_point_across_workspaces(session, scope_a, scope_b) -> None:
    """A 区的文档配 B 区的 workspace_id → 复合外键拒绝。

    单列 FK 在这里会放行（document_id 确实存在），只有复合键能挡住跨区。
    """
    if not await _fk_present(session, FK_VERSION_DOC):
        pytest.skip(_SKIP_HINT)

    doc_id = uuid4()
    await _insert_document(session, scope_a, doc_id, uuid4())

    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                """
                INSERT INTO document_versions
                    (document_version_id, workspace_id, document_id, content_hash,
                     byte_size, object_key, parse_status, created_by_principal_id,
                     ingestion_source)
                VALUES (gen_random_uuid(), :ws, :doc_id, 'h', 1, 'k', 'parsed',
                        gen_random_uuid(), 'upload')
                """
            ),
            {"ws": scope_b.workspace_id, "doc_id": doc_id},
        )


async def test_restrict_blocks_hard_delete_of_a_documented_row(session, scope_a) -> None:
    """RESTRICT 的意义：还有版本/切片时硬删文档必须失败。

    本项目的删除是软删（deleted_at）+ 异步 purge，本来就**不该**出现硬删；
    这条断言保证"不小心写出硬删"会立刻炸，而不是静默带走审计证据。
    """
    if not await _fk_present(session, FK_VERSION_DOC):
        pytest.skip(_SKIP_HINT)

    doc_id = uuid4()
    await _insert_document(session, scope_a, doc_id, uuid4())
    await session.execute(
        text(
            """
            INSERT INTO document_versions
                (document_version_id, workspace_id, document_id, content_hash,
                 byte_size, object_key, parse_status, created_by_principal_id,
                 ingestion_source)
            VALUES (gen_random_uuid(), :ws, :doc_id, 'h', 1, 'k', 'parsed',
                    gen_random_uuid(), 'upload')
            """
        ),
        {"ws": scope_a.workspace_id, "doc_id": doc_id},
    )
    await session.commit()

    with pytest.raises(IntegrityError):
        await session.execute(
            text("DELETE FROM documents WHERE document_id = :doc_id"), {"doc_id": doc_id}
        )
