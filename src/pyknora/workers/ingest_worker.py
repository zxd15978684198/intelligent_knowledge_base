"""成员2 的 Worker：把作业从 claim 跑到 publish。

链路：claim → 取版本字节 → 解析 → 切片 → 向量化（带缓存）→ staging build
     → publish（原子切换）

失败语义：
- 未超 max_attempts → 退回 pending，等下一次 claim
- 超了 → failed，并写 error_code
- 抛出 STALE_ATTEMPT / VERSION_SUPERSEDED → 说明已被更新的尝试接管，
  不再重试（重试只会再失败一次），直接放弃本次

**不假定恰好执行一次**：任何时刻被打断，重新 claim 都会重做一遍，
所以每一步都必须可重入（staging build 先删后建、vector 先删后写）。
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.core.settings import settings
from pyknora.db.session import session_scope
from pyknora.models.tables import ChunkRow, DocumentVersionRow
from pyknora.services.contracts.reads import KbRepo
from pyknora.services.ingest import parser, pool, publish, splitter
from pyknora.services.ingest.embedder import EmbeddingProvider, check_profile_compat
from pyknora.services.ingest.embedding_service import EmbeddingService, digest_content
from pyknora.services.queue import jobs as queue
from pyknora.services.queue.jobs import ClaimedJob

logger = logging.getLogger(__name__)


def _kb_field(profile: object, name: str, default: object) -> object:
    """取 KB profile 字段；缺失时回落到 settings 默认值并留下 warning。

    兜底是必要的（profile 结构在成员1 那边演进，字段不同步不能直接炸），
    但必须留痕：否则"配方静默回落到 settings"这类不一致永远看不见，等到检索
    结果不对时才反过来查。
    """
    value = getattr(profile, name, None)
    if value is None:
        logger.warning("kb profile 缺少 %s，回落到 %r", name, default)
        return default
    return value


# 这些错误重试也不会好，直接放弃本次尝试
_NO_RETRY = frozenset(
    {
        ErrorCode.STALE_ATTEMPT,
        ErrorCode.VERSION_SUPERSEDED,
        ErrorCode.UNSUPPORTED_FORMAT,
        ErrorCode.IDEMPOTENCY_CONFLICT,
        # 坏文件是**确定性**失败：同一份字节重试多少次都还是坏文件。
        # 以前它不在这个集合里，于是一个截断的 PDF 会占满 3 次重试、并常驻队头
        # 挡住后面所有上传（实测：队列 3 分钟没有任何作业被认领）。
        ErrorCode.PARSE_FAILED,
        # 解析硬超时（ADR 0008 D7）：重试只会再超时一次，和坏文件一样是确定性失败。
        # 必须进这个集合，否则一件超时作业会白白占满 3 次重试（每次 60s）。
        ErrorCode.PARSE_TIMEOUT,
    }
)

# 只有这两类才算"被更新的构建取代"。其余 `_NO_RETRY` 是**业务失败**，
# 终态必须是 `failed`：一份解析不了的文件被记成 `superseded`，会让审计看到
# "它被新版本替换了"——事实是它从来没成功过。
_SUPERSEDE_CODES = frozenset({ErrorCode.STALE_ATTEMPT, ErrorCode.VERSION_SUPERSEDED})


@dataclass(slots=True)
class ProcessResult:
    job_id: UUID
    status: str
    build_id: UUID | None = None
    chunk_count: int = 0
    error_code: str | None = None


# --------------------------------------------------------------------- 租约心跳
# ADR 0008 D6：租约 90 s（`core/settings.py:104`），心跳 30 s。一条心跳丢了还有两次
# 机会；连续 3 次（≈90 s）都失败才放弃，那时租约本来也快到期了。
HEARTBEAT_INTERVAL_S: Final[float] = 30.0
HEARTBEAT_MAX_CONSECUTIVE_FAILURES: Final[int] = 3

RenewLease = Callable[[ClaimedJob], Awaitable[bool]]


async def _renew_lease_in_new_session(job: ClaimedJob) -> bool:
    """用**独立连接**续租。

    必须独立：主 session 正在跑作业体（发布前还要锁 documents 行），把心跳塞进
    同一个事务里，两条 `UPDATE ingestion_jobs` 会互相等待。契约是"认领单独提交 +
    独立连接"两条同时成立，只做前一条不够。
    """
    async with session_scope() as session:
        return await queue.renew_lease(
            session,
            job_id=job.job_id,
            lease_epoch=job.lease_epoch,
            lease_seconds=settings.lease_seconds,
        )


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> bool:
    """等 `seconds` 秒；被 `stop` 提前唤醒时返回 True（该退出了）。"""
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:  # py3.12：asyncio.TimeoutError 就是内置 TimeoutError
        return False
    return True


async def heartbeat_loop(
    *,
    job: ClaimedJob,
    stop: asyncio.Event,
    renew: RenewLease | None = None,
    interval_s: float = HEARTBEAT_INTERVAL_S,
    max_consecutive_failures: int = HEARTBEAT_MAX_CONSECUTIVE_FAILURES,
) -> None:
    """周期性续租，直到 `stop` 置位、续租被拒、或连续失败到上限。

    **失败只 warning，不杀作业**：DB 抖动是暂时的，而中途放弃一份已经跑了一半的
    解析，代价是整份重来——这正是 D6 要修的那个缺陷。宁可让作业随租约到期被别人
    重领，也不在这里主动判死。
    """
    renew_fn = renew or _renew_lease_in_new_session
    failures = 0
    while not await _sleep_or_stop(stop, interval_s):
        try:
            renewed = await renew_fn(job)
        except Exception:
            failures += 1
            logger.warning(
                "作业 %s 续租失败（连续 %d 次），作业继续跑", job.job_id, failures, exc_info=True
            )
            if failures >= max_consecutive_failures:
                logger.warning(
                    "作业 %s 连续 %d 次续租失败，停止心跳；租约到期后作业可被重新认领",
                    job.job_id,
                    failures,
                )
                return
            continue
        failures = 0
        if not renewed:
            logger.warning(
                "作业 %s 续租被拒（lease_epoch=%s 已失效），停止心跳",
                job.job_id,
                job.lease_epoch,
            )
            return


@contextlib.asynccontextmanager
async def lease_heartbeat(
    job: ClaimedJob,
    *,
    renew: RenewLease | None = None,
    interval_s: float = HEARTBEAT_INTERVAL_S,
) -> AsyncIterator[None]:
    """给作业体挂一条心跳；退出上下文时必定收掉，不留悬空 task。"""
    stop = asyncio.Event()
    task = asyncio.create_task(
        heartbeat_loop(job=job, stop=stop, renew=renew, interval_s=interval_s),
        name=f"lease-heartbeat-{job.job_id}",
    )
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _load_bytes(session: AsyncSession, *, scope: Scope, version_id: UUID) -> bytes:
    """从版本快照取正文。

    当前版本把正文直接放在 object_key 指向的对象存储里；本仓库尚未接对象存储，
    所以用一个可测试的替身表（document_blobs）。生产替换点只有这一个函数。
    """
    row = await session.execute(
        text(
            "SELECT content FROM document_blobs "
            "WHERE workspace_id = :ws AND document_version_id = :vid"
        ),
        {"ws": scope.workspace_id, "vid": version_id},
    )
    data = row.scalar_one_or_none()
    if data is None:
        raise AppError(ErrorCode.PARSE_FAILED, "document bytes missing for version")
    return data.encode("utf-8") if isinstance(data, str) else bytes(data)


async def _load_version(
    session: AsyncSession, *, scope: Scope, version_id: UUID
) -> DocumentVersionRow:
    row = (
        await session.execute(
            select(DocumentVersionRow).where(
                DocumentVersionRow.workspace_id == scope.workspace_id,
                DocumentVersionRow.document_version_id == version_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "document version not found")
    return row


async def process_job(
    session: AsyncSession,
    *,
    job: ClaimedJob,
    kb_repo: KbRepo,
    embedder: EmbeddingProvider,
) -> ProcessResult:
    """处理一件已 claim 的作业。调用方负责 commit。"""
    # 身份只从作业 payload 里取（入队时由 API 层从 JWT 解出的 principal 写入）。
    # 老作业没有这个字段时退回 workspace_id 并告警：跨区仍然被 workspace 谓词挡住，
    # 但 principal 语义缺失，不能把它当成"就是本人"。
    raw_actor = job.payload.get("principal_id")
    if raw_actor:
        try:
            actor_id = UUID(str(raw_actor))
        except ValueError as exc:
            raise AppError(
                ErrorCode.INVALID_REQUEST, "job payload principal_id is not a uuid"
            ) from exc
    else:
        logger.warning("job %s 缺少 principal_id，身份退回 workspace_id", job.job_id)
        actor_id = job.workspace_id
    scope = Scope(workspace_id=job.workspace_id, principal_id=actor_id)

    if job.job_type == "purge":
        from pyknora.services.ingest.documents import purge_document

        report = await purge_document(session, scope=scope, document_id=job.document_id)
        await session.execute(
            text(
                "UPDATE ingestion_jobs SET status = 'succeeded', updated_at = now(), "
                "lease_expires_at = NULL WHERE job_id = :job_id AND lease_epoch = :epoch"
            ),
            {"job_id": job.job_id, "epoch": job.lease_epoch},
        )
        await queue.finish_attempt(session, attempt_id=job.attempt_id, outcome="succeeded")
        logger.info(
            "purge %s: chunks=%d blobs=%d versions=%d",
            job.document_id,
            report.chunks,
            report.blobs,
            report.versions,
        )
        return ProcessResult(job_id=job.job_id, status="succeeded", chunk_count=report.total)

    if job.job_type != "ingest":
        raise AppError(ErrorCode.INVALID_REQUEST, f"unknown job type {job.job_type}")

    version_id = UUID(str(job.payload["document_version_id"]))
    filename = str(job.payload.get("filename") or "unnamed.txt")
    content_type = job.payload.get("content_type")
    kb_id = UUID(str(job.payload["kb_id"]))

    profile = await kb_repo.get_embedding_profile(scope, kb_id)
    if profile is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "knowledge base gone during ingestion")

    # C1 接缝：KB 的 profile 是 embedding 配方的唯一来源，优先级高于 settings。
    # 不一致就停在这里——静默用 embedder 的维度会把 index_builds 的元数据写成
    # 假的（记录与真实向量来源不符），之后用正确维度反而查不到（红线 I7）。
    check_profile_compat(profile, dim=embedder.dim, model_key=embedder.model_key)
    recipe_model_key = str(_kb_field(profile, "embedding_model_key", embedder.model_key))
    recipe_dim = int(_kb_field(profile, "embedding_dim", embedder.dim))
    recipe_chunk_size = int(_kb_field(profile, "chunk_size", settings.chunk_size))
    recipe_chunk_overlap = int(_kb_field(profile, "chunk_overlap", settings.chunk_overlap))
    recipe_normalize = bool(_kb_field(profile, "normalize", True))

    # 先确认版本快照仍在（给 404 而不是后面的 PARSE_FAILED，报错更准）
    await _load_version(session, scope=scope, version_id=version_id)
    data = await _load_bytes(session, scope=scope, version_id=version_id)

    # ---- 纯函数段：解析 + 切片 ----
    # 解析走常驻进程池 + 硬超时（ADR 0008 D6），两件事必须同时成立：
    # 1) 在**子进程**里跑：pdfplumber 的内存与崩溃（OOM / 段错误）不污染 Worker，
    #    超时即整池重建（见 `services/ingest/pool.py`）；
    # 2) 用 `to_thread` 把阻塞的 `future.result()` 挪出事件循环：否则解析期间循环被
    #    占满，同一循环上的租约心跳一次都发不出去，90 s 后作业被第二个 Worker 重跑。
    parsed = await asyncio.to_thread(
        pool.run_in_pool,
        functools.partial(parser.parse, content_type=content_type),
        data,
        filename,
    )
    chunks = splitter.chunk_parsed(
        parsed,
        chunk_size=recipe_chunk_size,
        chunk_overlap=recipe_chunk_overlap,
    )

    # ---- 分配 index_version（锁 documents 行） ----
    doc_row = await session.execute(
        text(
            "SELECT active_index_version FROM documents "
            "WHERE workspace_id = :ws AND document_id = :doc FOR UPDATE"
        ),
        {"ws": job.workspace_id, "doc": job.document_id},
    )
    row = doc_row.first()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "document gone during ingestion")
    next_version = (row[0] or 0) + 1

    # 重入保护：清掉本次 attempt 之前留下的同版本 staging 数据
    await session.execute(
        text(
            "DELETE FROM chunks WHERE workspace_id = :ws AND document_id = :doc "
            "AND attempt_id = :attempt"
        ),
        {"ws": job.workspace_id, "doc": job.document_id, "attempt": job.attempt_id},
    )
    await session.execute(
        text(
            "DELETE FROM index_builds WHERE workspace_id = :ws AND document_id = :doc "
            "AND attempt_id = :attempt AND state = 'staging'"
        ),
        {"ws": job.workspace_id, "doc": job.document_id, "attempt": job.attempt_id},
    )

    build = await publish.create_staging_build(
        session,
        workspace_id=job.workspace_id,
        document_id=job.document_id,
        attempt_id=job.attempt_id,
        index_version=next_version,
        embedding_model_key=recipe_model_key,
        embedding_dim=recipe_dim,
        chunk_size=recipe_chunk_size,
        chunk_overlap=recipe_chunk_overlap,
        normalize=recipe_normalize,
    )

    # ---- 向量化（带缓存） ----
    service = EmbeddingService(embedder, session)
    vectors = await service.embed_cached([c.content for c in chunks])
    await service.bump_hits([c.content for c in chunks])

    # ---- 写 staging chunk（视图不可见） ----
    for chunk, vec in zip(chunks, vectors, strict=True):
        session.add(
            ChunkRow(
                chunk_id=__import__("uuid").uuid4(),
                workspace_id=job.workspace_id,
                document_id=job.document_id,
                build_id=build.build_id,
                index_version=build.index_version,
                attempt_id=job.attempt_id,
                chunk_ordinal=chunk.ordinal,
                content=chunk.content,
                content_hash=digest_content(chunk.content),
                page_no=chunk.page_no,
                page_end_no=chunk.page_end_no,
                heading_path=chunk.heading_path,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                token_count=chunk.token_count,
                locator_ref=chunk.locator_ref,
                embedding=vec,
            )
        )
    await session.flush()

    # tsvector 由数据库算，避免 Python 侧维护第二套分词规则。
    # 必须过 pyknora_cjk_bigram：`simple` 分词器把连续汉字当**一个词元**，
    # 直接 to_tsvector('simple', content) 会让"搜银狐"永远搜不到"银狐锚点报销标准"。
    # 见 migrations/0007_cjk_bigram_fts.sql 与 db/repos/reads.py 的 search_keyword。
    await session.execute(
        text(
            "UPDATE chunks SET tsvector = to_tsvector('simple', pyknora_cjk_bigram(content)) "
            "WHERE workspace_id = :ws AND build_id = :build"
        ),
        {"ws": job.workspace_id, "build": build.build_id},
    )

    # ---- 发布 ----
    published = await publish.publish(
        session,
        job=job,
        manifest=publish.PublishManifest(
            build_id=build.build_id,
            document_version_id=version_id,
            chunk_count=len(chunks),
            embedding_model_key=recipe_model_key,
            embedding_dim=recipe_dim,
        ),
    )
    return ProcessResult(
        job_id=job.job_id,
        status="succeeded",
        build_id=published.build_id,
        chunk_count=len(chunks),
    )


# 本 Worker 能消费的作业类型。**必须显式声明并传给 `claim`**：队列里有别人新增的
# 类型时，不过滤会让本 Worker 把它领走、在 `process_job` 里判 unknown 然后失败，
# 作业就永远进不了正确的消费者手里。
SUPPORTED_JOB_TYPES: Final[tuple[str, ...]] = ("ingest", "purge")


async def run_once(
    session: AsyncSession,
    *,
    worker_id: str,
    kb_repo: KbRepo,
    embedder: EmbeddingProvider,
) -> ProcessResult | None:
    """取一件、跑一件。返回 None 表示队列空。

    **两个事务边界，别合并**（ADR 0008 D6）：

    1. `claim` 之后**立即提交**——认领的那条 `UPDATE` 会一直持有该作业行的行锁，
       不提交就开跑，另一条连接上的心跳 `renew_lease`（同样 `UPDATE` 这一行）会阻塞
       到作业跑完，心跳等于没发，90 s 后第二个 Worker 把整份解析重跑一遍。提交之后
       作业已落库为 `processing`（带租约），进程崩了也不丢，到期自然被重领。
    2. 作业体包在 SAVEPOINT 里。这不只是"好看"：PG 里任何一条语句报错，
       整个事务立即进入 aborted 态，之后连 `UPDATE job_attempts` 都会被拒。
       如果不先回滚到保存点，`except` 里的清理语句自己也会失败，
       真正的错误就被 "transaction is aborted" 盖掉了——排错时极难定位。
    """
    job = await queue.claim(
        session,
        worker_id=worker_id,
        lease_seconds=settings.lease_seconds,
        job_types=SUPPORTED_JOB_TYPES,
    )
    if job is None:
        return None

    await session.commit()

    try:
        async with lease_heartbeat(job), session.begin_nested():
            return await process_job(session, job=job, kb_repo=kb_repo, embedder=embedder)
    except AppError as exc:
        # 此时保存点已回滚，作业体写的 staging 数据都不在了，但外层事务还活着
        await _record_failure(session, job=job, code=exc.code)
        logger.warning("job %s failed with %s: %s", job.job_id, exc.code, exc.message)
        return ProcessResult(job_id=job.job_id, status="failed", error_code=exc.code)
    except Exception:
        # **不许把异常再抛出去**。调用方（`scripts/run_worker.py::_tick`）包在
        # `session_scope()` 里，异常穿出去会让整个事务回滚，下面 `_record_failure`
        # 写进去的 attempt 与状态**一起消失**：作业保持 pending、attempt 停在 0，
        # 于是同一个作业被无限重领（队列按 created_at 认领，它会一直坐在队头），
        # 后面所有上传都进不来。实测复现：截断的 PDF 让队列 3 分钟零吞吐。
        logger.exception("job %s crashed with an unexpected error", job.job_id)
        try:
            await _record_failure(session, job=job, code=ErrorCode.INTERNAL_ERROR)
        except Exception:
            # 连失败都记不上，说明事务/连接本身已经不可用——这才该往上抛，
            # 让进程退出、租约过期后由别的 Worker 重领。
            logger.exception("job %s 的失败记录也无法写入，向上抛出", job.job_id)
            raise
        return ProcessResult(
            job_id=job.job_id,
            status="failed",
            error_code=ErrorCode.INTERNAL_ERROR,
        )


async def _record_failure(session: AsyncSession, *, job: ClaimedJob, code: str) -> None:
    """把失败记进 attempts 与 jobs。必须在保存点回滚之后调用。"""
    await queue.finish_attempt(session, attempt_id=job.attempt_id, outcome="failed")
    if code in _SUPERSEDE_CODES:
        # 被更新的构建取代：不是本次输入的问题，也不该再跑，但也不能算"失败"。
        await session.execute(
            text(
                "UPDATE ingestion_jobs SET status = 'superseded', error_code = :code, "
                "updated_at = now(), lease_expires_at = NULL "
                "WHERE job_id = :job_id AND lease_epoch = :epoch"
            ),
            {"code": code, "job_id": job.job_id, "epoch": job.lease_epoch},
        )
    elif code in _NO_RETRY:
        # 确定性失败（坏文件、格式不支持）：一次就终结成 failed，不留重试窗口。
        # `max_attempts=1` 让 `mark_failed` 立即判终态，与"重试也白搭"的语义一致。
        await queue.mark_failed(session, job=job, error_code=code, max_attempts=1)
    else:
        await queue.mark_failed(
            session, job=job, error_code=code, max_attempts=settings.max_attempts
        )
