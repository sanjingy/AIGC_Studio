"""SSE 事件通道（20_Realtime.md / ADR-013）。"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.core.redis import get_redis
from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.project import service as project_service
from apps.api.modules.realtime import stream
from apps.api.modules.realtime.relay import sse_frame

log = get_logger(__name__)

router = APIRouter(tags=["realtime"])

TICKET_TTL_SECONDS = 60
HEARTBEAT_SECONDS = 15
MAX_CONNECTION_SECONDS = 1800
REPLAY_LIMIT = 500


class TicketOut(BaseModel):
    ticket: str
    expires_in: int


def _ticket_key(ticket: str) -> str:
    return f"sse:ticket:{ticket}"


@router.post("/projects/{project_id}/events/ticket", response_model=TicketOut)
async def create_ticket(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TicketOut:
    """换取一次性票据。

    EventSource 不能自定义请求头，所以没法直接带 Bearer。
    但也**不能把长期 JWT 放进 query**——URL 会进 Nginx access log。
    折中是一次性短票据：泄露了也只有 60 秒且只能用一次。
    """
    await project_service.get_project(db, org_id=user.org_id, project_id=project_id)

    ticket = secrets.token_urlsafe(32)
    await get_redis().setex(_ticket_key(ticket), TICKET_TTL_SECONDS, f"{user.org_id}:{project_id}")
    return TicketOut(ticket=ticket, expires_in=TICKET_TTL_SECONDS)


@router.get("/projects/{project_id}/events")
async def project_events(
    project_id: uuid.UUID,
    request: Request,
    ticket: str = Query(...),
) -> StreamingResponse:
    r = get_redis()
    # GETDEL：票据用掉即焚，重放无效
    raw = await r.getdel(_ticket_key(ticket))
    if not raw or raw.split(":", 1)[1] != str(project_id):
        raise AppError("auth.token.invalid", message="invalid or expired sse ticket")

    # 浏览器重连时自动带这个头
    last_id = request.headers.get("Last-Event-ID", "")
    return StreamingResponse(
        _event_source(project_id=str(project_id), last_id=last_id, request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Nginx 必须关缓冲，否则 SSE 会被攒着不发
            "X-Accel-Buffering": "no",
        },
    )


async def _event_source(*, project_id: str, last_id: str, request: Request) -> AsyncIterator[str]:
    cursor = last_id or await stream.latest_id(project_id=project_id)

    if last_id:
        if await stream.cursor_is_fresh(project_id=project_id, entry_id=last_id):
            # 补齐断线期间错过的事件
            for entry_id, fields in await stream.read_since(
                project_id=project_id, last_id=last_id, count=REPLAY_LIMIT
            ):
                yield sse_frame(entry_id, fields)
                cursor = entry_id
        else:
            # 游标已被修剪掉，中间有缺口。告诉客户端全量拉取，
            # 不要假装无缝续上——那会留下永久性的状态错误。
            yield 'event: sync.required\ndata: {"reason":"cursor_expired"}\n\n'
            cursor = await stream.latest_id(project_id=project_id)

    deadline = asyncio.get_running_loop().time() + MAX_CONNECTION_SECONDS

    try:
        while True:
            if await request.is_disconnected():
                break
            if asyncio.get_running_loop().time() > deadline:
                # 主动断开让客户端重连，避免连接无限期挂着
                yield 'event: reconnect\ndata: {"reason":"max_duration"}\n\n'
                break

            entries = await stream.read_blocking(
                project_id=project_id, last_id=cursor, block_ms=HEARTBEAT_SECONDS * 1000
            )
            if not entries:
                # 心跳：防止中间层把空闲连接掐掉
                yield ": keepalive\n\n"
                continue

            for entry_id, fields in entries:
                yield sse_frame(entry_id, fields)
                cursor = entry_id
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("sse.stream_error", project_id=project_id, error=repr(exc))
    finally:
        with contextlib.suppress(Exception):
            log.info("sse.closed", project_id=project_id)
