"""FastAPI 应用入口。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.core.config import get_settings
from apps.api.core.db import dispose_engine, get_engine
from apps.api.core.errors import AppError
from apps.api.core.logging import configure_logging, get_logger, new_trace_id, trace_id_var
from apps.api.core.redis import close_redis, get_redis
from apps.api.modules.auth.router import router as auth_router

settings = get_settings()
configure_logging(level=settings.log_level, json_output=settings.is_production)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    log.info("api.starting", env=settings.env)
    yield
    await dispose_engine()
    await close_redis()
    log.info("api.stopped")


app = FastAPI(
    title="AIGC Studio API",
    version="0.1.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_middleware(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
    # 客户端可以透传 trace_id 便于端到端排查；没有就生成一个。
    trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
    token = trace_id_var.set(trace_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        trace_id_var.reset(token)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Trace-Id"] = trace_id
    log.info(
        "http.request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=elapsed_ms,
    )
    return response


# ---------------------------------------------------------------- 异常处理
#
# 所有异常都收敛成 21_ErrorTaxonomy.md 第 1 节的统一错误体，
# 绝不把框架原始报错或上游报错直接透给用户。


@app.exception_handler(AppError)
async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
    log.warning("app.error", code=exc.code, message=exc.message, detail=exc.detail)
    return JSONResponse(
        status_code=exc.http_status,
        content=exc.to_payload(trace_id_var.get()),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # 不能直接塞 exc.errors()：自定义校验器抛 ValueError 时，pydantic 会把
    # 异常对象本身放进 ctx，JSON 序列化会炸，422 变成 500。
    # 只取客户端真正需要的三个字段，顺带避免泄露内部结构。
    details = [
        {
            "field": ".".join(str(p) for p in e["loc"][1:]) or "body",
            "message": str(e.get("msg", "")),
            "type": str(e.get("type", "")),
        }
        for e in exc.errors()
    ]
    err = AppError("common.validation_failed", detail={"errors": details})
    return JSONResponse(status_code=422, content=err.to_payload(trace_id_var.get()))


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {403: "common.forbidden", 404: "common.not_found"}.get(
        exc.status_code, "common.internal"
    )
    err = AppError(code, message=str(exc.detail))
    return JSONResponse(status_code=exc.status_code, content=err.to_payload(trace_id_var.get()))


@app.exception_handler(Exception)
async def handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
    # 未捕获异常：日志留全栈，但对外只给通用文案，不泄露内部细节。
    log.exception("app.unhandled", error=repr(exc))
    err = AppError("common.internal", message=repr(exc))
    return JSONResponse(status_code=500, content=err.to_payload(trace_id_var.get()))


# ---------------------------------------------------------------- 路由

API_PREFIX = "/api/v1"

app.include_router(auth_router, prefix=API_PREFIX)


# ---------------------------------------------------------------- 健康检查


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    """存活探针：不查依赖，只证明进程还在。"""
    return {"status": "ok", "version": app.version}


@app.get("/readyz", tags=["system"])
async def readyz() -> JSONResponse:
    """就绪探针：依赖不通就不该接流量。"""
    checks: dict[str, str] = {}

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"error: {type(exc).__name__}"

    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "not_ready", "checks": checks},
    )
