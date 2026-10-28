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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pyknora.core.errors import AppError, ErrorCode
from pyknora.db.session import dispose_engine

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


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("pyknora api starting")
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

    from pyknora.api.v1 import documents as documents_router
    from pyknora.api.v1 import jobs as jobs_router
    from pyknora.api.v1 import knowledge_bases as knowledge_bases_router

    app.include_router(documents_router.router, prefix="/api/v1")
    app.include_router(jobs_router.router, prefix="/api/v1")
    app.include_router(knowledge_bases_router.router, prefix="/api/v1")
    return app


app = create_app()
