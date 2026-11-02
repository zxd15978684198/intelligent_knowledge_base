"""视觉块（扫描件）整链入库：无文本层 PDF → OCR 出块 → 切分 → 落库 → 视图可见。

这条用例补的是 `IMPLEMENTATION_STATUS.md` 里长期挂着的「未验证」项：此前只验到
进程内 `parse()` 能出 44 个块，没有验过「作业真跑完 → `chunks` 表里
`char_start` / `char_end` 为 NULL 且 `locator_ref` 是 `region:<page>`」。

为什么值得单独一条集成用例：视觉块的 `char_*` 是**故意**置 NULL 的（D9-5/D10-5，
位图没有原始字符流），而 `chunks.char_start/char_end` 在建表时是 NOT NULL。
如果哪天有人把列改回非空、或者切分层忘了给视觉块走特例，单元测试可能仍然全绿
（那里用的是内存对象），只有真库写入才会炸——这正是要在这里钉住的原因。
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text

from pyknora.core.scope import WORKSPACE_A
from pyknora.db.repos.reads import SqlChunkRepo
from pyknora.services.ingest import documents as docs
from pyknora.workers import ingest_worker


def _scanned_pdf() -> bytes:
    """造一份**没有文本层**的 PDF（整页就是一张位图），语义上等同扫描件。

    不用真扫描件当夹具：仓库里没有扫描件样本，而 700 KB+ 的二进制会让 clone 变重。
    Pillow 画一张图再存成 PDF，pdfplumber 抽不到任何字符，走的正是扫描件那条分支。
    用英文而非中文：Windows 无 arial 时 `ImageFont.load_default()` 画不出中文，
    而默认位图字体下的英文仍然清晰可识别，用例不会因字体缺失而假失败。
    """
    image = Image.new("RGB", (1240, 600), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except OSError:  # pragma: no cover - 无 arial 的机器退到默认字体
        font = ImageFont.load_default()
    draw.text((60, 80), "PyKnora scanned page", fill="black", font=font)
    draw.text((60, 180), "Invoice No 12345", fill="black", font=font)
    draw.text((60, 280), "Total 998 CNY", fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PDF", resolution=200.0)
    return buffer.getvalue()


async def test_scanned_pdf_ingests_with_null_char_offsets(
    session, scope_a, kb_repo, embedder, kb_id
):
    data = _scanned_pdf()

    up = await docs.upload_document(
        session, scope=scope_a, kb_repo=kb_repo, kb_id=kb_id, filename="scan.pdf", data=data
    )
    await session.commit()

    res = await ingest_worker.run_once(
        session, worker_id="w-vis", kb_repo=kb_repo, embedder=embedder
    )
    await session.commit()

    assert res is not None, "没有领到作业（上传没有入队？）"
    assert res.status == "succeeded", f"作业没有成功：{res!r}"

    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT c.char_start, c.char_end, c.locator_ref, c.locator, c.content
                FROM v_active_chunks c
                WHERE c.workspace_id = :ws AND c.document_id = :doc
                """
                ),
                {"ws": WORKSPACE_A, "doc": up.document_id},
            )
        )
        .mappings()
        .all()
    )

    assert rows, "扫描件没有产出任何 chunk（视觉通道没有接进切分链？）"
    assert all(r["char_start"] is None and r["char_end"] is None for r in rows), (
        "视觉块没有原始字符流，char_* 必须为 NULL（D9-5 / D10-5）；"
        f"实际取到 {[r['char_start'] for r in rows][:5]}"
    )
    assert all(r["locator_ref"] and r["locator_ref"].startswith("region:") for r in rows), (
        f"视觉块的 locator_ref 应为 region:<page>，实际 {[r['locator_ref'] for r in rows][:5]}"
    )
    assert any("PyKnora" in r["content"] or "Invoice" in r["content"] for r in rows), (
        f"OCR 没有认出正文：{[r['content'][:40] for r in rows][:5]}"
    )

    # 0014：结构化定位必须一路落到库里。这一条同时钉住两件事——
    # ① 迁移把 `chunks.locator` 加上了（视图第 23 列能看到值）；
    # ② 写路径（`ingest_worker` 构造 `ChunkRow`）没有把 `Chunk.locator` 丢掉。
    # 少任一条，前端就只能靠 `locator_ref` 那个字符串猜位置。
    assert all(r["locator"] is not None for r in rows), (
        "视觉块的 locator 没有落库（0014 未应用，或写路径漏传 locator）；"
        f"实际 {[r['locator'] for r in rows][:3]}"
    )
    assert all(r["locator"]["kind"] == "region" for r in rows), (
        f"视觉块的 locator 应为 region，实际 {[r['locator'].get('kind') for r in rows][:5]}"
    )
    assert all(r["locator"]["page"] == 1 for r in rows), (
        f"这份合成扫描件只有 1 页，page 应为 1，实际 {[r['locator'].get('page') for r in rows][:5]}"
    )

    # N6b：读路径也要把同一份结构位置带回来。上面查的是原始 JSONB（证明"落了库"），
    # 这里走 `SqlChunkRepo`（证明"取得出"）——它的一条 SQL 列清单 + 行→模型映射，
    # 任何一处漏掉 `locator`，表现都是接口能通、引用点不过去，单测里看不见。
    chunks = await SqlChunkRepo(session).list_active_chunks(scope_a, up.document_id)
    assert chunks, "读路径没有返回任何切片"
    assert all(c.locator is not None for c in chunks), (
        f"读路径把 locator 丢了：{[c.locator_ref for c in chunks][:3]}"
    )
    assert {c.locator.kind for c in chunks} == {"region"}, (
        f"读回归来的 locator kind 不对：{[c.locator.kind for c in chunks][:5]}"
    )
