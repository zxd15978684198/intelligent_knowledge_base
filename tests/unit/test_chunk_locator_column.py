"""N6a-1：`chunks.locator` 的数据层（0014 迁移 + ORM 列 + chunk 出参 + 迁库清单）。

只验数据层，不碰解析层：把 `Block.locator` 接进 parser/splitter 是后续节点的事，
读路径（`db/repos/reads.py`）与 `schemas/document.py` 也归别的节点。这里钉四件事：

1. `ChunkRow.locator` 真的映射成了可空的 JSONB 列（不是 text、不是非空）；
2. chunk 出参能从 `dict`（JSONB 读出来的形态）解析成对应的 Locator 子模型；
3. `locator=None` 合法（历史行、以及"这份格式没有位置概念"的切片）；
4. 非法 `kind` 被拒——在解析时炸，不留到前端渲染。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import JSONB

from pyknora.models.tables import ChunkRow
from pyknora.schemas.chunk import Chunk
from pyknora.schemas.locator import ParaLocator, SheetLocator
from pyknora.services.migration.snapshot import TABLES, build_snapshot, render_sql

# 一条最小合法的 chunk：只为验证 locator，其余字段取能满足既有不变量的值。
_MINIMAL = {
    "ordinal": 0,
    "content": "第一段正文",
    "char_start": 0,
    "char_end": 5,
}


# --------------------------------------------------------------- ORM 列本身


def test_chunk_row_has_a_nullable_jsonb_locator_column() -> None:
    """`chunks.locator` 必须是 jsonb 且可空。

    非 jsonb（比如 text）会让"按 kind 分支渲染"退化成正则解析；非空会让
    上线前的历史行、以及 0014 第二次执行前的新行直接插入失败。
    """
    column = ChunkRow.__table__.c.locator
    assert isinstance(column.type, JSONB), f"locator 类型是 {column.type!r}，应为 JSONB"
    assert column.nullable is True


def test_locator_and_locator_ref_are_two_columns_not_one() -> None:
    """ADR 0008 D2a：两者分工并存，不许合并成一列（合并必然有一边丢信息）。"""
    columns = set(ChunkRow.__table__.c.keys())
    assert {"locator", "locator_ref"} <= columns


# ------------------------------------------------------------- chunk 出参模型


def test_chunk_parses_sheet_locator_from_plain_dict() -> None:
    """JSONB 读出来就是 dict；出参模型要能直接吃下它并派到 `SheetLocator`。"""
    chunk = Chunk(
        **_MINIMAL,
        locator={"kind": "sheet", "sheet": "Sheet1", "cells": "B2:C4"},
    )
    assert isinstance(chunk.locator, SheetLocator)
    assert chunk.locator.sheet == "Sheet1"
    assert chunk.locator.cells == "B2:C4"
    # `inferred` 缺省为 False：解析器真实读到的位置不是推断值。
    assert chunk.locator.inferred is False


def test_chunk_accepts_the_0014_backfill_shape() -> None:
    """0014 回填的那一行必须能被出参模型解析，否则回填等于制造脏数据。

    `index=null` 是回填的关键：硬填一个具体段落号就是编造位置。
    """
    chunk = Chunk(**_MINIMAL, locator={"kind": "para", "index": None, "inferred": True})
    assert isinstance(chunk.locator, ParaLocator)
    assert chunk.locator.index is None
    assert chunk.locator.inferred is True


def test_chunk_locator_defaults_to_none() -> None:
    """不传 `.locator` 也必须合法——历史行与"无位置概念"的切片都走这条路径。"""
    chunk = Chunk(**_MINIMAL)
    assert chunk.locator is None
    assert chunk.locator_ref is None
    # 显式传 None 与缺省等价（JSONB 的 NULL 读出来就是 None）。
    assert Chunk(**_MINIMAL, locator=None).locator is None


def test_chunk_rejects_unknown_locator_kind() -> None:
    """非法 kind 必须在解析时就抛，不能悄悄降级成 dict 或 None。"""
    with pytest.raises(ValidationError):
        Chunk(**_MINIMAL, locator={"kind": "moon", "page": 1})


def test_chunk_rejects_field_mismatch_for_known_kind() -> None:
    """kind 合法但字段错配（sheet 缺 `sheet`）同样要拒——discriminated union 的意义在这。"""
    with pytest.raises(ValidationError):
        Chunk(**_MINIMAL, locator={"kind": "sheet", "cells": "B2:C4"})


# ------------------------------------------------------------- 迁库列清单


def test_snapshot_chunk_spec_includes_locator() -> None:
    """迁库必须带上 locator：漏了它，恢复后引用只能退回偏移句柄，页码/工作表全丢。"""
    spec = next(item for item in TABLES if item.name == "chunks")
    assert "locator" in spec.columns
    # 是"追加"而不是替换掉 locator_ref。
    assert "locator_ref" in spec.columns


def test_render_sql_emits_locator_as_jsonb_not_python_dict() -> None:
    """`render_sql` 对 jsonb 列必须产出合法 JSON + `::jsonb`。

    `str({"kind": "para"})` 会是 `{'kind': 'para'}`（单引号 + Python 的 `True`），
    那样的 SQL 一执行就被 PG 拒；这条用例把"导出即坏"钉住。
    """
    snapshot = build_snapshot(
        {
            "chunks": [
                {
                    **_MINIMAL,
                    "chunk_id": "c1",
                    "locator": {"kind": "para", "inferred": True},
                }
            ]
        }
    )
    sql_text = render_sql(snapshot)
    insert_line = next(line for line in sql_text.splitlines() if "INSERT INTO chunks" in line)
    assert '"kind": "para"' in insert_line
    assert '"inferred": true' in insert_line
    assert "::jsonb" in insert_line
    # Python 的 dict repr 不能出现：单引号 + 大写 True 都不是 JSON。
    assert "'kind': 'para'" not in insert_line


# --------------------------------------------------------------- 迁移文件本身

_MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "0014_chunk_locator.sql"


def test_migration_0014_appends_locator_and_backfills_honestly() -> None:
    """迁移文本的硬断言：只加列、回填带 `inferred`、视图重建。

    `index: null` 是回填的诚实之处：不知道段落号就写 null，不编一个数字。
    """
    sql_text = _MIGRATION.read_text(encoding="utf-8")
    assert "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS locator jsonb;" in sql_text
    assert '"kind":"para","index":null,"inferred":true' in sql_text
    assert "CREATE OR REPLACE VIEW v_active_chunks" in sql_text


def test_migration_0014_puts_locator_last_in_the_view() -> None:
    """`CREATE OR REPLACE VIEW` 按位置比对列名：locator 只能追加在末尾。

    中途插入会报 `cannot change name of view column ...`，所以这条顺序是硬约束，
    不是风格问题。这里取 SELECT 列表里的列引用，要求 locator 恰好是最后一个。
    """
    sql_text = _MIGRATION.read_text(encoding="utf-8")
    view_body = sql_text.split("CREATE OR REPLACE VIEW v_active_chunks", 1)[1]
    select_list = view_body.split("FROM chunks c", 1)[0]
    referenced = re.findall(r"\b[a-z]\.[a-z_]+\b", select_list)
    assert referenced[-1] == "c.locator", f"视图最后一列是 {referenced[-1]}，应为 c.locator"
    # 前缀列序与 0013 一致：本文件只在末尾多一列，没有重排。
    assert referenced[:3] == ["c.chunk_id", "c.workspace_id", "d.kb_id"]
    assert "c.page_end_no" in referenced
    assert referenced.count("c.locator") == 1
