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
from typing import Protocol, runtime_checkable

from pyknora.core.scope import Scope
from pyknora.schemas.document import ActiveChunk, ActiveDocument, IndexBuild, ScoredChunk


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
        """按余弦距离取 top-k。**这是成员3 唯一该用的检索入口。**

        语义护栏四条：
        1. scope 必填且进 SQL 谓词，跨区的 chunk 不会被读进本进程。
        2. `query_vector` 的长度必须等于目标库的 `embedding_dim`，不符直接抛
           `EmbeddingDimensionError`——不要让它到 PG 里变成 500。
        3. 库内若存在多个 `embedding_dim`（换过模型且未重建），同样抛错，
           因为这种库的向量不可比，返回任何结果都是错的。
        4. 返回按 `distance` 升序，`distance` 是距离不是相似度。
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

    async def get_embedding_profile(self, scope: Scope, kb_id) -> object | None:
        """返回 EmbeddingProfile。KB 不存在、跨区、已删除，统一返回 None。"""
        ...
