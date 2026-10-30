"""成员2 摄取 Worker 的启动入口（S08 缺的那一环）。

S08 的通过信号写的是"起两个 Worker 进程、并发抢件不重复"，但仓库里一直没有这个
入口：`run_once` 只被测试调用，生产上没有进程消费 `ingestion_jobs`。上传接口返回
202 之后，作业就一直躺在 pending 里。

本脚本只做装配，不新增业务逻辑：

1. 每轮用 `session_scope()` 开新 session（`db/session.py:47` 正常退出即 commit）；
2. 用成员1 交付的 `SqlKnowledgeBaseRepo` 提供 `KbRepo`——**只 import 使用，不改其代码**；
3. 用 `build_embedder(settings)` 装配 embedder，再循环 `run_once`。

用法::

    uv run python scripts/run_worker.py                  # 常驻，Ctrl-C 退出
    uv run python scripts/run_worker.py --once           # 只抢一件，冒烟用
    uv run python scripts/run_worker.py --poll-interval 0.5
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pyknora.core.settings import settings  # noqa: E402
from pyknora.db.repos.knowledge_bases import SqlKnowledgeBaseRepo  # noqa: E402
from pyknora.db.session import dispose_engine, session_scope  # noqa: E402
from pyknora.services.ingest.embedder import EmbeddingProvider, build_embedder  # noqa: E402
from pyknora.services.outbox.dispatcher import DispatchReport, dispatch_pending  # noqa: E402
from pyknora.workers.ingest_worker import ProcessResult, run_once  # noqa: E402

logger = logging.getLogger("pyknora.worker")


async def _tick(
    *, worker_id: str, embedder: EmbeddingProvider
) -> tuple[ProcessResult | None, DispatchReport]:
    """一轮 = 一个短事务：跑一件作业 + 派发一批发件箱事件。

    不要再在外面包事务：`run_once` 内部已经用 SAVEPOINT 把作业体隔开
    （`workers/ingest_worker.py:291`），外面再包一层会破坏它设计的失败回滚语义。

    发件箱派发放在**这里**而不是 `run_once` 里面：队列空时 `run_once` 提前返回，
    于是发布事务写下的 `document.index_published` 在空闲 Worker 上永远等不到派发
    （见 `services/outbox/dispatcher.py` 的模块注释）。
    """
    async with session_scope() as session:
        result = await run_once(
            session,
            worker_id=worker_id,
            kb_repo=SqlKnowledgeBaseRepo(session),
            embedder=embedder,
        )
        report = await dispatch_pending(session)
        return result, report


async def main() -> int:
    parser = argparse.ArgumentParser(description="PyKnora 摄取 Worker")
    parser.add_argument("--once", action="store_true", help="只处理一件就退出")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="空队列时休眠秒数")
    parser.add_argument("--worker-id", default=None, help="默认 worker-<pid>")
    args = parser.parse_args()

    embedder = build_embedder(settings)
    worker_id = args.worker_id or f"worker-{os.getpid()}"
    logger.info(
        "worker %s 启动：backend=%s model_key=%s dim=%s",
        worker_id,
        settings.embedding_backend,
        embedder.model_key,
        embedder.dim,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            # Windows 的 ProactorEventLoop 不支持 add_signal_handler，靠 KeyboardInterrupt
            loop.add_signal_handler(sig, stop.set)

    try:
        while not stop.is_set():
            try:
                result, report = await _tick(worker_id=worker_id, embedder=embedder)
            except Exception:  # noqa: BLE001
                # 作业级错误已由 run_once 回写；这里兜住的是装配/连接级异常。
                # 不能退出：一条坏作业不该让整个 worker 下线（lease 到期会重新可抢）。
                logger.exception("处理作业时出现未捕获异常")
                if args.once:
                    return 1
                await asyncio.sleep(5.0)
                continue

            if report.total:
                logger.info(
                    "outbox 派发：dispatched=%s failed=%s", report.dispatched, report.failed
                )

            if result is None:
                if args.once:
                    logger.info("队列空，--once 退出")
                    return 0
                await asyncio.sleep(args.poll_interval)
                continue

            logger.info(
                "job %s -> %s (chunks=%s, error=%s)",
                result.job_id,
                result.status,
                result.chunk_count,
                result.error_code,
            )
            if args.once:
                return 0 if result.status == "succeeded" else 1
    except KeyboardInterrupt:
        logger.info("收到 Ctrl-C，退出")
    finally:
        await dispose_engine()
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    raise SystemExit(asyncio.run(main()))
