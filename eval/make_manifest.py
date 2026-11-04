"""评测版本清单生成器（计划 P0-G2）：把一次评测运行绑到一份可哈希的清单上。

为什么需要它：eval 跑出来的数字（recall / citation / gate 通过率）只有在"同一份代码 +
同一版提示词 + 同一个 embedding 模型 + 同一个库状态"下才可复现。这些信息现在散落在
两份 config yaml、report.md 和人的记忆里，换一台机器就对不上。本脚本把它们压成
`eval/day/manifest.json` 一份 JSON，并用规范 JSON 的 sha256 给整份清单盖章；报告里只要
记下 `manifest_sha256`，就能回答"这批数字是在哪份清单上跑出来的"。

**哈希口径（重要，别用错）**

    manifest_sha256 = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                        separators=(",", ":")))

其中 `payload` **不含** `manifest_sha256` 自身。落盘文件是同一份内容的 `indent=2` 版本
（便于人读与 git diff），所以"文件字节的 sha256"**不等于** `manifest_sha256`——前者随缩进
变化，后者只随内容变化。复核请用 `--verify`（它按上面的口径重算），不要直接
`hashlib.sha256(open(path, "rb").read())`，那必然得到不一致的结论。

**读不到库时不许编**

`documents` / `chunks` 是"活库"的事实，不是可以估的东西。连不上库时清单退化为
`documents=[]` + `chunks=null` + `db_status="unavailable"`，仍然 exit 0——清单的其余部分
（commit / 模型 / 语料哈希）照样有用；只有加 `--strict` 才把"读不到库"当失败（exit 2）。
注意 `documents=[]` 有两种含义，靠 `db_status` 区分：`"ok"` = 库是空的，`"unavailable"` =
没读到。连上了但某张表/视图读不到时 `db_status="partial"`，并在 `db_error` 里写原因。

**不写进清单的东西**

`settings.database_url` 只在进程内用来连库，**绝不落盘**——它带库密码。

命令行：

    uv run --frozen python eval/make_manifest.py                 # 写 eval/day/manifest.json
    uv run --frozen python eval/make_manifest.py --print-only    # 只打印，不写盘
    uv run --frozen python eval/make_manifest.py --verify        # 复核已有文件的哈希
    uv run --frozen python eval/make_manifest.py --strict        # 库读不到时 exit 2
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from pyknora.schemas.document import filename_from_object_key

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_DB_STRICT = 2

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = "eval/day/manifest.json"
CORPUS_MANIFEST = REPO_ROOT / "eval" / "corpus_manifest.json"
# 连库超时：清单是"顺手记一笔"的产物，不该因为库没起就卡住一次评测的收尾。
DB_TIMEOUT_S = 5.0
HASH_FIELD = "manifest_sha256"

# 文档列表：指针取自 documents（可变表，存的是"当前"），文件名从活动版本的 object_key
# 末段还原（复用 schemas/document.py 的 filename_from_object_key，别在这里再写一遍
# rpartition —— 那份实现已经登记了三个边界情况）。
#
# 只取 deleted_at IS NULL：软删的文档不在 v_active_chunks 里，列出来会和 chunks 数对不上。
# ORDER BY document_id：清单要能 diff，行序不能随 PG 的物理顺序漂。
# uuid 一律 ::text：asyncpg 会返回 UUID 对象，json.dumps 认不出来。
_DOCUMENTS_SQL = """
SELECT d.document_id::text      AS document_id,
       d.active_build_id::text  AS active_build_id,
       d.active_index_version   AS active_index_version,
       dv.object_key            AS object_key
  FROM documents d
  LEFT JOIN document_versions dv
         ON dv.workspace_id = d.workspace_id
        AND dv.document_id = d.document_id
        AND dv.document_version_id = d.active_document_version_id
 WHERE d.deleted_at IS NULL
 ORDER BY d.document_id
"""

# chunk 数只认 v_active_chunks（AGENTS §4 I1：读 chunk 必须走视图，不许绕开）。
_CHUNKS_SQL = "SELECT count(*) AS chunks FROM v_active_chunks"


@dataclass(frozen=True, slots=True)
class SettingsFacts:
    """settings 里能进清单的字段，外加连库用的 DSN（DSN 只在进程内用）。"""

    prompt_version: str | None = None
    embedding_model_key: str | None = None
    embedding_dim: int | None = None
    database_url: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DbState:
    """一次活库读取的结果。status ∈ {"ok", "partial", "unavailable"}。"""

    documents: list[dict[str, Any]] = field(default_factory=list)
    chunks: int | None = None
    status: str = "unavailable"
    role: str | None = None
    error: str | None = None


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(payload: Any) -> str:
    """规范 JSON：键序固定、无多余空白、中文不转义——哈希只随内容变化。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def manifest_sha256(payload: dict[str, Any]) -> str:
    """整份清单的哈希（不含 manifest_sha256 自身，否则自指）。"""
    body = {key: value for key, value in payload.items() if key != HASH_FIELD}
    return _sha256_hex(_canonical_json(body).encode("utf-8"))


def _describe(exc: BaseException) -> str:
    """把异常压成一行可读文本：清单里不写 traceback，但原因要留得住。"""
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:300]}" if text else type(exc).__name__


def _git(*args: str) -> str | None:
    """跑一条只读 git 命令；git 不可用 / 不是仓库 / 非零退出都返回 None。"""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def _git_dirty() -> bool | None:
    """工作区是否脏。None = git 用不了，**不能**当成 False（干净）。

    为什么要记这个：`app_commit` 只钉住 HEAD，脏工作区里的代码和 HEAD 并不是一回事；
    一个 dirty=true 的清单说明"这批数字来自未提交的改动"，复现时必须先找回那些改动。
    """
    try:
        done = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return bool(done.stdout.strip())


def _settings_facts() -> SettingsFacts:
    """从 settings 取字段。加载失败时全部留空并记下原因，清单照写。"""
    try:
        from pyknora.core.settings import settings

        # prompt_version 目前**不在** settings 里（真实来源是
        # services/rag/answer.py 的 PROMPT_VERSION）。用 getattr 而不是直接取：
        # 将来它被加进 settings 就自动生效，现在没有就是 null——这不是在掩盖拼写错误，
        # 而是这一项在当前代码里确实没有可读的来源。
        return SettingsFacts(
            prompt_version=getattr(settings, "prompt_version", None),
            embedding_model_key=settings.embedding_model_key,
            embedding_dim=settings.embedding_dim,
            database_url=settings.database_url,
        )
    except Exception as exc:  # settings 是外部配置边界：pydantic 校验失败也要能出清单
        return SettingsFacts(error=f"settings 不可用：{_describe(exc)}")


def _asyncpg_dsn(database_url: str) -> str:
    """settings 的 DSN 带 SQLAlchemy 驱动后缀（postgresql+asyncpg://），asyncpg 不认。"""
    scheme, sep, rest = database_url.partition("://")
    if not sep:
        return database_url
    return f"{scheme.replace('+asyncpg', '', 1)}://{rest}"


def _document_entry(row: Any) -> dict[str, Any]:
    return {
        "document_id": row["document_id"],
        "active_build_id": row["active_build_id"],
        "active_index_version": row["active_index_version"],
        "filename": filename_from_object_key(row["object_key"]),
    }


async def _read_conn(conn: asyncpg.Connection, timeout_s: float) -> DbState:
    role: str | None = None
    with contextlib.suppress(Exception):
        # 角色只用于诊断：superuser / 表 owner 天然绕过 RLS（AGENTS §2），
        # 那时清单反映的是整库；普通角色则可能被 RLS 挡掉一部分行。
        role = await asyncio.wait_for(conn.fetchval("SELECT current_user"), timeout_s)

    documents: list[dict[str, Any]] = []
    chunks: int | None = None
    problems: list[str] = []

    try:
        rows = await asyncio.wait_for(conn.fetch(_DOCUMENTS_SQL), timeout_s)
        documents = [_document_entry(row) for row in rows]
    except Exception as exc:
        problems.append(f"documents 读不到（{_describe(exc)}）")

    try:
        counted = await asyncio.wait_for(conn.fetchval(_CHUNKS_SQL), timeout_s)
        chunks = int(counted or 0)
    except Exception as exc:
        problems.append(f"v_active_chunks 读不到（{_describe(exc)}）")

    status = "partial" if problems else "ok"
    return DbState(documents, chunks, status, role, "; ".join(problems) or None)


async def _collect_db(facts: SettingsFacts, timeout_s: float) -> DbState:
    """读活库。任何失败都退化成状态位，不抛异常——清单本身不该因为库没起就产不出来。"""
    if not facts.database_url:
        return DbState(error=facts.error or "settings.database_url 为空，无法连库")
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(_asyncpg_dsn(facts.database_url), timeout=timeout_s),
            timeout_s,
        )
    except Exception as exc:  # 连不上：拒连 / 密码错 / DNS / 超时，一律"读不到"
        return DbState(error=f"连接失败：{_describe(exc)}")
    try:
        return await _read_conn(conn, timeout_s)
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


def _corpus_sha256() -> str | None:
    """`eval/corpus_manifest.json` 的字节哈希；文件不在就是 None（不猜）。"""
    try:
        return _sha256_hex(CORPUS_MANIFEST.read_bytes())
    except OSError:
        return None


def build_payload(facts: SettingsFacts, db: DbState) -> dict[str, Any]:
    """组装清单正文（不含 manifest_sha256）。注意：这里没有、也不许有 database_url。"""
    return {
        "app_commit": _git("rev-parse", "HEAD"),
        "app_dirty": _git_dirty(),
        "prompt_version": facts.prompt_version,
        "embedding_model_key": facts.embedding_model_key,
        "embedding_dim": facts.embedding_dim,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "documents": db.documents,
        "chunks": db.chunks,
        "corpus": _corpus_sha256(),
        "db_status": db.status,
        "db_role": db.role,
        "db_error": db.error,
        "settings_error": facts.error,
    }


def _write_manifest(payload: dict[str, Any], out_path: Path) -> str:
    """写盘，返回**文件字节**的 sha256（与 payload 里的 manifest_sha256 不是同一个数）。"""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    data = text.encode("utf-8")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 用 write_bytes 而不是 write_text：后者在 Windows 会把 "\n" 翻成 "\r\n"，
    # 于是"文件字节哈希"与这里算的对不上，等于自己埋一个假不一致。
    out_path.write_bytes(data)
    return _sha256_hex(data)


def _resolve_out(raw: str) -> Path:
    """相对路径按**仓库根**解析：从 eval/ 或任意子目录启动都落到同一个文件。"""
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def _print_summary(
    payload: dict[str, Any],
    *,
    out_path: Path,
    file_hash: str | None,
    print_only: bool,
) -> None:
    print(f"[manifest] app_commit={payload['app_commit']} dirty={payload['app_dirty']}")
    print(f"[manifest] prompt_version={payload['prompt_version']}")
    print(f"[manifest] embedding={payload['embedding_model_key']} dim={payload['embedding_dim']}")
    print(
        f"[manifest] db_status={payload['db_status']} role={payload['db_role']} "
        f"documents={len(payload['documents'])} chunks={payload['chunks']}"
    )
    if payload["db_error"]:
        print(f"[manifest] db_error={payload['db_error']}")
    if payload["settings_error"]:
        print(f"[manifest] settings_error={payload['settings_error']}")
    print(f"[manifest] corpus={payload['corpus']}")
    print(f"[manifest] {HASH_FIELD}={payload[HASH_FIELD]}")
    if print_only:
        print("[manifest] --print-only：只打印，未写盘")
    else:
        print(f"[manifest] wrote {out_path} (file_sha256={file_hash})")


def _cmd_verify(out_path: Path) -> int:
    """按规范 JSON 口径重算已有文件里的哈希——这是唯一的复核入口。"""
    try:
        raw = out_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[manifest] 读不到 {out_path}：{_describe(exc)}", file=sys.stderr)
        return EXIT_FAIL
    if not isinstance(payload, dict):
        print(f"[manifest] {out_path} 顶层不是 JSON 对象", file=sys.stderr)
        return EXIT_FAIL

    recorded = payload.get(HASH_FIELD)
    recomputed = manifest_sha256(payload)
    print(f"[manifest] recorded  ={recorded}")
    print(f"[manifest] recomputed={recomputed}")
    print(f"[manifest] file_sha256={_sha256_hex(raw)}")
    print("[manifest] 注：file_sha256 与 manifest_sha256 不同是正常的——前者是缩进后的文件字节，")
    print("[manifest]     后者是规范 JSON（sort_keys / 无空格）的字节，口径见本文件顶部。")
    if recorded != recomputed:
        print("[manifest] 不一致：文件被改过，或生成时的规范口径不同", file=sys.stderr)
        return EXIT_FAIL
    print("[manifest] 一致")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="make_manifest",
        description="生成 eval/day/manifest.json：把一次评测运行绑到一份可哈希的清单上",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  uv run --frozen python eval/make_manifest.py --print-only\n"
            "  uv run --frozen python eval/make_manifest.py\n"
            "  uv run --frozen python eval/make_manifest.py --verify\n"
            "  uv run --frozen python eval/make_manifest.py --strict\n"
            "\n"
            "exit：0 成功（库读不到但非 --strict 也算成功）/ 1 写盘或校验失败 / "
            "2 --strict 且库读不到\n"
        ),
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help=f"清单落盘路径，相对路径按仓库根解析（默认 {DEFAULT_OUT}）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="库读不到（db_status != ok）时 exit 2；默认降级成 db_status=unavailable 并 exit 0",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="只打印清单，不写盘（stdout 第一段是 JSON，第二段是 [manifest] 摘要）",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="不读库：按规范 JSON 口径重算 --out 文件里的 manifest_sha256 并比对",
    )
    parser.add_argument(
        "--db-timeout",
        type=float,
        default=DB_TIMEOUT_S,
        help=f"连接与单条查询的超时秒数（默认 {DB_TIMEOUT_S}）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_path = _resolve_out(args.out)

    if args.verify:
        return _cmd_verify(out_path)

    facts = _settings_facts()
    db = asyncio.run(_collect_db(facts, args.db_timeout))
    payload = build_payload(facts, db)
    payload[HASH_FIELD] = manifest_sha256(payload)

    if args.print_only:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        _print_summary(payload, out_path=out_path, file_hash=None, print_only=True)
    else:
        try:
            file_hash = _write_manifest(payload, out_path)
        except OSError as exc:
            print(f"[manifest] 写盘失败：{_describe(exc)}", file=sys.stderr)
            return EXIT_FAIL
        _print_summary(payload, out_path=out_path, file_hash=file_hash, print_only=False)

    if args.strict and db.status != "ok":
        print(
            f"[manifest] --strict：db_status={db.status}，按失败退出（exit {EXIT_DB_STRICT}）",
            file=sys.stderr,
        )
        return EXIT_DB_STRICT
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
