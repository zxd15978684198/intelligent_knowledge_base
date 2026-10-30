"""给成员3（检索与 RAG）造跨区隔离的测试数据。

**为什么不直接等摄取链路跑通**：成员3 要验证的第一条断言是「跨区检索返回空」，
这条断言只依赖三张表 + 一个视图（documents / index_builds / chunks / v_active_chunks），
不依赖队列、Worker、发布事务。让检索侧被摄取侧的进度卡住，是纯粹的排队浪费。

**这份数据的证据强度来自「两区正文逐字相同」**：
如果 A 区写"猫"、B 区写"狗"，那「跨区搜不到」只能说明关键词不重合，
权限过滤到底生效没有，一点也没证明。所以本脚本让两个 workspace 拥有
完全相同的 chunk 正文，并且 HashingEmbedder 对相同文本输出相同向量——
也就是说 B 区的向量在数学上与 A 区**完全相等**。此时向量检索只要漏掉
workspace_id 过滤，就必然把对方区的行捞出来。搜不到，才真的证明了隔离。

**幂等的实现方式**：所有主键都由 uuid5 从固定命名空间确定性派生，
所以重复运行得到同一批 id，清理时按这批 id 精确删除即可。
刻意不按 workspace 整体 TRUNCATE：同一个库里还有别人（成员2 的集成测试、
其他 agent）正在写的数据，为了自己幂等就清空整个区是越界行为。
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

# 允许从仓库根以外的地方调用（`python scripts/seed_for_m3.py` 与 `uv run` 都成立）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import bindparam, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B  # noqa: E402
from pyknora.core.settings import settings  # noqa: E402
from pyknora.db.session import dispose_engine, session_scope  # noqa: E402
from pyknora.models.tables import (  # noqa: E402
    ChunkRow,
    DocumentBlobRow,
    DocumentRow,
    DocumentVersionRow,
    IndexBuildRow,
)
from pyknora.services.ingest.embedder import EmbeddingProvider, build_embedder  # noqa: E402

# 每个 workspace 的文档数与每篇的 chunk 数：2 × 3 × 5 = 30 行活动 chunk
DOC_COUNT = 3
CHUNKS_PER_DOC = 5
INDEX_VERSION = 1

# 确定性 id 的命名空间。改这个字符串等于换一批新数据（旧数据会变成孤儿），
# 所以它是本文件唯一的"版本号"。
_NAMESPACE = uuid5(NAMESPACE_URL, "https://pyknora.local/seed-for-m3")

# 三个文档各有自己的锚点词。锚点词在两区出现的位置、次数完全一致，
# 成员3 可以直接拿它当 query 做对照实验：A 区搜 "蓝鲸锚点" 命中 A 的 5 行，
# 换成 B 的 scope 搜同一个词，命中的是 B 里同样的 5 行，两边的 document_id 互不相交。
#
# **锚点词可以截断查，不必整词**：tsvector 由 `pyknora_cjk_bigram` 分词
# （连续汉字切成重叠二元组，见 migrations/0007_cjk_bigram_fts.sql），"银狐锚点"
# 入库后是 `银狐 狐锚 锚点`，所以搜 "银狐"、"锚点"、"银狐锚点" 都能命中，
# 只是打分不同（含完整短语的 chunk 分数更高）。
# 但**单个汉字查不到多字词**：存储侧只产出二元组，没有单字词元，搜 "狐" 匹配不到
# "银狐锚点"。最短有效查询是 2 个汉字。这个边界容易被误读成"权限过滤把结果吃掉了"，
# 排查时先确认查询本身有 2 个以上连续汉字。
_DOC_TOPICS: tuple[tuple[str, str], ...] = (
    ("索引构建与发布事务", "蓝鲸锚点"),
    ("向量召回与重排", "银狐锚点"),
    ("权限隔离与作用域校验", "赤鹿锚点"),
)

# 正文模板。刻意写进「两区逐字相同」这句话本身：后来人看到数据时会先怀疑
# 这里是不是有人手改成不同文本了。
_PARA_TEMPLATE = (
    "{topic}第 {n} 段：围绕「{needle}」的对照样本。"
    "这段正文在 workspace A 与 workspace B 中逐字相同，"
    "因此任何「搜不到」都只能由 workspace_id 过滤解释，"
    "而不会是关键词恰好不重合。"
)


@dataclass(frozen=True, slots=True)
class SeedIds:
    """一份种子文档涉及的全部主键。集中派生，避免各表之间 id 对不上。"""

    workspace_id: UUID
    kb_id: UUID
    document_id: UUID
    document_version_id: UUID
    build_id: UUID
    attempt_id: UUID
    chunk_ids: tuple[UUID, ...]


def _uuid(*parts: str) -> UUID:
    return uuid5(_NAMESPACE, "/".join(parts))


def chunk_texts(doc_index: int) -> tuple[str, ...]:
    """某篇文档的 5 段正文。两区调用同一个函数——这是"正文相同"的物理保证。"""
    topic, needle = _DOC_TOPICS[doc_index]
    return tuple(
        _PARA_TEMPLATE.format(topic=topic, n=ordinal + 1, needle=needle)
        for ordinal in range(CHUNKS_PER_DOC)
    )


def seed_ids(ws_tag: str, workspace_id: UUID, doc_index: int) -> SeedIds:
    return SeedIds(
        workspace_id=workspace_id,
        kb_id=_uuid("kb", ws_tag),
        document_id=_uuid("doc", ws_tag, str(doc_index)),
        document_version_id=_uuid("version", ws_tag, str(doc_index)),
        build_id=_uuid("build", ws_tag, str(doc_index)),
        attempt_id=_uuid("attempt", ws_tag, str(doc_index)),
        chunk_ids=tuple(
            _uuid("chunk", ws_tag, str(doc_index), str(ordinal))
            for ordinal in range(CHUNKS_PER_DOC)
        ),
    )


def all_seed_ids() -> list[SeedIds]:
    out: list[SeedIds] = []
    for ws_tag, workspace_id in (("a", WORKSPACE_A), ("b", WORKSPACE_B)):
        out.extend(seed_ids(ws_tag, workspace_id, i) for i in range(DOC_COUNT))
    return out


def content_hash(data: bytes) -> str:
    """与 `services.ingest.documents.compute_content_hash` 保持同一口径（sha256 of bytes）。"""
    return hashlib.sha256(data).hexdigest()


async def assert_table_dimension(session: AsyncSession) -> None:
    """表的 vector 维度必须等于配置维度。

    这一条必须在写任何数据之前查：pgvector 在 `vector(512)` 列里写入 768 维向量时
    报的是 `expected 512 dimensions, not 768`，信息藏在 asyncpg 的 cause 链里，
    很容易被误读成连接问题。提前查一次，报错就直接指向真正的分歧。
    """
    declared = (
        await session.execute(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
            )
        )
    ).scalar_one_or_none()
    if declared is None:
        raise RuntimeError("chunks 表或 embedding 列不存在，先跑 scripts/migrate.py")
    digits = "".join(ch for ch in str(declared) if ch.isdigit())
    if not digits or int(digits) != settings.embedding_dim:
        raise RuntimeError(
            f"表里的 embedding 维度是 {declared}，但 settings.embedding_dim="
            f"{settings.embedding_dim}。先统一配置再 seed，否则插库必失败。"
        )


async def purge(session: AsyncSession) -> None:
    """按确定性 id 精确删除上一轮的数据。只碰自己造的行。

    每个 workspace 只配自己的那 3 个 document_id。写成
    `workspace_id IN (A,B) AND document_id IN (A的3个 + B的3个)` 会让谓词变成
    两个集合的笛卡尔积，语义上等于"A 区里恰好叫 B 那几个 id 的行也删"——
    幂等上无害，但那个谓词表达的不是作者的意思，后来人读它会以为是 bug。
    """
    for ws_tag, workspace_id in (("a", WORKSPACE_A), ("b", WORKSPACE_B)):
        ids = [seed_ids(ws_tag, workspace_id, i) for i in range(DOC_COUNT)]
        doc_ids = [s.document_id for s in ids]
        version_ids = [s.document_version_id for s in ids]

        statements: tuple[tuple[str, dict[str, object]], ...] = (
            (
                "DELETE FROM chunks WHERE workspace_id = :ws AND document_id IN :docs",
                {"ws": workspace_id, "docs": doc_ids},
            ),
            (
                "DELETE FROM index_builds WHERE workspace_id = :ws AND document_id IN :docs",
                {"ws": workspace_id, "docs": doc_ids},
            ),
            (
                "DELETE FROM document_blobs "
                "WHERE workspace_id = :ws AND document_version_id IN :versions",
                {"ws": workspace_id, "versions": version_ids},
            ),
            (
                "DELETE FROM document_versions WHERE workspace_id = :ws AND document_id IN :docs",
                {"ws": workspace_id, "docs": doc_ids},
            ),
            (
                "DELETE FROM documents WHERE workspace_id = :ws AND document_id IN :docs",
                {"ws": workspace_id, "docs": doc_ids},
            ),
        )
        for sql, params in statements:
            stmt = text(sql)
            for key in ("docs", "versions"):
                if key in params:
                    stmt = stmt.bindparams(bindparam(key, expanding=True))
            await session.execute(stmt, params)
    await session.flush()


async def ensure_knowledge_bases(session: AsyncSession, embedder: EmbeddingProvider) -> None:
    """保证两个 workspace 各有一条种子知识库。

    0002 给 `documents` 加了 `(workspace_id, kb_id) → knowledge_bases` 的复合外键，
    所以父行必须由本脚本自己建：跑成员3 的人不该先手工建库，也不该被别的测试
    留下的知识库"刚好长得对"骗过去。配置五字段与 embedder 保持一致，否则
    `SqlChunkSearchRepo.search_similar` 的维度/模型校验会在检索时才发现不对。
    """
    statement = text(
        "INSERT INTO knowledge_bases ("
        " kb_id, workspace_id, name, embedding_model_key, embedding_dim,"
        " chunk_size, chunk_overlap, normalize"
        ") VALUES ("
        " :kb_id, :workspace_id, :name, :model_key, :dim, :chunk_size, :chunk_overlap, true"
        ") ON CONFLICT (workspace_id, kb_id) DO UPDATE SET"
        " embedding_model_key = EXCLUDED.embedding_model_key,"
        " embedding_dim = EXCLUDED.embedding_dim,"
        " chunk_size = EXCLUDED.chunk_size,"
        " chunk_overlap = EXCLUDED.chunk_overlap,"
        " normalize = EXCLUDED.normalize,"
        " updated_at = now()"
    )
    for ws_tag, workspace_id in (("a", WORKSPACE_A), ("b", WORKSPACE_B)):
        await session.execute(
            statement,
            {
                "kb_id": _uuid("kb", ws_tag),
                "workspace_id": workspace_id,
                "name": f"种子知识库 {ws_tag.upper()}",
                "model_key": embedder.model_key,
                "dim": settings.embedding_dim,
                "chunk_size": settings.chunk_size,
                "chunk_overlap": settings.chunk_overlap,
            },
        )


async def seed_document(
    session: AsyncSession,
    embedder: EmbeddingProvider,
    *,
    ids: SeedIds,
    doc_index: int,
) -> None:
    """插一篇文档的完整足迹：documents / version / blob / build / 5 个 chunk。"""
    texts = chunk_texts(doc_index)
    # 全文 = 各段用空行连接。chunk 的 char_start/char_end 以全文为坐标系，
    # 与 splitter 的约定一致，这样成员3 做 citations 回拼时不会遇到两套偏移语义。
    full_text = "\n\n".join(texts)
    raw = full_text.encode("utf-8")

    vectors = await embedder.embed(list(texts))
    if len(vectors) != CHUNKS_PER_DOC:
        raise RuntimeError(f"embedder 返回 {len(vectors)} 条向量，期望 {CHUNKS_PER_DOC}")
    for vec in vectors:
        # 绝不截断：截断的后果是插库成功、检索全错、且没有任何报错。
        if len(vec) != settings.embedding_dim:
            raise RuntimeError(
                f"向量维度 {len(vec)} != settings.embedding_dim {settings.embedding_dim}；"
                "拒绝截断，请修 embedder 或配置"
            )

    now = datetime.now(UTC)
    topic = _DOC_TOPICS[doc_index][0]

    # 插入顺序必须显式排。这几张表之间**没有 ORM relationship**，SQLAlchemy 的
    # unit of work 于是只能按 mapper 名排序（实测顺序是 chunks → document_blobs →
    # document_versions → documents → index_builds），子行会先于父行撞外键。
    # 依赖链：knowledge_bases → documents → document_versions → document_blobs，
    # 另有 documents → index_builds、documents → chunks。每层 flush 完再写下一层。

    # 三个指针必须一起写。只设 active_build_id 而漏掉 active_index_version，
    # 发布侧的「版本号必须递增」校验会在后续真实摄取时误判。
    session.add(
        DocumentRow(
            document_id=ids.document_id,
            workspace_id=ids.workspace_id,
            kb_id=ids.kb_id,
            desired_document_version_id=ids.document_version_id,
            active_document_version_id=ids.document_version_id,
            active_build_id=ids.build_id,
            active_index_version=INDEX_VERSION,
            generation=1,
            row_version=1,
            deleted_at=None,
        )
    )
    await session.flush()  # version / build / chunks 都要引用这一行

    session.add(
        DocumentVersionRow(
            document_version_id=ids.document_version_id,
            workspace_id=ids.workspace_id,
            document_id=ids.document_id,
            content_hash=content_hash(raw),
            byte_size=len(raw),
            object_key=f"objects/{ids.workspace_id}/{content_hash(raw)}/seed-doc-{doc_index}.txt",
            parse_status="succeeded",
            created_by_principal_id=ids.workspace_id,
            ingestion_source="seed",
        )
    )
    await session.flush()  # document_blobs 引用 version

    session.add(
        DocumentBlobRow(
            workspace_id=ids.workspace_id,
            document_version_id=ids.document_version_id,
            content=raw,
        )
    )
    await session.flush()

    session.add(
        IndexBuildRow(
            build_id=ids.build_id,
            workspace_id=ids.workspace_id,
            document_id=ids.document_id,
            index_version=INDEX_VERSION,
            attempt_id=ids.attempt_id,
            embedding_model_key=embedder.model_key,
            embedding_dim=settings.embedding_dim,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            normalize=True,
            chunk_count=CHUNKS_PER_DOC,
            state="active",
            activated_at=now,
        )
    )
    await session.flush()

    cursor = 0
    for ordinal, content in enumerate(texts):
        start = cursor
        cursor += len(content)
        session.add(
            ChunkRow(
                chunk_id=ids.chunk_ids[ordinal],
                workspace_id=ids.workspace_id,
                document_id=ids.document_id,
                build_id=ids.build_id,
                index_version=INDEX_VERSION,
                attempt_id=ids.attempt_id,
                chunk_ordinal=ordinal,
                content=content,
                content_hash=content_hash(content.encode("utf-8")),
                page_no=None,
                heading_path=topic,
                char_start=start,
                char_end=start + len(content),
                token_count=max(1, len(content) // 2),
                locator_ref=f"offsets:{start}-{start + len(content)}",
                embedding=vectors[ordinal],
            )
        )
        cursor += 2  # 跳过段落之间的空行，保持与 full_text 一致

    await session.flush()

    # tsvector 交给 PG 算，而不是在 Python 里拼字符串：
    # 词法分析规则（分词、大小写归一、停用词）属于数据库配置，抄一份到 Python
    # 里就是在制造第二份真相，两边一旦漂移，全文检索与 GIN 索引就会不一致。
    # 分词必须过 pyknora_cjk_bigram（中文二元组），与 worker/重建索引同一口径。
    await session.execute(
        text(
            "UPDATE chunks SET tsvector = to_tsvector('simple', pyknora_cjk_bigram(content)) "
            "WHERE workspace_id = :workspace_id AND build_id = :build_id"
        ),
        {"workspace_id": ids.workspace_id, "build_id": ids.build_id},
    )


async def verify(session: AsyncSession) -> dict[str, int]:
    """在提交前自检。这里失败说明指针或状态没写对，属于本脚本的 bug。"""
    # 一律限定在自己造的 document_id 上：库里的其他行不归本脚本管，
    # 把它们算进断言只会让失败归因变得含糊。
    doc_ids = [ids.document_id for ids in all_seed_ids()]
    rows = (
        await session.execute(
            text(
                "SELECT workspace_id, count(*) AS n FROM v_active_chunks "
                "WHERE document_id IN :docs "
                "GROUP BY workspace_id ORDER BY workspace_id"
            ).bindparams(bindparam("docs", expanding=True)),
            {"docs": doc_ids},
        )
    ).all()
    per_workspace = {row.workspace_id: row.n for row in rows}
    expected_per_workspace = DOC_COUNT * CHUNKS_PER_DOC

    for workspace_id in (WORKSPACE_A, WORKSPACE_B):
        got = per_workspace.get(workspace_id, 0)
        if got != expected_per_workspace:
            raise RuntimeError(
                f"workspace {workspace_id} 在 v_active_chunks 里有 {got} 行，"
                f"期望 {expected_per_workspace}。多半是 documents.active_build_id "
                "或 index_builds.state 没设成 active"
            )

    # 「两区正文逐字相同」是这个数据集唯一的价值来源，必须被断言而不是被假设。
    # 目前它由 chunk_texts() 被两个 workspace 共用而结构性成立，但那是隐式的：
    # 将来谁给 chunk_texts 加一个 workspace 分支（哪怕出于好意做点"区分度"），
    # 这条证据链就断了，而明面上一切正常——30 行都在、视图计数也对。
    # 所以在这里用 md5 去重数把它钉住：30 个 chunk 只该有 15 种正文。
    distinct_bodies = (
        await session.execute(
            text(
                "SELECT count(DISTINCT md5(content)) FROM chunks WHERE document_id IN :docs"
            ).bindparams(bindparam("docs", expanding=True)),
            {"docs": doc_ids},
        )
    ).scalar_one()
    expected_bodies = DOC_COUNT * CHUNKS_PER_DOC
    if distinct_bodies != expected_bodies:
        raise RuntimeError(
            f"两区正文不再逐字相同：30 个 chunk 里有 {distinct_bodies} 种正文，"
            f"期望 {expected_bodies}。正文一旦分化，「跨区搜不到」就不能再归因于"
            "workspace_id 过滤，只会退化成关键词不重合的巧合。"
        )

    # 跨区必须读不到：拿 A 的 document_id 配 B 的 workspace_id 查，必须 0 行。
    for ids in all_seed_ids():
        other = WORKSPACE_B if ids.workspace_id == WORKSPACE_A else WORKSPACE_A
        leaked = (
            await session.execute(
                text(
                    "SELECT count(*) FROM v_active_chunks "
                    "WHERE workspace_id = :ws AND document_id = :doc"
                ),
                {"ws": other, "doc": ids.document_id},
            )
        ).scalar_one()
        if leaked:
            raise RuntimeError(f"跨区泄漏：{ids.document_id} 在 {other} 下被读到了 {leaked} 行")

    # 只统计自己造的行。库里可能还有别人（集成测试、其他 agent）正在写的数据，
    # 用 `SELECT count(*) FROM v_active_chunks` 会把它们算进来，数字就对不上了。
    total = (
        await session.execute(
            text("SELECT count(*) FROM v_active_chunks WHERE document_id IN :docs").bindparams(
                bindparam("docs", expanding=True)
            ),
            {"docs": doc_ids},
        )
    ).scalar_one()
    expected_total = 2 * DOC_COUNT * CHUNKS_PER_DOC
    if total != expected_total:
        raise RuntimeError(f"自建行数为 {total}，期望 {expected_total}")

    return {"active_total": int(total), "per_workspace": expected_per_workspace}


async def main() -> int:
    # 强制走 hashing 后端：seed 不该依赖网络、权重下载或 API key，
    # 也不该因为 PYKNORA_EMBEDDING_BACKEND 被改而成批失败。
    embedder = build_embedder(settings, backend="hashing")
    if embedder.dim != settings.embedding_dim:
        raise RuntimeError(
            f"embedder 维度 {embedder.dim} 与 settings.embedding_dim "
            f"{settings.embedding_dim} 不一致"
        )

    try:
        async with session_scope() as session:
            await assert_table_dimension(session)
            await ensure_knowledge_bases(session, embedder)
            await purge(session)
            for ws_tag, workspace_id in (("a", WORKSPACE_A), ("b", WORKSPACE_B)):
                for doc_index in range(DOC_COUNT):
                    await seed_document(
                        session,
                        embedder,
                        ids=seed_ids(ws_tag, workspace_id, doc_index),
                        doc_index=doc_index,
                    )
            stats = await verify(session)

        print(
            f"seed ok: {stats['active_total']} chunks, "
            f"workspaces=2 (A={WORKSPACE_A}, B={WORKSPACE_B}), "
            f"docs={2 * DOC_COUNT}, builds={2 * DOC_COUNT}, "
            f"per_workspace={stats['per_workspace']}, "
            f"model={embedder.model_key}, dim={settings.embedding_dim}"
        )
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
