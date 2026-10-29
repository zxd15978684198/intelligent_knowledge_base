"""任务17 第 2/3 步：把快照重放进目标库（元信息 + 原文，**仍然没有向量**）。

用法::

    uv run python scripts/replay_metadata.py \
        --dsn postgresql+asyncpg://postgres:dev@127.0.0.1:5434/pyknora_migrated \
        --in .tmp_migration/snapshot.json

写完后的状态是"能读元信息、读不到 chunk"：

- `v_active_chunks` 一条都不露（视图要求 `embedding IS NOT NULL`）；
- 检索接口返回空结果——这正是"没搬向量"的可观测证据。

要让索引重新可用，必须调 `POST /admin/reindex`（任务16），由目标进程用自己的
embedding provider 重算。**本脚本刻意不提供 `--reindex` 快捷开关**：那会把
"重放"和"重建索引"揉成一个动作，演练就分不清"迁移成功"和"重建成功"。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from pyknora.core.settings import settings  # noqa: E402
from pyknora.services.migration.snapshot import (  # noqa: E402
    SnapshotError,
    assert_no_embedding,
    replay_metadata,
)


def _redact(dsn: str) -> str:
    if "@" not in dsn or "://" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    credentials, host = rest.split("@", 1)
    user = credentials.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


async def _run(dsn: str, snapshot_path: Path, on_conflict: str) -> int:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert_no_embedding(snapshot)  # 再查一遍：快照可能被人手改过

    engine = create_async_engine(dsn, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session, session.begin():
            written = await replay_metadata(session, snapshot, on_conflict=on_conflict)
    except SnapshotError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # 主键/外键冲突等，整笔事务已回滚
        print(f"失败：重放未提交（{type(exc).__name__}: {exc}）", file=sys.stderr)
        return 4
    finally:
        await engine.dispose()

    print(f"目标　：{_redact(dsn)}")
    print(f"快照　：{snapshot_path}（导出于 {snapshot.get('exported_at')}）")
    for name, count in written.items():
        print(f"　{name:<18} 写入 {count} 行")
    print("提示　：目标库 chunks 此时没有向量，v_active_chunks 为空属预期；")
    print("　　　　下一步是 POST /admin/reindex，由目标进程重算向量。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把迁库快照写进目标库（不含向量）")
    parser.add_argument("--dsn", default=None, help="目标库 DSN（必填，刻意不默认指向源库）")
    parser.add_argument("--in", dest="snapshot", required=True, help="JSON 快照路径")
    parser.add_argument(
        "--on-conflict",
        choices=("error", "skip"),
        default="error",
        help="目标库已有同主键时：error=报错回滚（默认）/ skip=跳过",
    )
    args = parser.parse_args(argv)
    dsn = args.dsn or settings.database_url
    return asyncio.run(_run(dsn, Path(args.snapshot), args.on_conflict))


if __name__ == "__main__":
    raise SystemExit(main())
