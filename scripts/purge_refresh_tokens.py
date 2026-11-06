"""手动清理过期的会话令牌行（运维脚本，不引调度器）。

用法::

    uv run python scripts/purge_refresh_tokens.py
    uv run python scripts/purge_refresh_tokens.py --dry-run

会话家族的绝对过期时间不随刷新滑动延长，所以「过期即无用的行」可以安全地
按 expires_at 直接删。删除不影响任何文档/切片/索引数据，也不改既有迁移。
本脚本是唯一的全表清理入口；登录时的顺带清理另在鉴权端点按主体执行。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from pyknora.core.settings import settings  # noqa: E402

_COUNT_SQL = text("SELECT count(*) FROM refresh_tokens WHERE expires_at < now()")
_DELETE_SQL = text("DELETE FROM refresh_tokens WHERE expires_at < now()")


async def _run(dry_run: bool) -> int:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            count = int((await conn.execute(_COUNT_SQL)).scalar_one())
            if dry_run:
                print(f"dry-run：{count} 行已过期，未删除")
            else:
                await conn.execute(_DELETE_SQL)
                print(f"已删除 {count} 行过期会话令牌")
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理过期的会话令牌行")
    parser.add_argument("--dry-run", action="store_true", help="只报数，不删除")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
