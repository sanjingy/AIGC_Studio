"""Arq Worker 入口（ADR-010）。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from apps.api.core.config import get_settings
from apps.api.core.db import dispose_engine
from apps.api.core.logging import configure_logging, get_logger
from apps.api.core.redis import close_redis
from apps.api.modules.realtime.relay import run_forever as relay_run_forever
from worker.jobs.execute import execute_task
from worker.jobs.maintenance import purge_abandoned_uploads

settings = get_settings()
configure_logging(level=settings.log_level, json_output=settings.is_production)
log = get_logger(__name__)

_RELAY_STOP = "relay_stop"
_RELAY_TASK = "relay_task"


async def ping(ctx: dict[Any, Any], message: str = "pong", *_a: Any, **_kw: Any) -> str:
    """自检任务。"""
    del ctx
    log.info("worker.ping", message=message)
    return message


async def startup(ctx: dict[Any, Any]) -> None:
    log.info("worker.starting", env=settings.env)
    # 发件箱中继随 Worker 一起跑（ADR-019）。
    # 放在这里而不是 API 进程：API 会水平扩容，多副本中继没必要。
    stop = asyncio.Event()
    ctx[_RELAY_STOP] = stop
    ctx[_RELAY_TASK] = asyncio.create_task(relay_run_forever(stop))


async def shutdown(ctx: dict[Any, Any]) -> None:
    if stop := ctx.get(_RELAY_STOP):
        stop.set()
    if task := ctx.get(_RELAY_TASK):
        try:
            await asyncio.wait_for(task, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
    await dispose_engine()
    await close_redis()
    log.info("worker.stopped")


class WorkerSettings:
    # 新增任务函数必须登记到这里，否则 Worker 收到任务会报 unknown function。
    functions: ClassVar[list[Callable[..., Any]]] = [
        ping,
        execute_task,
        purge_abandoned_uploads,
    ]

    cron_jobs: ClassVar[list[Any]] = [
        # 每小时回收一次超时未完成的上传
        cron(purge_abandoned_uploads, minute=17, run_at_startup=False),
    ]

    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        database=settings.redis_db,
    )
    # 4 核机器，先给 8。真实并发上限由 Provider 限流决定，不由这里决定。
    max_jobs = 8
    job_timeout = 900  # 视频任务可能跑很久，S6 会按任务类型分队列细化
    keep_result = 3600
