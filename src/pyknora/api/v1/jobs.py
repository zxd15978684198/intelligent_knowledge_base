"""作业查询与取消。

两条纪律：

1. **查询必须带 scope**。`queue.get_job` 的 SQL 里有 `workspace_id` 谓词，
   跨区查别人的 job 得到 404，不是 403——403 会告诉对方"这个 job 存在"。
2. **取消不是"删掉"**。置 `cancelled` 之后 Worker 可能已经在跑，它会在发布前
   发现状态变了而放弃；强行让它停下来需要的是 fencing（`lease_epoch`），
   不是这里的一个 UPDATE。
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.api.deps import get_scope, get_session, require_permission
from pyknora.core.errors import not_found
from pyknora.core.scope import Scope
from pyknora.services.auth.permissions import JOB_CANCEL, JOB_READ
from pyknora.services.queue import jobs as queue

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])


class JobDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID
    workspace_id: UUID
    document_id: UUID
    job_type: str
    status: str
    attempt: int
    error_code: str | None = None
    # 终态集合由 queue 模块定义，不在这里另写一份常量。
    terminal: bool


@router.get(
    "/jobs/{job_id}",
    response_model=JobDetail,
    summary="作业状态",
    dependencies=[Depends(require_permission(JOB_READ))],
)
async def get_job(
    job_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JobDetail:
    row = await queue.get_job(session, scope=scope, job_id=job_id)
    if row is None:
        raise not_found("job")
    return JobDetail(
        job_id=row.job_id,
        workspace_id=row.workspace_id,
        document_id=row.document_id,
        job_type=row.job_type,
        status=row.status,
        attempt=row.attempt,
        error_code=row.error_code,
        terminal=row.status in queue.TERMINAL_STATUSES,
    )


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobDetail,
    summary="取消作业",
    dependencies=[Depends(require_permission(JOB_CANCEL))],
)
async def cancel_job(
    job_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JobDetail:
    row = await queue.get_job(session, scope=scope, job_id=job_id)
    if row is None:
        raise not_found("job")

    if row.status in queue.TERMINAL_STATUSES:
        # 已经跑完了。取消是空操作，回当前状态而不是报错——
        # 调用方多半是在轮询里顺手取消，报错只会让它多写一段异常分支。
        return JobDetail(
            job_id=row.job_id,
            workspace_id=row.workspace_id,
            document_id=row.document_id,
            job_type=row.job_type,
            status=row.status,
            attempt=row.attempt,
            error_code=row.error_code,
            terminal=True,
        )

    # 只动 pending 的行。processing 的交给 Worker 在发布前自己发现状态变了；
    # 这里硬改会让"谁在跑"变得不可判定。
    #
    # 用 `UPDATE ... RETURNING` 而不是"UPDATE 完再 SELECT"：session 的
    # `expire_on_commit=False`，提交后再查会命中标识映射里的旧对象，读回来
    # 还是 `pending`——接口就会对一个刚被取消的作业回 `pending`，前端于是继续轮询。
    # RETURNING 拿的是这一条语句自己写进去的值，不存在读到旧快照的问题。
    updated = (
        (
            await session.execute(
                text(
                    "UPDATE ingestion_jobs SET status = 'cancelled', updated_at = now() "
                    "WHERE job_id = :job_id AND workspace_id = :ws AND status = 'pending' "
                    "RETURNING job_id, workspace_id, document_id, job_type, status, "
                    "attempt, error_code"
                ),
                {"job_id": job_id, "ws": scope.workspace_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    # 提交紧跟在这条 UPDATE 后面，不留"改了不提交就去做长活"的窗口（ADR 0008 D6）：
    # 这条 UPDATE 持有该行的行锁直到事务结束，Worker 的租约心跳在**独立连接**上
    # UPDATE 同一行，所以任何"先改后忙"的写法都会让心跳一直等到这边提交。
    await session.commit()

    if updated is None:
        # 没改成：说明它在读和写之间被别人推进了（典型是从 pending 变 processing）。
        # 回当前真实状态，仍然不算错误。
        latest = await queue.get_job(session, scope=scope, job_id=job_id)
        if latest is None:
            raise not_found("job")
        return JobDetail(
            job_id=latest.job_id,
            workspace_id=latest.workspace_id,
            document_id=latest.document_id,
            job_type=latest.job_type,
            status=latest.status,
            attempt=latest.attempt,
            error_code=latest.error_code,
            terminal=latest.status in queue.TERMINAL_STATUSES,
        )

    return JobDetail(
        job_id=updated["job_id"],
        workspace_id=updated["workspace_id"],
        document_id=updated["document_id"],
        job_type=updated["job_type"],
        status=updated["status"],
        attempt=updated["attempt"],
        error_code=updated["error_code"],
        terminal=updated["status"] in queue.TERMINAL_STATUSES,
    )
