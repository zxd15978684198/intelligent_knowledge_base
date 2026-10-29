"""任务17 第 3/3 步：比对源库与目标库，确认"迁移没丢东西、向量是重算的"。

用法::

    uv run python scripts/verify_migration.py \
        --source postgresql+asyncpg://postgres:dev@127.0.0.1:5434/pyknora \
        --target postgresql+asyncpg://postgres:dev@127.0.0.1:5434/pyknora_migrated

失败即非 0 退出，并把差异定位到具体 document_id；不输出"看起来没问题"这种话。
刻意不比对 `index_version`：目标库重算一次之后版本号必然 +1，那是设计出来的差异。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from pyknora.core.settings import settings  # noqa: E402
from pyknora.services.migration.snapshot import verify_metadata  # noqa: E402

_WORKSPACES_SQL = text(
    "SELECT workspace_id FROM ("
    "  SELECT workspace_id FROM workspaces"
    "  UNION SELECT workspace_id FROM knowledge_bases"
    ") AS scoped ORDER BY workspace_id"
)


def _redact(dsn: str) -> str:
    if "@" not in dsn or "://" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    credentials, host = rest.split("@", 1)
    user = credentials.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


async def _run(source_dsn: str, target_dsn: str, workspaces: list[UUID] | None) -> int:
    source_engine = create_async_engine(source_dsn, pool_pre_ping=True)
    target_engine = create_async_engine(target_dsn, pool_pre_ping=True)
    source_factory = async_sessionmaker(source_engine, expire_on_commit=False, class_=AsyncSession)
    target_factory = async_sessionmaker(target_engine, expire_on_commit=False, class_=AsyncSession)

    failures = 0
    try:
        async with source_factory() as source, target_factory() as target:
            if workspaces is None:
                workspaces = [row[0] for row in (await source.execute(_WORKSPACES_SQL)).all()]
            print(f"源　　：{_redact(source_dsn)}")
            print(f"目标　：{_redact(target_dsn)}")
            print(f"范围　：{len(workspaces)} 个 workspace\n")
            for workspace_id in workspaces:
                print(f"[workspace {workspace_id}]")
                checks = await verify_metadata(source, target, workspace_id=workspace_id)
                for check in checks:
                    mark = "OK  " if check.ok else "FAIL"
                    print(f"  {mark} {check.name}：{check.detail}")
                    if not check.ok:
                        failures += 1
                print()
    finally:
        await source_engine.dispose()
        await target_engine.dispose()

    if failures:
        print(f"结论：{failures} 项不一致（非 0 退出）", file=sys.stderr)
        return 1
    print("结论：全部校验项一致")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验迁库结果（不含向量的字节比对）")
    parser.add_argument("--source", default=settings.database_url, help="源库 DSN")
    parser.add_argument("--target", required=True, help="目标库 DSN")
    parser.add_argument(
        "--workspace",
        action="append",
        default=None,
        help="只校验指定 workspace（可重复；不给=源库里所有 workspace）",
    )
    args = parser.parse_args(argv)
    workspaces = [UUID(value) for value in args.workspace] if args.workspace else None
    return asyncio.run(_run(args.source, args.target, workspaces))


if __name__ == "__main__":
    raise SystemExit(main())
