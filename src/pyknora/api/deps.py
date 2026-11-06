"""FastAPI 依赖：从 JWT 解析可信 Scope、拿 session、拿仓库。

这一层是**权限边界的唯一入口**。三条规则写在这里而不是散在路由里：

1. `Scope` 只从**服务端下发的凭证**解出来：优先读会话 Cookie 里的访问令牌，
   没有 Cookie 才回退到 `Authorization: Bearer <jwt>`（命令行工具与评测脚本）。
   请求体里的 `workspace_id` / `principal_id` / 查询串里的 `user_id` **一律不读**
   ——读了就等于把授权交给调用方自己声明。
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

import hmac
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.errors import AppError, ErrorCode, not_found
from pyknora.core.scope import Scope
from pyknora.core.settings import settings
from pyknora.db.repos.knowledge_bases import SqlKnowledgeBaseRepo
from pyknora.db.repos.reads import (
    SqlChunkRepo,
    SqlChunkSearchRepo,
    SqlDocumentRepo,
    SqlIndexBuildRepo,
)
from pyknora.db.session import get_session_factory, session_scope
from pyknora.services.auth.jwt import decode_token
from pyknora.services.auth.membership import (
    load_membership,
    load_permissions,
    set_workspace_context,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """每请求一个 session，正常退出提交、异常回滚。"""
    async with session_scope() as session:
        yield session


async def get_scope(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
    pyknora_access: Annotated[str | None, Cookie()] = None,
) -> Scope:
    """解析可信 Scope。所有业务路由的第一个依赖。

    会话 Cookie 优先：浏览器登录后访问令牌在 HttpOnly Cookie 里；没有 Cookie
    才回退到 Bearer 头，供命令行工具与评测脚本继续使用。两者都不带 = 401。
    把 Cookie 做成带默认值的尾参数，既有单元测试的四参数直接调用仍走 Bearer 兜底。
    """
    raw = pyknora_access
    if not raw:
        if not authorization or not authorization.startswith("Bearer "):
            raise AppError(ErrorCode.UNAUTHENTICATED, "missing bearer token")
        raw = authorization.removeprefix("Bearer ").strip()
    payload = decode_token(raw)

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


async def get_keyword_search_repo(
    scope: Annotated[Scope, Depends(get_scope)],
) -> AsyncIterator[SqlChunkSearchRepo]:
    """关键词那一路的**独立连接**——两路检索要真并行，就不能共用 session。

    SQLAlchemy 的 `AsyncSession` 不是并发安全的：同一个 session 上同时跑两条
    语句会抛 `InvalidRequestError`（greenlet 之间互相踩）。所以并行版的两路
    各用各的 session，这个依赖负责开第二条并在请求结束时关掉。

    `set_workspace_context` 不能省：`chunks` 表开了 RLS，漏设时开发库因为用
    superuser 连（绕过 RLS）测不出来，生产换成 `pyknora_app` 会静默返回 0 行。
    """
    factory = get_session_factory()
    async with factory() as session:
        await set_workspace_context(session, scope.workspace_id)
        yield SqlChunkSearchRepo(session)


def get_build_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlIndexBuildRepo:
    return SqlIndexBuildRepo(session)


def get_kb_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    """KB 仓库。

    成员1 未交付前用进程级 `FakeKbRepo`（数据假、schema 真）。
    真实实现在 `pyknora/api/main.py` 的 `app.state.kb_repo` 里注册，
    这个函数只是取它——**替换成本就是这一行**。
    """
    from pyknora.api.main import get_app_state

    state = get_app_state()
    if state.extra.get("kb_repo_configured"):
        return state.kb_repo
    return SqlKnowledgeBaseRepo(session)


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
    if kb_repo is None:
        # 与上传端点同一个兜底：仓库没注入是部署问题，不是"库不存在"。
        raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "knowledge base repo not configured")
    kb = await kb_repo.get(scope, kb_id)
    if kb is None:
        raise not_found("knowledge base")
    return kb


def get_max_upload_bytes() -> int:
    return settings.max_upload_bytes


async def get_permissions(
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> frozenset[str]:
    """当前主体在当前工作区的权限点集合（判权只读 `role_grants`）。

    **fail-closed**：非成员 / 账号停用 / 角色没有任何授权 → 空集。空集不会让任何
    `require_permission` 通过，也不存在"默认权限"兜底。

    来源链：`workspace_memberships.role` → `roles` → `role_grants`。
    刻意不做跨请求缓存：撤权限必须**下一次请求**就生效（与"停用账号立即失效"同一原则）。
    同一请求里多处 `require_permission` 由 FastAPI 的依赖缓存合并成一次查询。
    """
    return await load_permissions(
        session, principal_id=scope.principal_id, workspace_id=scope.workspace_id
    )


def require_permission(permission: str) -> Callable[..., Awaitable[frozenset[str]]]:
    """生成"必须拥有某个权限点"的依赖，挂到路由或 `APIRouter` 上：

        @router.post("", dependencies=[Depends(require_permission(Permission.DOCUMENT_WRITE))])

    为什么是 403 而不是 404：走到这一步说明"这人是本空间成员"已由 `get_scope`
    确认，缺的是**动作权限**，没有需要隐藏的资源 id；用 404 会让前端把"权限不足"
    误显示成"资源不存在"。跨区访问仍然由资源查询回 404（不承认存在性）。

    权限点字符串一律取 `services.auth.permissions` 的常量：拼错的后果是静默 403，
    不会抛异常。
    """

    async def _require(
        permissions: Annotated[frozenset[str], Depends(get_permissions)],
    ) -> frozenset[str]:
        if permission not in permissions:
            raise AppError(ErrorCode.FORBIDDEN, f"missing permission: {permission}")
        return permissions

    _require.__name__ = "require_" + permission.replace(".", "_")
    _require.__doc__ = f"要求权限点 {permission}"
    return _require


# --------------------------------------------------------------------------- #
# 双提交 CSRF（只保护「浏览器会话 + 写方法」）
# --------------------------------------------------------------------------- #

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_ACCESS_COOKIE = "pyknora_access"
_REFRESH_COOKIE = "pyknora_refresh"
_CSRF_COOKIE = "pyknora_csrf"
_CSRF_HEADER = "X-CSRF-Token"


async def require_csrf(request: Request) -> None:
    """校验双提交 CSRF；不通过抛 `CSRF_FAILED`（403，不可重试）。

    三条豁免都刻意不引入第二套身份判断：
    - 安全方法（GET/HEAD/OPTIONS/TRACE）不改写状态；
    - 没有会话 Cookie：匿名注册/登录，或命令行 Bearer（跨站表单塞不进自定义
      Cookie，也读不到别人的 Cookie），不存在伪造面；
    - `pyknora_csrf` 本身前端可读，攻击者能读同源 Cookie 就等于已经 XSS 了。

    判定「有没有会话」只看 Cookie，与 `get_scope` 的「Cookie 存在即忽略 Bearer」
    完全一致；否则攻击者附一个 Bearer 头就能让本依赖误判成命令行请求。
    """
    if request.method in _SAFE_METHODS:
        return
    if _ACCESS_COOKIE not in request.cookies and _REFRESH_COOKIE not in request.cookies:
        return
    sent = request.headers.get(_CSRF_HEADER, "")
    expected = request.cookies.get(_CSRF_COOKIE, "")
    if not expected or not hmac.compare_digest(sent, expected):
        raise AppError(ErrorCode.CSRF_FAILED, "csrf token mismatch")
