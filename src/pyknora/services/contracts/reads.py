"""C2 读取契约：成员3、成员5 通过这几个方法取数，不直接写 SQL。

三条语义红线：
- R1 只返回活动 build 的 chunk（由视图保证）
- R2 拿不到就看"无权限"，不回落到"读到什么算什么"
- R3 scope 是必填第一个参数，没有默认值

**为什么这里有一个向量检索方法**：原先只有三个"按文档取"的方法，成员3 要 top-k
检索就得自己写 SQL 打视图——而自己写 SQL 就意味着自己写 `<=>`，也就意味着
自己决定"维度不符时怎么办"。维度不符在 PG 里是抛错而非返回空，一旦发生在
每请求路径上，现象是"某些 KB 检索 100% 500"。把它收进契约，维度校验就只有一份。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from pyknora.core.scope import Scope
from pyknora.schemas.document import (
    ActiveChunk,
    ActiveDocument,
    DocumentVersionInfo,
    IndexBuild,
    ScoredChunk,
)
from pyknora.schemas.kb import EmbeddingProfile, KnowledgeBase


@runtime_checkable
class DocumentRepo(Protocol):
    async def get_active(self, scope: Scope, document_id) -> ActiveDocument | None:
        """返回活动指针指向的文档状态。已删除、跨区、不存在，三者统一返回 None。"""
        ...


@runtime_checkable
class ChunkRepo(Protocol):
    async def list_active_chunks(
        self, scope: Scope, document_id, *, limit: int = 100, offset: int = 0
    ) -> Sequence[ActiveChunk]:
        """只读活动 build 的 chunk。staging 与 retired 一律不可见。"""
        ...


@runtime_checkable
class ChunkSearchRepo(Protocol):
    async def search_similar(
        self,
        scope: Scope,
        kb_id,
        query_vector: Sequence[float],
        *,
        limit: int = 10,
        document_id=None,
    ) -> Sequence[ScoredChunk]:
        """按余弦距离取 top-k 向量候选；关键词候选使用 `search_keyword`。

        语义护栏四条：
        1. scope 必填且进 SQL 谓词，跨区的 chunk 不会被读进本进程。
        2. `query_vector` 的长度必须等于目标库的 `embedding_dim`，不符直接抛
           `EmbeddingDimensionError`——不要让它到 PG 里变成 500。
        3. 库内若存在多个 `embedding_dim`（换过模型且未重建），同样抛错，
           因为这种库的向量不可比，返回任何结果都是错的。
        4. 返回按 `distance` 升序，`distance` 是距离不是相似度。
        """
        ...

    async def search_keyword(
        self,
        scope: Scope,
        kb_id,
        query_text: str,
        *,
        limit: int = 10,
    ) -> Sequence[ActiveChunk]:
        """按 PostgreSQL 全文检索排名返回活动 chunk。

        scope 与 kb_id 必须在 SQL 中过滤；返回顺序按关键词相关度从高到低。
        中文按当前 `simple` 配置整词匹配，具体分词质量仍需后续评测。
        """
        ...


@runtime_checkable
class IndexBuildRepo(Protocol):
    async def get(self, scope: Scope, build_id) -> IndexBuild | None:
        """按 build_id 取构建记录，用于来源追踪与重建。"""
        ...


@runtime_checkable
class KbRepo(Protocol):
    """C1：成员1 交付。成员2 只调这个，绝不直连 knowledge_bases 表。"""

    async def get(self, scope: Scope, kb_id: UUID) -> KnowledgeBase | None:
        """不存在、跨区、已删除统一返回 None。"""
        ...

    async def list(
        self,
        scope: Scope,
        *,
        limit: int,
        after_created_at: datetime | None = None,
        after_kb_id: UUID | None = None,
    ) -> tuple[Sequence[KnowledgeBase], bool]:
        """稳定键集分页；bool 表示是否仍有下一页。"""
        ...

    async def get_embedding_profile(self, scope: Scope, kb_id: UUID) -> EmbeddingProfile | None:
        """返回 EmbeddingProfile。KB 不存在、跨区、已删除，统一返回 None。"""
        ...


# ---------------------------------------------------------------------------
# C2 追加面（成员2 交付给成员3/5 的只读扩展）
#
# 下面四个 Protocol 是**新增**的，与上面五个既有契约互不影响：不改任何已有签名，
# 只把下游新暴露出的读取需求收进契约，免得它们各自手写 SQL 打 v_active_chunks。
# ---------------------------------------------------------------------------


@runtime_checkable
class ChunkLookupRepo(Protocol):
    async def get_chunks(self, scope: Scope, chunk_ids: Sequence[UUID]) -> Sequence[ActiveChunk]:
        """按 id 批量取**当前活动**版本的切片。

        用于引用二次授权：模型给出的 chunk_id 必须重新查一次活动视图才算数。
        **失效的 id 直接缺席，不补 None、不报错**——调用方要靠"返回集合比请求小"
        发现引用已失效。补占位会把"失效"伪装成"存在但内容为空"。
        """
        ...


@runtime_checkable
class BuildListRepo(Protocol):
    async def list_builds(
        self, scope: Scope, document_id: UUID, *, limit: int = 50, offset: int = 0
    ) -> Sequence[IndexBuild]:
        """一个文档的**全部**构建记录（staging/active/retired），按 created_at 倒序。"""
        ...


@runtime_checkable
class BuildChunkRepo(Protocol):
    async def list_chunks_by_build(
        self, scope: Scope, build_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> Sequence[ActiveChunk]:
        """**历史** build 的切片——即 v_active_chunks 看不到的那部分。

        I1 的受控例外：可见性不再来自 active 指针，而来自
        `scope.workspace_id + 显式 build_id + 文档未删除` 三重谓词。
        实现必须自己保证这三条；调用方不得绕过本方法直接查 chunks 基表。
        顺序按 chunk_ordinal，不做"最新覆盖"推断。

        **为什么和 `BuildListRepo` 分开**：这两个能力落在两个不同的仓储上
        （构建记录在 build 仓储、切片在 chunk 仓储）。合成一个 Protocol 会逼出
        一个只为"凑接口"而存在的适配类——接口应该描述能力，不应该描述拼装方式。
        """
        ...


@runtime_checkable
class DocumentVersionRepo(Protocol):
    async def get_version(self, scope: Scope, version_id: UUID) -> DocumentVersionInfo | None:
        """不可变版本快照的元数据；跨区与不存在统一 None。"""
        ...
