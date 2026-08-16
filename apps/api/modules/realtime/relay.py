"""发件箱中继（ADR-019）。

不断把 `outbox_events` 里未投递的事件搬到 Redis Stream。
在 Worker 进程里作为后台循环运行。

为什么不在 API 进程里跑：API 会水平扩容，多个副本同时中继会重复投递
（虽然 SELECT FOR UPDATE SKIP LOCKED 挡得住，但没必要）。
Worker 只有一个逻辑角色，更适合。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

from apps.api.core.db import session_scope
from apps.api.core.logging import get_logger
from apps.api.modules.realtime import stream
from apps.api.modules.task import repository as task_repo

log = get_logger(__name__)

POLL_INTERVAL_SECONDS = 0.2
BATCH_SIZE = 200


async def relay_once() -> int:
    """搬一批。返回投递条数。"""
    async with session_scope() as db:
        rows = await task_repo.fetch_unpublished(db, limit=BATCH_SIZE)
        if not rows:
            return 0

        published: list[uuid.UUID] = []
        for row in rows:
            if row.project_id is None:
                # 没有项目归属的事件暂时无处推送，直接标记已处理，
                # 否则它们会永远堵在发件箱头部。
                published.append(row.id)
                continue
            try:
                await stream.publish(
                    project_id=str(row.project_id),
                    event_type=row.type,
                    payload={
                        "type": row.type,
                        "project_id": str(row.project_id),
                        "ts": row.created_at.isoformat(),
                        "data": row.data_json,
                    },
                )
                published.append(row.id)
            except Exception as exc:
                # 投递失败就停在这里，保持顺序。下一轮重试。
                log.warning("relay.publish_failed", outbox_id=str(row.id), error=repr(exc))
                break

        await task_repo.mark_published(db, ids=published)
        return len(published)


async def run_forever(stop: asyncio.Event) -> None:
    log.info("relay.started", interval=POLL_INTERVAL_SECONDS)
    while not stop.is_set():
        try:
            moved = await relay_once()
        except Exception as exc:
            # 中继挂掉等于所有前端永远转圈，绝不能让异常终止循环
            log.exception("relay.iteration_failed", error=repr(exc))
            moved = 0

        if moved == 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_SECONDS)
    log.info("relay.stopped")


def sse_frame(entry_id: str, fields: dict[str, str]) -> str:
    """把 Stream 条目渲染成 SSE 帧。"""
    data = fields.get("data") or json.dumps({})
    event_type = fields.get("type", "message")
    return f"id: {entry_id}\nevent: {event_type}\ndata: {data}\n\n"
