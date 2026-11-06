"""任务9：注册与登录。

两条链路的共同点：**响应里绝不出现密码或哈希**。注册成功直接发 token，
省掉一次"注册完再登录"的往返——但登录接口仍然必须独立存在，因为老用户要能续期。

几个决定：

- 注册返回 **201**（真的创建了主体+工作区两行），不是 200。
- 重名统一 **409 `CONFLICT`**，且**不告诉调用方是用户名还是邮箱撞了**：
  说了就等于提供一个"这个邮箱是否注册过"的查询接口。要区分的话，得先做
  邮箱验证流程，那是后续需求。
- 登录失败只有一种响应：401 `invalid credentials`。区分"用户不存在"与"密码错"，
  等于把用户枚举做成免费服务；同时用 bcrypt 的常数时间比对，不给计时侧信道。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.api.deps import get_scope, get_session, require_csrf
from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.core.settings import settings
from pyknora.db.repos.principals import SqlIdentityRepo
from pyknora.db.repos.refresh_tokens import SqlRefreshTokenRepo
from pyknora.models.tables import RefreshTokenRow
from pyknora.services.auth.jwt import DEFAULT_TTL_SECONDS, issue_token
from pyknora.services.auth.membership import (
    create_default_workspace_for,
    load_permissions,
    resolve_default_workspace,
)
from pyknora.services.auth.passwords import (
    MAX_PASSWORD_BYTES,
    hash_password_async,
    password_within_limit,
    verify_password_async,
)

router = APIRouter(tags=["auth"])


class RegisterRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="登录名，允许字母/数字/下划线/点/短横线",
    )
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, value: str) -> str:
        # 不用 pydantic 的 EmailStr：那要多装 email-validator 依赖，而本仓库
        # 不做邮件投递，真正的校验在"发验证邮件"那一步，现在只需要挡住明显不是邮箱的输入。
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("invalid email")
        return value

    @field_validator("password")
    @classmethod
    def _password_bytes(cls, value: str) -> str:
        # bcrypt 5.x 超过 72 字节直接抛错；在这里拦成 422，比落到哈希函数里更清楚。
        if not password_within_limit(value):
            raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """`access_token` 之外把 principal/workspace 一并回给前端。

    否则前端拿到 token 还得再解一次 JWT 才知道自己是谁、默认在哪个空间——
    解 token 的逻辑在服务端只该有一份。
    """

    model_config = ConfigDict(frozen=True)

    access_token: str
    token_type: str = "bearer"
    expires_in: int = DEFAULT_TTL_SECONDS
    principal_id: UUID
    workspace_id: UUID
    display_name: str | None = None


def _duplicate() -> AppError:
    # 消息里既不指出撞的是用户名还是邮箱，也不出现 "email"/"username" 字样，
    # 避免这个接口被当成"某邮箱是否注册过"的存在性探针。
    return AppError(ErrorCode.CONFLICT, "account already exists")


# 只有这两个唯一键才代表"账号已存在"。`workspace_memberships_role_fkey` 之类的
# 违约是**环境数据坏了**（例如 `roles` 参考表被清空），不是账号冲突。
_DUPLICATE_CONSTRAINTS = frozenset({"principals_username_key", "principals_email_key"})


def _constraint_name(exc: IntegrityError) -> str | None:
    """从 SQLAlchemy/asyncpg 的异常里取约束名，取不到就退回消息里的 `constraint "x"`。"""
    orig = getattr(exc, "orig", None)
    name = getattr(orig, "constraint_name", None)
    if isinstance(name, str):
        return name
    match = re.search(r'constraint "([^"]+)"', str(orig or exc))
    return match.group(1) if match else None


class RefreshResponse(BaseModel):
    """刷新成功只回有效期；访问令牌只在 HttpOnly Cookie 里，不回响应体。

    与 `TokenResponse` 刻意不同：注册/登录要把令牌回给命令行工具，刷新则只服务
    浏览器会话，把令牌回进响应体等于把 HttpOnly 的价值在刷新那一刻又漏回 JS。
    """

    model_config = ConfigDict(frozen=True)

    expires_in: int = DEFAULT_TTL_SECONDS


_ACCESS_COOKIE = "pyknora_access"
_REFRESH_COOKIE = "pyknora_refresh"
_CSRF_COOKIE = "pyknora_csrf"
_AUTH_PATH = "/api/v1/auth"
# 同一个旧刷新串在宽限内被重复投递（连接抖动/浏览器重发）不算盗用。
_REPLAY_GRACE_SECONDS = 10


def _set_session_cookies(
    response: Response, *, access: str, refresh: str | None, csrf: str | None
) -> None:
    """下发三件套；刷新端点只重发前两个，CSRF 按会话稳定、不随刷新轮换。"""
    secure = settings.cookie_secure
    response.set_cookie(
        _ACCESS_COOKIE,
        access,
        max_age=DEFAULT_TTL_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    if refresh is not None:
        response.set_cookie(
            _REFRESH_COOKIE,
            refresh,
            max_age=settings.refresh_ttl_seconds,
            path=_AUTH_PATH,
            httponly=True,
            samesite="lax",
            secure=secure,
        )
    if csrf is not None:
        response.set_cookie(
            _CSRF_COOKIE,
            csrf,
            max_age=settings.refresh_ttl_seconds,
            path="/",
            httponly=False,
            samesite="lax",
            secure=secure,
        )


def _clear_session_cookies(response: Response) -> None:
    for name, path in (
        (_ACCESS_COOKIE, "/"),
        (_REFRESH_COOKIE, _AUTH_PATH),
        (_CSRF_COOKIE, "/"),
    ):
        response.delete_cookie(name, path=path, samesite="lax", secure=settings.cookie_secure)


async def _new_session(session: AsyncSession, *, principal_id: UUID) -> tuple[str, str]:
    """建一个会话家族，返回 (刷新串明文, CSRF 明文)——明文只经 Set-Cookie 回一次。

    顺带按主体清掉已过绝对期的行，不引调度器：长期不登录的账号留下的过期行
    由 `scripts/purge_refresh_tokens.py` 手动清。
    """
    refresh = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    await SqlRefreshTokenRepo(session).purge_expired_for(principal_id)
    session.add(
        RefreshTokenRow(
            token_hash=hashlib.sha256(refresh.encode()).hexdigest(),
            family_id=uuid4(),
            principal_id=principal_id,
            csrf_hash=hashlib.sha256(csrf.encode()).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.refresh_ttl_seconds),
        )
    )
    await session.flush()
    return refresh, csrf


async def _deny(session: AsyncSession, family_id: UUID) -> None:
    """撤销整族并**显式提交**，再抛 401。

    会话作用域在异常路径会 rollback；不先 commit，撤销会被一起回滚掉，盗用者
    手里同族的其它刷新串就还能用。这是「失败分支也要提交」的唯一原因。
    """
    await SqlRefreshTokenRepo(session).revoke_family(family_id)
    await session.commit()
    raise AppError(ErrorCode.UNAUTHENTICATED, "invalid refresh token")


@router.post(
    "/auth/register",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenResponse,
    summary="注册并直接签发 token",
)
async def register(
    payload: RegisterRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    repo = SqlIdentityRepo(session)

    # 先查一次只是为了给出干净的 409；真正的兜底是唯一约束 + 下面的 IntegrityError。
    if await repo.find_principal_by_username(payload.username):
        raise _duplicate()
    if await repo.find_principal_by_email(payload.email):
        raise _duplicate()

    password_hash = await hash_password_async(payload.password)

    try:
        principal = await repo.create_principal(
            username=payload.username,
            email=payload.email,
            password_hash=password_hash,
            display_name=payload.display_name,
        )
        # 注册与建空间必须同事务：只建人不建空间，用户登录后无处可去。
        workspace = await create_default_workspace_for(session, principal)
        await session.commit()
    except IntegrityError as exc:
        # 并发注册同名：两个请求都通过了"先查"，第二个在这里撞唯一键。
        await session.rollback()
        constraint = _constraint_name(exc)
        if constraint not in _DUPLICATE_CONSTRAINTS:
            # **不是**账号冲突，而是建默认工作区时撞了别的完整性约束（典型：角色
            # 参考表被清空 → `workspace_memberships_role_fkey` 违约）。报 409
            # "account already exists" 会让用户以为换个用户名再试就好，也会让排障的
            # 人往前端/数据库连接上找——真实原因在服务端数据，必须显式报出去。
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "account provisioning failed",
                details={"constraint": constraint or "unknown"},
            ) from exc
        raise _duplicate() from exc

    # 会话与账号分两笔事务：账号 + 默认工作区必须同生共死（上面的 try），
    # 会话行插入失败只是「这次没发出 Cookie」，重新登录即可，不该把账号一起回滚。
    access = issue_token(principal.principal_id, workspace.workspace_id)
    refresh, csrf = await _new_session(session, principal_id=principal.principal_id)
    await session.commit()
    _set_session_cookies(response, access=access, refresh=refresh, csrf=csrf)
    return TokenResponse(
        access_token=access,
        principal_id=principal.principal_id,
        workspace_id=workspace.workspace_id,
        display_name=principal.display_name,
    )


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    summary="登录并签发 token",
)
async def login(
    payload: LoginRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    repo = SqlIdentityRepo(session)
    principal = await repo.find_principal_by_username(payload.username)

    if principal is None or principal.status != "active":
        raise AppError(ErrorCode.UNAUTHENTICATED, "invalid credentials")

    if not await verify_password_async(payload.password, principal.password_hash):
        raise AppError(ErrorCode.UNAUTHENTICATED, "invalid credentials")

    workspace_id = await resolve_default_workspace(session, principal.principal_id)
    if workspace_id is None:
        # 数据异常：账号存在但没有成员关系（注册事务被手工回滚等的残留）。
        # 这不该发生，真发生了要报错而不是签发一个没有空间的 token。
        raise AppError(ErrorCode.FORBIDDEN, "principal has no workspace membership")

    await repo.touch_last_login(principal.principal_id)
    refresh, csrf = await _new_session(session, principal_id=principal.principal_id)
    await session.commit()

    access = issue_token(principal.principal_id, workspace_id)
    _set_session_cookies(response, access=access, refresh=refresh, csrf=csrf)
    return TokenResponse(
        access_token=access,
        principal_id=principal.principal_id,
        workspace_id=workspace_id,
        display_name=principal.display_name,
    )


class MeResponse(BaseModel):
    """当前身份自省：前端登录后据此决定显示哪些入口。

    只带 `display_name`，**不带 email**：email 是账号资料而不是判权依据，自省接口越少
    暴露资料，越不容易被当成"这个邮箱是否注册过"的查询面。
    """

    model_config = ConfigDict(frozen=True)

    principal_id: UUID
    display_name: str | None
    workspace_id: UUID
    role: str
    permissions: list[str]


@router.get("/auth/me", response_model=MeResponse, summary="当前身份、角色与权限点")
async def me(
    scope: Annotated[Scope, Depends(get_scope)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    """回答"我在这个空间能做什么"。

    权限点**现算不缓存**，且与 `require_permission` 共用 `load_permissions`：两处若
    各算一套，前端就会出现"按钮显示得出来、点下去 403"的自相矛盾。

    三种失败分开报，因为调用方的处置完全不同：令牌无效/过期（`get_scope` → 401，
    去重登）、主体被停用（401，别重登了，找管理员）、不是本区成员（403，切空间）。
    """
    repo = SqlIdentityRepo(session)
    principal = await repo.find_principal(scope.principal_id)
    if principal is None or principal.status != "active":
        # 与"令牌签发后才被停用"同口径：401，且不区分"不存在"与"已停用"。
        raise AppError(ErrorCode.UNAUTHENTICATED, "principal is not active")
    membership = await repo.find_membership(scope.workspace_id, scope.principal_id)
    if membership is None:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "not a member of this workspace",
            details={"workspace_id": str(scope.workspace_id)},
        )
    permissions = await load_permissions(
        session, principal_id=scope.principal_id, workspace_id=scope.workspace_id
    )
    return MeResponse(
        principal_id=principal.principal_id,
        display_name=principal.display_name,
        workspace_id=scope.workspace_id,
        role=membership.role,
        permissions=sorted(permissions),
    )


@router.post(
    "/auth/refresh",
    response_model=RefreshResponse,
    dependencies=[Depends(require_csrf)],
    summary="轮换会话（单次轮换 + 重用检测）",
)
async def refresh_session(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """用刷新串换一对新 Cookie；旧串单次作废，再用就撤整个家族。

    不挂 `get_scope`：访问令牌过期正是刷新的正常前提，挂了会先 401。
    失败分支由 `_deny` 先提交撤销再抛 401；服务端不清 Cookie，交给前端的
    失效流程走幂等登出——异常处理器会重建响应，端点上 delete_cookie 会被丢掉。
    """
    refresh = request.cookies.get(_REFRESH_COOKIE)
    csrf = request.cookies.get(_CSRF_COOKIE, "")
    repo = SqlRefreshTokenRepo(session)
    row = (
        await repo.get_for_update(hashlib.sha256(refresh.encode()).hexdigest()) if refresh else None
    )
    if row is None:
        raise AppError(ErrorCode.UNAUTHENTICATED, "invalid refresh token")

    family = row.family_id
    now = datetime.now(UTC)

    # 与库里绑定的 CSRF 哈希核对：防止把别人的 CSRF Cookie 注进本会话。
    if not hmac.compare_digest(hashlib.sha256(csrf.encode()).hexdigest(), row.csrf_hash):
        await _deny(session, family)
    if row.revoked_at is not None or row.expires_at <= now:
        await _deny(session, family)
    if row.replaced_by is not None:
        grace = (
            row.rotated_at is not None
            and (now - row.rotated_at).total_seconds() <= _REPLAY_GRACE_SECONDS
        )
        if grace and await repo.has_live_successor(family):
            # 同一旧串在宽限内被重复投递：已经有人成功轮换过了，幂等放行。
            # 204 不是 401，前端不会误判成「会话失效」，也不带任何 Set-Cookie。
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        await _deny(session, family)

    principal = await SqlIdentityRepo(session).find_principal(row.principal_id)
    if principal is None or principal.status != "active":
        await _deny(session, family)
    workspace_id = await resolve_default_workspace(session, row.principal_id)
    if workspace_id is None:
        raise AppError(ErrorCode.FORBIDDEN, "principal has no workspace membership")

    new_refresh = secrets.token_urlsafe(32)
    new_hash = hashlib.sha256(new_refresh.encode()).hexdigest()
    await repo.insert_successor(row, new_hash)
    await repo.mark_rotated(row.token_hash, new_hash)
    await session.commit()

    result = JSONResponse(content={"expires_in": DEFAULT_TTL_SECONDS})
    _set_session_cookies(
        result,
        access=issue_token(row.principal_id, workspace_id),
        refresh=new_refresh,
        csrf=None,
    )
    return result


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
    summary="登出（撤销整个会话家族，幂等）",
)
async def logout(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """撤销刷新串所属的整个家族并清三件套；没有 Cookie 也照样 204（幂等）。"""
    refresh = request.cookies.get(_REFRESH_COOKIE)
    if refresh:
        repo = SqlRefreshTokenRepo(session)
        row = await repo.get_for_update(hashlib.sha256(refresh.encode()).hexdigest())
        if row is not None:
            await repo.revoke_family(row.family_id)
            await session.commit()
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookies(out)
    return out
