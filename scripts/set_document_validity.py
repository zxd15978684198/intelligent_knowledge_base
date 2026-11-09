"""把一份文档标成"已失效"或改权威度（migrations/0016）。

为什么要有这个脚本：有效期最初只在上传时能设，可现实里最需要它的场景是
**文档已经在库里了，才发现它是旧版**（评测里《员工手册 v1》就是这样进来的）。
没有这个脚本就只能写 UPDATE 语句，而写 UPDATE 的人不会去动 documents 之外的
任何东西 —— 也就不会有人记得同步 row_version。

用法：

    # 让 v1 手册从 2025-01-01 起失效（历史文档一律走这个方向）
    uv run python scripts/set_document_validity.py \
        --document-id 89069c8b-... --effective-until 2025-01-01T00:00:00+08:00

    # 撤销失效，恢复成现行
    uv run python scripts/set_document_validity.py --document-id ... --clear-until

    # 改权威度（越小越权威，默认 100）
    uv run python scripts/set_document_validity.py --document-id ... --authority 10

    # 按文件名找（同一 KB 内可能多个，脚本会要求加 --document-id 消歧）
    uv run python scripts/set_document_validity.py --filename 员工手册v1.docx --effective-until ...

只改 `documents` 三列 + `row_version`，不动切片、不动 build、不重跑索引：
视图 `v_active_chunks` 的下一次查询就会按新有效期过滤。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from pyknora.core.settings import settings  # noqa: E402


def _parse_moment(raw: str) -> datetime:
    """解析 ISO8601；不带时区的按本机时区解释（运维手敲时间不会带时区）。"""
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(
            f"时间格式看不懂：{raw!r}（要 ISO8601，如 2025-01-01T00:00:00+08:00）"
        ) from exc
    if value.tzinfo is None:
        value = value.astimezone()
    return value


_FIND_BY_FILENAME = text(
    """
    SELECT d.document_id, d.kb_id, v.object_key
    FROM documents AS d
    JOIN document_versions AS v
      ON v.document_id = d.document_id
     AND v.workspace_id = d.workspace_id
    WHERE d.deleted_at IS NULL
      AND v.object_key LIKE '%/' || :filename
    ORDER BY d.created_at
    """
)

_UPDATE = text(
    """
    UPDATE documents
       SET effective_until = :until,
           row_version = row_version + 1
     WHERE document_id = :document_id
       AND deleted_at IS NULL
    RETURNING document_id, effective_until, authority
    """
)

_UPDATE_AUTHORITY = text(
    """
    UPDATE documents
       SET authority = :authority,
           row_version = row_version + 1
     WHERE document_id = :document_id
       AND deleted_at IS NULL
    RETURNING document_id, effective_until, authority
    """
)


async def _run(args: argparse.Namespace) -> int:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            document_id: UUID | None = UUID(args.document_id) if args.document_id else None

            if document_id is None:
                rows = (await conn.execute(_FIND_BY_FILENAME, {"filename": args.filename})).all()
                if not rows:
                    print(f"[未找到] 没有名为 {args.filename} 的在册文档", file=sys.stderr)
                    return 1
                if len({str(r.document_id) for r in rows}) > 1:
                    print(
                        "[需要消歧] 同一文件名对应多份文档，请加 --document-id：", file=sys.stderr
                    )
                    for row in rows:
                        print(
                            f"  {row.document_id}  kb={row.kb_id}  {row.object_key}",
                            file=sys.stderr,
                        )
                    return 2
                document_id = rows[0].document_id

            # 一次只做一件事：同时给 --effective-until 与 --authority 时按两条 UPDATE 依次执行，
            # 每条都自带 row_version + 1，便于审计时看出动过几次。
            touched = False
            if args.effective_until is not None or args.clear_until:
                until = None if args.clear_until else _parse_moment(args.effective_until)
                row = (
                    await conn.execute(_UPDATE, {"until": until, "document_id": document_id})
                ).first()
                if row is None:
                    print(f"[未找到] {document_id} 不在册（或已删除）", file=sys.stderr)
                    return 1
                print(f"[已更新] {row.document_id} effective_until={row.effective_until}")
                touched = True

            if args.authority is not None:
                row = (
                    await conn.execute(
                        _UPDATE_AUTHORITY, {"authority": args.authority, "document_id": document_id}
                    )
                ).first()
                if row is None:
                    print(f"[未找到] {document_id} 不在册（或已删除）", file=sys.stderr)
                    return 1
                print(f"[已更新] {row.document_id} authority={row.authority}")
                touched = True

            if not touched:
                print("[无事可做] 至少给一个 --effective-until / --clear-until / --authority")
                return 3
            return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="设置文档有效期与权威度（只改 documents 三列）")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--document-id", help="文档 UUID")
    target.add_argument("--filename", help="按文件名找（唯一时才动）")
    parser.add_argument("--effective-until", help="失效时刻，ISO8601；早于现在即视为已失效")
    parser.add_argument("--clear-until", action="store_true", help="清空失效时刻，恢复成现行")
    parser.add_argument("--authority", type=int, help="权威度，越小越权威（默认 100）")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
