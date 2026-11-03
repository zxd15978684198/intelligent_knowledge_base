"""表格块整链入库：.xlsx → 解析出 `SheetLocator` → 切分 → 落库 → 视图/读路径可见。

这条用例补的是 `IMPLEMENTATION_STATUS.md` 里最后一条挂着的「未验证」：表格块的
`locator` 此前只有单元测试覆盖（内存对象），没有真跑过「上传 → 作业跑完 → 库里
`chunks.locator.kind == "sheet"` 且 `cells` 是真的单元格范围」。

为什么值得单独一条集成用例：表格块走的是 D3 的**受控例外**——
`is_tabular=True` 的块不进 `full_text`、`page_no`/`heading_path` 让路为 NULL、
`char_start`/`char_end` 是**块内**偏移。这几条任何一条在切分层被改错，单元测试用
内存对象看不出来；而 `locator` 是表格块**唯一**的页/单元格定位来源，写路径漏传就是
「接口能通、引用点不过去」。
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from sqlalchemy import text

from pyknora.core.scope import WORKSPACE_A
from pyknora.db.repos.reads import SqlChunkRepo
from pyknora.services.ingest import documents as docs
from pyknora.workers import ingest_worker

# 工作表名。刻意显式设置而不是依赖 openpyxl 的默认值——默认名在不同版本里并不稳定
# （实测本机是 `Sheet`，而老版本/文档里常写 `Sheet1`），断言跟着默认值走会变成
# 一次升级就红。常量同时给夹具与断言用，改一处即可。
SHEET = "Sheet1"
CELLS = "A1:B3"


def _xlsx() -> bytes:
    """造一份两列三行的 .xlsx（表头 + 两行数据）。

    用内存里的 `BytesIO` 而不是仓库夹具：几十字节的生成成本远低于入库一个二进制，
    而 `openpyxl` 本来就是运行依赖（`parsers/tabular.py` 用它读 xlsx）。
    """
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws["A1"], ws["B1"] = "项目", "金额"
    ws["A2"], ws["B2"] = "差旅费", 1200
    ws["A3"], ws["B3"] = "办公用品", 800
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def test_xlsx_table_block_ingests_with_sheet_locator(
    session, scope_a, kb_repo, embedder, kb_id
):
    up = await docs.upload_document(
        session,
        scope=scope_a,
        kb_repo=kb_repo,
        kb_id=kb_id,
        filename="表格.xlsx",
        data=_xlsx(),
    )
    await session.commit()

    res = await ingest_worker.run_once(
        session, worker_id="w-tab", kb_repo=kb_repo, embedder=embedder
    )
    await session.commit()

    assert res is not None, "没有领到作业（上传没有入队？）"
    assert res.status == "succeeded", f"作业没有成功：{res!r}"

    rows = (
        (
            await session.execute(
                text(
                    """
                    SELECT c.page_no, c.page_end_no, c.heading_path,
                           c.char_start, c.char_end, c.locator_ref, c.locator, c.content
                    FROM v_active_chunks c
                    WHERE c.workspace_id = :ws AND c.document_id = :doc
                    ORDER BY c.chunk_ordinal
                    """
                ),
                {"ws": WORKSPACE_A, "doc": up.document_id},
            )
        )
        .mappings()
        .all()
    )

    assert rows, "xlsx 没有产出任何 chunk（表格解析没接进流水线？）"

    # D3-2：表格块让路的是页码与标题路径，不是定位能力。
    assert all(r["page_no"] is None and r["page_end_no"] is None for r in rows), (
        "表格块没有真实页码，page_no/page_end_no 必须为 NULL（D3-2）；"
        f"实际 {[r['page_no'] for r in rows][:5]}"
    )
    assert all(r["heading_path"] is None for r in rows), (
        "表格块不在标题层级里，heading_path 应为 NULL；"
        f"实际 {[r['heading_path'] for r in rows][:5]}"
    )

    # 0014 + N6b：结构化定位必须一路落到库里，且是**真单元格范围**而不是编造的。
    assert all(r["locator"] is not None for r in rows), (
        "表格块的 locator 没有落库（0014 未应用，或 splitter 没给表格块传 locator）；"
        f"实际 {[r['locator'] for r in rows][:3]}"
    )
    assert all(r["locator"]["kind"] == "sheet" for r in rows), (
        f"表格块的 locator kind 应为 sheet，实际 {[r['locator'].get('kind') for r in rows][:5]}"
    )
    assert all(r["locator"]["sheet"] == SHEET for r in rows), (
        f"工作表名不对：{[r['locator'].get('sheet') for r in rows][:5]}"
    )
    assert all(r["locator"]["cells"] == CELLS for r in rows), (
        f"单元格范围不对，期望 {CELLS}：{[r['locator'].get('cells') for r in rows][:5]}"
    )
    assert all(r["locator_ref"] == f"sheet:{SHEET}!{CELLS}" for r in rows), (
        "locator_ref 应与 locator 同源（compact_locator）；"
        f"实际 {[r['locator_ref'] for r in rows][:5]}"
    )

    # I6 例外：表格块的 char_* 是**块内**偏移，0 起、等于内容长度，不是文档全局偏移。
    for r in rows:
        assert r["char_start"] == 0, f"表格块 char_start 应为块内 0，实际 {r['char_start']}"
        assert r["char_end"] == len(r["content"]), (
            f"表格块 char_end 应等于块内长度 {len(r['content'])}，实际 {r['char_end']}"
        )

    body = "\n".join(r["content"] for r in rows)
    assert "差旅费" in body and "办公用品" in body, f"表格正文没进 chunk：{body[:80]!r}"
    assert "\t" in body, "表格块应以制表符渲染（保持列语义），实际没有 \\t"

    # 读路径也要把同一份结构位置带回来（列清单 + 行→模型映射任一处漏掉，
    # 表现都是「库里对、接口空」）。
    chunks = await SqlChunkRepo(session).list_active_chunks(scope_a, up.document_id)
    assert chunks, "读路径没有返回任何切片"
    assert {c.locator.kind for c in chunks if c.locator is not None} == {"sheet"}, (
        f"读回归来的 locator kind 不对：{[c.locator.kind for c in chunks if c.locator][:5]}"
    )
    assert all(c.locator is not None and c.locator.cells == CELLS for c in chunks), (
        f"读路径丢了单元格范围：{[(c.locator.cells if c.locator else None) for c in chunks][:5]}"
    )
