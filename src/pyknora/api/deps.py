"""FastAPI 依赖：从 JWT 解析可信 Scope、拿 session、拿仓库。

这一层是**权限边界的唯一入口**。三条规则写在这里而不是散在路由里：

1. `Scope` 只从 `Authorization: Bearer <jwt>` 解出来。请求体里的 `workspace_id`
   / `principal_id` / 查询串里的 `user_id` **一律不读**——读了就等于把授权交给
   调用方自己声明。这条在 `docs/tdd/07_Data_API_Security.md:103` 有明确要求。
2. `X-Workspace-ID` 头只用来"在这人已加入的多个空间里选一个"，它**不能**凭空
   造出一个他本不属于的空间。未提供时用 JWT 里的 `default_workspace_id`。
3. 解析失败一律 401 `UNAUTHENTICATED`，不区分"没带 token / token 过期 / 签名错"
   ——区分了就是在给攻击者做 oracle。

**成员4 尚未交付**：这里的 `decode_token` 是一个**明确的占位实现**，只做 HS256
验签 + `exp` 校验，没有用户表、没有成员关系表。等成员4 的 `/auth` 落地后，
唯一需要改的是 `_load_membership` 这个函数——`Scope` 的构造方式不要动。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.core.settings import settings
from pyknora.db.repos.knowledge_bases import SqlKnowledgeBaseRepo
from pyknora.db.repos.reads import (
    SqlChunkRepo,
    SqlChunkSearchRepo,
    SqlDocumentRepo,
    SqlIndexBuildRepo,
)
from pyknora.db.session import session_scope

# 开发期默认密钥。生产必须由 PYKNORA_JWT_SECRET 覆盖。
_DEFAULT_SECRET = "dev-only-insecure-secret"


def _b64url_decode(segment: str) -> bytes:
    """JWT 段是 base64url 且**去掉 padding**，必须补齐才能解。"""
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _sign(message: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode(), message, hashlib.sha256).digest()


def decode_token(token: str, secret: str | None = None) -> dict[str, Any]:
    """最小 JWT (HS256) 校验。

    只实现到"能被验证"为止：算法固定 HS256（**不接受 token 头部自报的 alg**，
    否则 `alg: none` 就是一次伪造），验签用 `hmac.compare_digest` 防时序侧信道。
    """
    secret = secret or os.environ.get("PYKNORA_JWT_SECRET", _DEFAULT_SECRET)
    parts = token.split(".")
    if len(parts) != 3:
        raise AppError(ErrorCode.UNAUTHENTICATED, "malformed token")

    header_seg, payload_seg, sig_seg = parts
    try:
        header = json.loads(_b64url_decode(header_seg))
        payload = json.loads(_b64url_decode(payload_seg))
        signature = _b64url_decode(sig_seg)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise AppError(ErrorCode.UNAUTHENTICATED, "malformed token") from exc

    # 关键：不看 header["alg"] 决定用什么算法，只拒绝非 HS256。
    if header.get("alg") != "HS256":
        raise AppError(ErrorCode.UNAUTHENTICATED, "unsupported alg")

    expected = _sign(f"{header_seg}.{payload_seg}".encode(), secret)
    if not hmac.compare_digest(expected, signature):
        raise AppError(ErrorCode.UNAUTHENTICATED, "bad signature")

    exp = payload.get("exp")
    if exp is not None and float(exp) <= time.time():
        raise AppError(ErrorCode.UNAUTHENTICATED, "token expired")

    return payload


async def _load_membership(principal_id: UUID, workspace_id: UUID) -> bool:
    """该 principal 是否属于该 workspace。

    成员4 交付前：占位为 True —— **这是本轮唯一的授权空洞，且已显式记录在
    `docs/plans/成员2执行计划.md` §9 的未验证清单里**。它不掩盖问题：真实实现
    落地时这个函数体换掉，调用它的三处路由一行都不用动。
    """
    return True


async def get_scope(
    request: Request,
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

    if not await _load_membership(principal_id, workspace_id):
        # 不属于该空间，与"空间不存在"同一响应，避免枚举。
        raise AppError(ErrorCode.FORBIDDEN, "not a member of this workspace")

    scope = Scope(workspace_id=workspace_id, principal_id=principal_id)
    # 记到 request.state，异常处理器与日志要读，不必再解一次 token。
    request.state.scope = scope
    return scope


async def get_session() -> AsyncIterator[AsyncSession]:
    """每请求一个 session，正常退出提交、异常回滚。"""
    async with session_scope() as session:
        yield session


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


def get_max_upload_bytes() -> int:
    return settings.max_upload_bytes
