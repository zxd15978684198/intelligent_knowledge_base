"""成员5：运维面路由（任务16 重建索引）。

本文件当前只有 `POST /api/v1/admin/reindex`。语义与代价都写在
`services/migration/reindex.py` 的模块 docstring 里，这里只负责三件事：

1. **身份只从 JWT 来**（I2）：请求体里只有 `kb_id` / `mode` / `idempotency_key`，
   没有任何身份字段；`workspace_id` 一律取 `scope`，body 里塞了也不读
   （`extra="ignore"`，与传身份字段的"忽略而非报错"口径一致）。
2. **可见性判定不在路由里写第二套**：直接调用 `deps.require_kb_access`
   （成员4 的权限唯一入口），他人的 kb_id 得到 404 而不是 403。
3. **状态码诚实**：真跑完才回 202 且 `state` 反映实际结果（`succeeded` / `failed`）。
   本接口是同步执行的长请求，不是"受理后稍后完成"——202 在这里表示"已采纳本次重建"，
   与 `documents` 上传的 202 口径一致。
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.api.deps import (
    get_kb_repo,
    get_scope,
    get_session,
    require_kb_access,
    require_permission,
)
from pyknora.api.main import get_app_state
from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.services.auth.permissions import INDEX_REBUILD
from pyknora.services.migration.reindex import MODE_REINDEX, reindex_knowledge_base

router = APIRouter(tags=["admin"], dependencies=[Depends(require_permission(INDEX_REBUILD))])


class ReindexRequest(BaseModel):
    """重建索引的请求体。

    `mode` 显式收在 body 里而不是路径上：`index_migrations.mode` 的 CHECK 允许
    `export` / `replay` / `reindex` 三种，将来补齐时接口形状不变——只有 `reindex`
    可用时，另外两种回 422 并说明未实现，不静默降级（静默降级会烧掉一次全量重建）。
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    kb_id: UUID = Field(description="要重建的知识库 id")
    mode: str = Field(default=MODE_REINDEX, description="reindex（export/replay 尚未实现）")
    idempotency_key: str | None = Field(
        default=None,
        max_length=200,
        description="同键重放返回上次成功结果，不重复建 build",
    )


class ReindexResponse(BaseModel):
    """一次重建的结果。

    `documents` 是**真正重建成功**的文档数；`skipped` 是"没有可重建正文"或"有新版本
    在等发布"的文档数；`failed` 是本次失败（已按文档回滚）的文档数。
    三者相加 = KB 下已发布文档总数，调用方不用再猜。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    migration_id: UUID
    kb_id: UUID
    mode: str
    state: str
    documents: int
    chunks: int
    skipped: int
    failed: int
    replayed: bool


@router.post(
    "/admin/reindex",
    status_code=202,
    response_model=ReindexResponse,
    summary="按当前 profile 重建知识库索引（任务16）",
)
async def reindex(
    body: ReindexRequest,
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
    kb_repo: Annotated[Any, Depends(get_kb_repo)],
) -> ReindexResponse:
    embedder = get_app_state().embedder
    if embedder is None:
        # 没注入 embedder 就没法重算向量。提前 503，而不是让十几个文档逐个
        # 在 embedding 里抛 AttributeError 变成 500。
        raise AppError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "embedding provider is not configured on this API process",
        )

    # 以普通协程调用而不是 `Depends(require_kb_access)`：`kb_id` 在请求体里，
    # 而 FastAPI 会把依赖里未声明来源的 `UUID` 参数解析成查询参数。调用同一个函数，
    # 可见性判定仍然只有成员4 那一处实现。
    kb = await require_kb_access(kb_id=body.kb_id, scope=scope, kb_repo=kb_repo)

    outcome = await reindex_knowledge_base(
        session,
        scope=scope,
        kb_repo=kb_repo,
        kb_id=kb.kb_id,
        embedder=embedder,
        mode=body.mode,
        idempotency_key=body.idempotency_key,
    )
    return ReindexResponse(
        migration_id=outcome.migration_id,
        kb_id=outcome.kb_id,
        mode=outcome.mode,
        state=outcome.state,
        documents=outcome.documents,
        chunks=outcome.chunks,
        skipped=outcome.skipped,
        failed=outcome.failed,
        replayed=outcome.replayed,
    )
