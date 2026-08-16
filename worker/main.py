"""Arq Worker 入口（ADR-010）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from apps.api.core.config import get_settings
from apps.api.core.db import dispose_engine
from apps.api.core.logging import configure_logging, get_logger
from worker.jobs.maintenance import purge_abandoned_uploads

settings = get_settings()
configure_logging(level=settings.log_level, json_output=settings.is_production)
log = get_logger(__name__)


async def ping(_ctx: dict[str, Any], message: str = "pong") -> str:
    """自检任务。"""
    log.info("worker.ping", message=message)
    return message


async def startup(_ctx: dict[str, Any]) -> None:
    log.info("worker.starting", env=settings.env)


async def shutdown(_ctx: dict[str, Any]) -> None:
    await dispose_engine()
    log.info("worker.stopped")


class WorkerSettings:
    # 新增任务函数必须登记到这里，否则 Worker 收到任务会报 unknown function。
    functions: ClassVar[list[Callable[..., Any]]] = [ping, purge_abandoned_uploads]

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
    job_timeout = 900  # 视频任务可能跑很久，S4 会按任务类型分队列细化
    keep_result = 3600
