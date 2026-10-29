"""任务17 第 1/3 步：从源库导出"元信息 + 原文"快照（**不含向量**）。

用法::

    uv run python scripts/export_metadata.py --out .tmp_migration/snapshot.json
    uv run python scripts/export_metadata.py --dsn postgresql+asyncpg://... --workspace <uuid>

产物两个文件，同一份数据的两种形态：

- `<out>.json`：给 `scripts/replay_metadata.py` 用（机器读）。
- `<out>.sql`：给人看、给 psql 用。计划书 `:571` 的验收判据"导出 SQL 里
  `embedding` 的 `INSERT` 行数 == 0"就靠它复核。

退出码非 0 的三种情况（都不静默）：连不上库、快照里出现向量列、SQL 里出现向量 INSERT。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from pyknora.core.settings import settings  # noqa: E402
from pyknora.services.migration.snapshot import (  # noqa: E402
    count_embedding_inserts,
    export_metadata,
    render_sql,
)


def _redact(dsn: str) -> str:
    """把口令从 DSN 里抹掉再打印（日志里不许出现口令，AGENTS §4 错误处理口径）。"""
    if "@" not in dsn or "://" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    credentials, host = rest.split("@", 1)
    user = credentials.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


async def _run(dsn: str, out: Path, workspaces: list[UUID] | None) -> int:
    engine = create_async_engine(dsn, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            snapshot = await export_metadata(session, workspace_ids=workspaces)
    finally:
        await engine.dispose()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    sql_path = out.with_suffix(".sql")
    sql_text = render_sql(snapshot, target_dsn_hint=_redact(dsn))
    sql_path.write_text(sql_text, encoding="utf-8")

    tables = snapshot["tables"]
    counts = ", ".join(f"{name}={len(rows)}" for name, rows in tables.items() if rows)
    print(f"来源　：{_redact(dsn)}")
    print(f"JSON　：{out}（{out.stat().st_size} 字节）")
    print(f"SQL 　：{sql_path}（{sql_path.stat().st_size} 字节）")
    print(f"行数　：{counts or '（空库）'}")

    offenders = count_embedding_inserts(sql_text)
    print(f"判据　：导出 SQL 中涉及 embedding/tsvector 的 INSERT 行数 = {offenders}")
    if offenders:
        print("失败：导出里出现了向量列，停止（迁库只搬元信息与原文）", file=sys.stderr)
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出迁库快照（元信息 + 原文，不含向量）")
    parser.add_argument("--dsn", default=settings.database_url, help="源库 DSN（默认取 settings）")
    parser.add_argument(
        "--out",
        default=".tmp_migration/snapshot.json",
        help="JSON 输出路径（同目录同名 .sql 一并生成）",
    )
    parser.add_argument(
        "--workspace",
        action="append",
        default=None,
        help="只导指定 workspace（可重复；不给=全库）",
    )
    args = parser.parse_args(argv)
    workspaces = [UUID(value) for value in args.workspace] if args.workspace else None
    return asyncio.run(_run(args.dsn, Path(args.out), workspaces))


if __name__ == "__main__":
    raise SystemExit(main())
