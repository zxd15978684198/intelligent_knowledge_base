"""把 settings.embedding_dim 传给 psql。

关键点：**不把 SQL 文件路径传给容器**。容器启动时没有挂载宿主目录，
容器内不存在 /migrations 路径，`psql -f /migrations/x.sql` 必然报
`No such file or directory`。改由宿主读文件、经 stdin 喂给 psql。

`--database` 用于迁库演练：目标库是新建的空库，也要先跑同一套迁移建表，
否则重放会报 `relation "documents" does not exist`。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pyknora.core.settings import settings  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按 0001..000N 顺序建表（宿主读文件 → 容器 psql）")
    parser.add_argument(
        "--database",
        default="pyknora",
        help="目标数据库名（默认 pyknora；迁库演练时传新建的空库名）",
    )
    parser.add_argument("--container", default=settings.db_container, help="PG 容器名")
    args = parser.parse_args(argv)

    docker = "docker"
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql_text = sql_file.read_text(encoding="utf-8")
        # 等价于命令行 -v dim=512，避免再拼 shell 参数
        script = f"\\set dim {settings.embedding_dim}\n" + sql_text
        cmd = [
            docker,
            "exec",
            "-i",
            args.container,
            "psql",
            "-U",
            "postgres",
            "-d",
            args.database,
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "-",
        ]
        print("+", " ".join(cmd), "<", sql_file.name)
        # encoding 必须显式写死 utf-8：text=True 且不指定 encoding 时，Python 用
        # locale.getpreferredencoding(False) 决定管道编码，在中文 Windows 上可能是
        # cp936/GBK。SQL 里语句内部的中文注释会被编成 GBK 字节，psql 按 UTF-8 校验
        # 就会报 `invalid byte sequence for encoding "UTF8": 0xb5`（"的"的 GBK 首字节）。
        subprocess.run(cmd, input=script, text=True, encoding="utf-8", check=True)
    print("migrations ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
