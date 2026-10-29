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

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.api.deps import get_scope, get_session
from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.db.repos.principals import SqlIdentityRepo
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


@router.post(
    "/auth/register",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenResponse,
    summary="注册并直接签发 token",
)
async def register(
    payload: RegisterRequest,
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
        raise _duplicate() from exc

    return TokenResponse(
        access_token=issue_token(principal.principal_id, workspace.workspace_id),
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
    await session.commit()

    return TokenResponse(
        access_token=issue_token(principal.principal_id, workspace_id),
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
