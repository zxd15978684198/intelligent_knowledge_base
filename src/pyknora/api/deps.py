"""FastAPI 依赖：从 JWT 解析可信 Scope、拿 session、拿仓库。

这一层是**权限边界的唯一入口**。三条规则写在这里而不是散在路由里：

1. `Scope` 只从 `Authorization: Bearer <jwt>` 解出来。请求体里的 `workspace_id`
   / `principal_id` / 查询串里的 `user_id` **一律不读**——读了就等于把授权交给
   调用方自己声明。这条在 `docs/tdd/07_Data_API_Security.md:103` 有明确要求。
2. `X-Workspace-ID` 头只用来"在这人已加入的多个空间里选一个"，它**不能**凭空
   造出一个他本不属于的空间。未提供时用 JWT 里的 `default_workspace_id`。
3. 解析失败一律 401 `UNAUTHENTICATED`，不区分"没带 token / token 过期 / 签名错"
   ——区分了就是在给攻击者做 oracle。

**成员4 已交付**：`load_membership` 现在是真查询（见 `services/auth/membership.py`），
不再是 `return True` 的占位。JWT 的签发/校验在 `services/auth/jwt.py`，这里只消费。

一个容易踩的点：`get_scope` 依赖 `get_session` 有两个目的——查成员关系要连接；
顺手把 `app.workspace_id` 写进事务给 RLS 兜底。FastAPI 的依赖缓存保证同一个请求里
路由拿到的 session 与这里 `SET LOCAL` 用的 session 是**同一个对象**；如果改成
在这里自己 `session_scope()`，就会是一条新连接、新事务，RLS 上下文立刻失效。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.errors import AppError, ErrorCode, not_found
from pyknora.core.scope import Scope
from pyknora.core.settings import settings
from pyknora.db.repos.reads import (
    SqlChunkRepo,
    SqlChunkSearchRepo,
    SqlDocumentRepo,
    SqlIndexBuildRepo,
)
from pyknora.db.session import session_scope
from pyknora.services.auth.jwt import decode_token
from pyknora.services.auth.membership import load_membership, set_workspace_context


async def get_session() -> AsyncIterator[AsyncSession]:
    """每请求一个 session，正常退出提交、异常回滚。"""
    async with session_scope() as session:
        yield session


async def get_scope(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
) -> Scope:
    """解析可信 Scope。所有业务路由的第一个依赖。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise AppError(ErrorCode.UNAUTHENTICATED, "missing bearer token")
    payload = decode_token(authorization.removeprefix("Bearer ").strip())

    sub = payload.get("sub")
    if not sub:
        raise AppError(ErrorCode.UNAUTHENTICATED, "token missing sub")
    try:
        principal_id = UUID(str(sub))
    except ValueError as exc:
        raise AppError(ErrorCode.UNAUTHENTICATED, "sub is not a uuid") from exc

    # 空间选择：请求头优先，其次 token 里的默认空间。两者都必须过 membership。
    raw_ws = x_workspace_id or payload.get("default_workspace_id")
    if not raw_ws:
        raise AppError(ErrorCode.UNAUTHENTICATED, "no workspace in token or header")
    try:
        workspace_id = UUID(str(raw_ws))
    except ValueError as exc:
        raise AppError(ErrorCode.FORBIDDEN, "invalid workspace id") from exc

    if not await load_membership(session, principal_id, workspace_id):
        # 不属于该空间，与"空间不存在"同一响应，避免枚举。
        raise AppError(ErrorCode.FORBIDDEN, "not a member of this workspace")

    # RLS 兜底开关：只在同一事务内有效，且仅对非 owner 角色有实际作用。
    await set_workspace_context(session, workspace_id)

    scope = Scope(workspace_id=workspace_id, principal_id=principal_id)
    # 记到 request.state，异常处理器与日志要读，不必再解一次 token。
    request.state.scope = scope
    return scope


def get_document_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlDocumentRepo:
    return SqlDocumentRepo(session)


def get_chunk_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlChunkRepo:
    return SqlChunkRepo(session)


def get_chunk_search_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlChunkSearchRepo:
    return SqlChunkSearchRepo(session)


def get_build_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlIndexBuildRepo:
    return SqlIndexBuildRepo(session)


def get_kb_repo() -> Any:
    """KB 仓库。

    成员1 未交付前用进程级 `FakeKbRepo`（数据假、schema 真）。
    真实实现在 `pyknora/api/main.py` 的 `app.state.kb_repo` 里注册，
    这个函数只是取它——**替换成本就是这一行**。
    """
    from pyknora.api.main import get_app_state

    return get_app_state().kb_repo


async def require_kb_access(
    kb_id: UUID,
    scope: Annotated[Scope, Depends(get_scope)],
    kb_repo: Annotated[Any, Depends(get_kb_repo)],
) -> Any:
    """知识库访问校验（任务15）：他人的 kb_id 返回 **404 而不是 403**。

    刻意不自己写 `SELECT knowledge_bases`：判定权收在拥有数据的成员1 模块里，
    通过 C1 契约 `kb_repo.get(scope, kb_id)` 取一次。这样即使 KB 的可见性规则
    将来变复杂（公开库、共享链接），改的也只有成员1 的那一处，
    不会出现"两个模块各有一套可见性判断、结论不一致"的分家局面。

    返回值直接是 KB 对象，路由可以省掉第二次查询：

        kb: Annotated[Any, Depends(require_kb_access)]

    404 与"不存在"同码，不给攻击者用状态码探测 kb_id 是否存在。
    """
    kb = await kb_repo.get(scope, kb_id)
    if kb is None:
        raise not_found("knowledge base")
    return kb


def get_max_upload_bytes() -> int:
    return settings.max_upload_bytes
