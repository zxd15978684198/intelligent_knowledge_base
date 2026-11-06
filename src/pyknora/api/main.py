"""FastAPI 应用入口。

三件事在这里收口，路由里不再重复：

1. **统一错误体**：所有 `AppError` 变成 `{"error":{code,message,retryable,details},"request_id"}`，
   形状对齐 `docs/tdd/07_Data_API_Security.md:190`。未捕获异常一律 500 + `INTERNAL_ERROR`，
   **绝不把堆栈或数据库原文放进 `details`**。
2. **请求 ID**：每个请求一个，回写到响应头 `X-Request-ID`，错误体里重复一份，
   让"用户看到的报错"和"日志里的那一行"能对上。
3. **应用级状态**：`kb_repo` 与 `embedder` 挂在进程上，由 `configure()` 注入。
   成员1 的真实 KB 仓库落地时，**改的就是 `configure()` 传进去的那个对象**。

不在这里做的事：不建表、不跑迁移（那是 `scripts/migrate.py`）、不启动 Worker
（那是 `python -m pyknora.workers.ingest_worker`）。API 进程里跑长任务会拖死事件循环，
`docs/PRD_PyKnora.md:507-508` 明确要求 API 与 Worker 拆开。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from pyknora.api.deps import require_csrf
from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.settings import settings
from pyknora.db.session import dispose_engine
from pyknora.services.ingest.embedder import build_embedder

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppState:
    """进程级依赖。字段都允许为 None，缺哪个就由对应路由报 503。"""

    kb_repo: Any = None
    embedder: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


_state = AppState()


def get_app_state() -> AppState:
    """取进程级状态。`deps.get_kb_repo` 靠它拿到 KB 仓库。

    刻意不做"没配就抛错"：那样连 `/health` 都起不来，本地调试会先卡在这一步。
    缺依赖时由**用到它的路由**报 `DEPENDENCY_UNAVAILABLE`，错误定位更准。
    """
    return _state


def configure(*, kb_repo: Any = None, embedder: Any = None) -> AppState:
    """注入依赖。测试与生产都走这一条，不搞两套。"""
    _state.kb_repo = kb_repo
    _state.embedder = embedder
    _state.extra["kb_repo_configured"] = True
    return _state


def reset_state() -> None:
    """测试夹具用：把进程级状态清回初始，避免用例之间互相污染。"""
    _state.kb_repo = None
    _state.embedder = None
    _state.extra.clear()


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = uuid4().hex
        request.state.request_id = rid
    return rid


def _error_response(exc: AppError, request: Request) -> JSONResponse:
    rid = _request_id(request)
    return JSONResponse(
        status_code=exc.http_status,
        content=exc.to_body(rid),
        headers={"X-Request-ID": rid},
    )


# 状态码 → 业务错误码。Starlette / FastAPI 自己抛的异常（未知路由 404、方法不允许 405、
# 请求体校验失败 422）不带业务码，必须在这里折算，否则同一个客户端要写两套错误解析。
_HTTP_CODE_BY_STATUS: dict[int, str] = {
    400: ErrorCode.INVALID_REQUEST,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.RESOURCE_NOT_FOUND,
    405: ErrorCode.INVALID_REQUEST,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.INVALID_REQUEST,
    429: ErrorCode.INVALID_REQUEST,
}

# 请求体校验失败时最多回 20 条：批量传参的用例可能一次错几十处，全量回没有信息增量。
_MAX_VALIDATION_ERRORS = 20


def _unified_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any],
    request: Request,
) -> JSONResponse:
    """按统一错误体回响应；HTTP 状态码与业务码分开传。

    两者通常一致（`AppError.http_status` 就是由码推出的），但 405 这类"框架状态码
    在业务码表里没有对应项"的情况必须能各自取值——把 405 硬拗成 422 才是失真。
    """
    rid = _request_id(request)
    body = AppError(code, message, details=details).to_body(rid)
    return JSONResponse(status_code=status_code, content=body, headers={"X-Request-ID": rid})


_UNPRIVILEGED_ENVS = frozenset({"dev", "test", "local"})


def _is_rls_bypassed(*, rolsuper: bool, rolbypassrls: bool) -> bool:
    """表 owner / superuser 会绕过 RLS。纯函数，便于在不连库的情况下单测。"""
    return bool(rolsuper or rolbypassrls)


async def _warn_if_db_role_bypasses_rls() -> None:
    """非开发环境用 owner 角色连接时告警：RLS 这层兜底等于不存在。

    只告警、不阻断：把"连接角色选错"做成启动失败，会连带让迁移与本地开发都起不来；
    而 RLS 本来就是第二道网（第一道是应用层谓词，测试里有覆盖）。探测本身失败也只
    告警——启动路径上不该因为一条诊断查询挂掉。
    """
    if settings.env.strip().lower() in _UNPRIVILEGED_ENVS:
        return
    try:
        # 延迟导入：让 `import pyknora.api.main` 不强制拉起数据库引擎。
        from sqlalchemy import text as sql_text

        from pyknora.db.session import session_scope

        async with session_scope() as session:
            row = (
                await session.execute(
                    sql_text(
                        "SELECT current_user AS rolname, rolsuper, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).first()
    except Exception:  # noqa: BLE001 - 诊断失败不能拦住启动
        logger.warning("db_role_probe_failed", exc_info=True)
        return
    if row is not None and _is_rls_bypassed(rolsuper=row.rolsuper, rolbypassrls=row.rolbypassrls):
        logger.warning(
            "db_role_bypasses_rls role=%s：生产应改用非表 owner 角色（如 pyknora_app），"
            "否则 RLS 第二道网不生效",
            row.rolname,
        )


async def _warm_up_embedder(embedder: Any) -> None:
    """把「第一次查询」的加载成本搬到启动期。

    本地 HF 后端首次 ``embed`` 才真正加载权重（本机实测 24s，且若 HuggingFace
    不可达还会先耗上百秒重试）。这段成本若留在首个请求里，会直接吃掉整轮 chat
    预算（``chat_budget_timeout_s`` 25s）——用户看到的是 BUDGET_EXCEEDED 而不是
    答案，排查时又完全不像是「模型没加载完」。

    失败不拦启动：预热只是性能优化，模型真不可用时应在请求路径上按原逻辑报错，
    而不是让整个 API 起不来（否则一次网络抖动就能把服务钉死）。
    """
    started = time.perf_counter()
    try:
        await embedder.embed(["预热"])
    except Exception:  # noqa: BLE001 - 预热失败只降级，不改变启动语义
        logger.warning(
            "embedder warmup failed; the first query will pay the load cost",
            exc_info=True,
        )
        return
    logger.info("embedder warmup done in %.2fs", time.perf_counter() - started)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("pyknora api starting")
    state = get_app_state()
    if state.embedder is None:
        # 建立 provider 不会立即加载模型；这里显式预热，见 _warm_up_embedder。
        state.embedder = build_embedder(settings)
        await _warm_up_embedder(state.embedder)
    await _warn_if_db_role_bypasses_rls()
    try:
        yield
    finally:
        # 不 dispose 的话，pytest 每次建 app 都会留一个连接池，
        # 几十个用例之后 PG 就 max_connections 了。
        await dispose_engine()
        logger.info("pyknora api stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="PyKnora",
        version="0.1.0",
        description="企业知识库：增量索引与权限隔离（成员2 交付面：摄取与索引）",
        lifespan=_lifespan,
    )

    @app.middleware("http")
    async def _attach_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(AppError)
    async def _on_app_error(request: Request, exc: AppError) -> JSONResponse:
        # 4xx 不记 error 级：用户传错参数不是系统故障，记了会把真故障淹掉。
        if exc.http_status >= 500:
            logger.error("app error %s: %s", exc.code, exc.message)
        else:
            logger.info("app error %s: %s", exc.code, exc.message)
        return _error_response(exc, request)

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 只留"定位三元组"。`exc.errors()` 每项还带 `input` / `ctx`，那是请求体原文
        # 的切片——注册接口的密码就在里面，不能回显给客户端、也不该落进响应体。
        errors = [
            {
                "loc": [str(part) for part in error.get("loc", ())],
                "type": str(error.get("type", "")),
                "msg": str(error.get("msg", "")),
            }
            for error in exc.errors()[:_MAX_VALIDATION_ERRORS]
        ]
        rid = _request_id(request)
        # 参数错误是用户侧失误，记 info 不记 error（与 `_on_app_error` 同一口径）。
        logger.info("request validation failed request_id=%s errors=%d", rid, len(errors))
        return _unified_error_response(
            status_code=422,
            code=ErrorCode.INVALID_REQUEST,
            message="request validation failed",
            details={"errors": errors},
            request=request,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _on_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODE_BY_STATUS.get(exc.status_code)
        if code is None:
            code = ErrorCode.INTERNAL_ERROR if exc.status_code >= 500 else ErrorCode.INVALID_REQUEST
        # `detail` 是框架固定短语（"Not Found" / "Method Not Allowed"），不是用户输入，
        # 原样当 message 用；它不是字符串时（极少见）退回通用文案。
        message = exc.detail if isinstance(exc.detail, str) and exc.detail else "request failed"
        if exc.status_code >= 500:
            logger.error("http error %s: %s", exc.status_code, message)
        else:
            logger.info("http error %s: %s", exc.status_code, message)
        return _unified_error_response(
            status_code=exc.status_code,
            code=code,
            message=message,
            details={},
            request=request,
        )

    @app.exception_handler(Exception)
    async def _on_unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = _request_id(request)
        # 堆栈只进日志，不进响应体。
        logger.exception("unhandled error request_id=%s", rid)
        body = {
            "error": {
                "code": ErrorCode.INTERNAL_ERROR,
                "message": "internal error",
                "retryable": True,
                "details": {},
            },
            "request_id": rid,
        }
        return JSONResponse(status_code=500, content=body, headers={"X-Request-ID": rid})

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, Any]:
        """存活探针。**不查数据库**——查了就会在 PG 抖动时被误判为 API 挂了。"""
        state = get_app_state()
        return {
            "status": "ok",
            "kb_repo": type(state.kb_repo).__name__ if state.kb_repo else None,
            "embedder": getattr(state.embedder, "model_key", None),
        }

    from pyknora.api.v1 import admin as admin_router
    from pyknora.api.v1 import auth as auth_router
    from pyknora.api.v1 import chat as chat_router
    from pyknora.api.v1 import documents as documents_router
    from pyknora.api.v1 import jobs as jobs_router
    from pyknora.api.v1 import knowledge_bases as knowledge_bases_router
    from pyknora.api.v1 import lineage as lineage_router
    from pyknora.api.v1 import query as query_router
    from pyknora.api.v1 import workspaces as workspaces_router

    app.include_router(
        documents_router.router,
        prefix="/api/v1",
        dependencies=[Depends(require_csrf)],
    )
    app.include_router(
        jobs_router.router,
        prefix="/api/v1",
        dependencies=[Depends(require_csrf)],
    )
    # 成员4：注册/登录必须在其它业务路由之前可用——它们是所有鉴权的前置。
    app.include_router(auth_router.router, prefix="/api/v1")
    app.include_router(
        workspaces_router.router,
        prefix="/api/v1",
        dependencies=[Depends(require_csrf)],
    )

    app.include_router(
        query_router.router,
        prefix="/api/v1",
        dependencies=[Depends(require_csrf)],
    )
    app.include_router(
        chat_router.router,
        prefix="/api/v1",
        dependencies=[Depends(require_csrf)],
    )
    app.include_router(
        knowledge_bases_router.router,
        prefix="/api/v1",
        dependencies=[Depends(require_csrf)],
    )
    app.include_router(
        lineage_router.router,
        prefix="/api/v1",
        dependencies=[Depends(require_csrf)],
    )
    app.include_router(
        admin_router.router,
        prefix="/api/v1",
        dependencies=[Depends(require_csrf)],
    )

    return app


app = create_app()
