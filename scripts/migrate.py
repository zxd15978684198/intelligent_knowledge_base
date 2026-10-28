"""把 settings.embedding_dim 传给 psql。

关键点：**不把 SQL 文件路径传给容器**。容器启动时没有挂载宿主目录，
容器内不存在 /migrations 路径，`psql -f /migrations/x.sql` 必然报
`No such file or directory`。改由宿主读文件、经 stdin 喂给 psql。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pyknora.core.settings import settings  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def main() -> int:
    docker = "docker"
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql_text = sql_file.read_text(encoding="utf-8")
        # 等价于命令行 -v dim=512，避免再拼 shell 参数
        script = f"\\set dim {settings.embedding_dim}\n" + sql_text
        cmd = [
            docker,
            "exec",
            "-i",
            settings.db_container,
            "psql",
            "-U",
            "postgres",
            "-d",
            "pyknora",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "-",
        ]
        print("+", " ".join(cmd), "<", sql_file.name)
        subprocess.run(cmd, input=script, text=True, check=True)
    print("migrations ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
