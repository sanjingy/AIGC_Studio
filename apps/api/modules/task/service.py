"""任务模块的唯一对外入口。

状态权威（ADR-008）：`tasks.status` 是全系统执行状态的唯一真相。
所有状态跃迁都必须经过这里，且必须在同一事务内写发件箱事件。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.config import get_settings
from apps.api.core.errors import ERRORS, AppError
from apps.api.core.logging import get_logger
from apps.api.modules.task import events
from apps.api.modules.task import repository as repo
from apps.api.modules.task.models import ALLOWED_TRANSITIONS, TASK_TYPES, Task

log = get_logger(__name__)

MAX_PAGE_SIZE = 100
EXECUTE_JOB = "execute_task"


class InvalidTransitionError(AppError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            "common.conflict",
            message=f"非法状态跃迁 {current} -> {target}",
            detail={"from": current, "to": target},
        )


def _check_transition(current: str, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransitionError(current, target)


async def _enqueue(task_id: uuid.UUID) -> None:
    s = get_settings()
    pool = await create_pool(
        RedisSettings(host=s.redis_host, port=s.redis_port, database=s.redis_db)
    )
    try:
        # _job_id 用任务 ID：同一个任务重复入队会被 arq 去重，
        # 避免"点两次重试跑两遍"这种直接翻倍成本的问题。
        await pool.enqueue_job(EXECUTE_JOB, str(task_id), _job_id=f"task:{task_id}")
    finally:
        await pool.aclose()


async def create_task(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    created_by: uuid.UUID,
    task_type: str,
    input_json: dict[str, Any] | None = None,
    priority: int = 100,
    max_attempts: int = 3,
    idempotency_key: str | None = None,
) -> tuple[Task, bool]:
    """建任务并入队。返回 (任务, 是否新建)。

    幂等键命中时直接返回已有任务，不重复创建——这是防止
    "网络抖动导致客户端重发、结果扣了两次钱"的第一道闸。
    """
    if task_type not in TASK_TYPES:
        raise AppError("provider.params.invalid", message=f"unknown task type {task_type}")

    if idempotency_key:
        existing = await repo.get_by_idempotency_key(db, key=idempotency_key)
        if existing is not None:
            if existing.org_id != org_id:
                # 幂等键撞到别的租户：绝不能返回对方的任务
                raise AppError("common.conflict", message="idempotency key conflict")
            return existing, False

    try:
        task = await repo.create(
            db,
            org_id=org_id,
            project_id=project_id,
            created_by=created_by,
            task_type=task_type,
            input_json=input_json or {},
            priority=priority,
            max_attempts=max_attempts,
            idempotency_key=idempotency_key,
        )
        await events.emit(
            db,
            org_id=org_id,
            project_id=project_id,
            type=events.EVENT_TASK_CREATED,
            data=_snapshot(task),
        )
        await db.commit()
    except IntegrityError:
        # 并发创建同一个幂等键，唯一约束兜底
        await db.rollback()
        if idempotency_key and (
            existing := await repo.get_by_idempotency_key(db, key=idempotency_key)
        ):
            return existing, False
        raise

    await _enqueue(task.id)
    return task, True


async def get_task(db: AsyncSession, *, org_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    row = await repo.get(db, org_id=org_id, task_id=task_id)
    if row is None:
        raise AppError("common.not_found", message=f"task {task_id}")
    return row


async def list_tasks(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 30,
    cursor: datetime | None = None,
) -> tuple[list[Task], datetime | None]:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    rows = await repo.list_page(
        db,
        org_id=org_id,
        project_id=project_id,
        status=status,
        limit=limit + 1,
        cursor=cursor,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    return page, (page[-1].created_at if has_more and page else None)


async def cancel_task(db: AsyncSession, *, org_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    task = await get_task(db, org_id=org_id, task_id=task_id)
    _check_transition(task.status, "cancelled")

    await repo.mark_finished(db, task, status="cancelled")
    await events.emit(
        db,
        org_id=task.org_id,
        project_id=task.project_id,
        type=events.EVENT_TASK_CANCELLED,
        data=_snapshot(task),
    )
    await db.commit()
    return task


async def retry_task(db: AsyncSession, *, org_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    task = await get_task(db, org_id=org_id, task_id=task_id)
    _check_transition(task.status, "queued")

    spec = ERRORS.get(task.error_code or "")
    if spec is not None and not spec.retryable:
        raise AppError(
            "common.conflict",
            message=f"错误 {task.error_code} 不可重试",
            detail={"error_code": task.error_code},
        )

    task.status = "queued"
    task.error_code = None
    task.error_detail = None
    task.finished_at = None
    task.progress = 0
    await events.emit(
        db,
        org_id=task.org_id,
        project_id=task.project_id,
        type=events.EVENT_TASK_CREATED,
        data=_snapshot(task),
    )
    await db.commit()

    await _enqueue(task.id)
    return task


# ---------------------------------------------------------------- Worker 侧
#
# 下面三个函数由 Worker 调用，不带 org_id——Worker 是系统身份。


async def begin_execution(db: AsyncSession, *, task_id: uuid.UUID) -> Task | None:
    """把任务置为 running。已终态或已在跑则返回 None，表示不该执行。"""
    task = await repo.get_for_worker(db, task_id=task_id)
    if task is None:
        log.warning("task.missing", task_id=str(task_id))
        return None

    if task.status != "queued":
        # 取消后仍被 Worker 捞到、或重复投递，都会走到这里。静默跳过。
        log.info("task.skip_execution", task_id=str(task_id), status=task.status)
        return None

    await repo.mark_started(db, task)
    await events.emit(
        db,
        org_id=task.org_id,
        project_id=task.project_id,
        type=events.EVENT_TASK_STARTED,
        data=_snapshot(task),
    )
    await db.commit()
    return task


async def report_progress(
    db: AsyncSession, *, task_id: uuid.UUID, percent: int, stage: str | None = None
) -> None:
    task = await repo.get_for_worker(db, task_id=task_id)
    if task is None or task.status != "running":
        return

    task.progress = max(0, min(100, percent))
    await events.emit(
        db,
        org_id=task.org_id,
        project_id=task.project_id,
        type=events.EVENT_TASK_PROGRESS,
        data={**_snapshot(task), "stage": stage},
    )
    await db.commit()


async def finish_execution(
    db: AsyncSession,
    *,
    task_id: uuid.UUID,
    status: str,
    output: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> None:
    task = await repo.get_for_worker(db, task_id=task_id)
    if task is None:
        return
    if task.status != "running":
        # 执行过程中被取消了。不要把它改回 succeeded/failed——
        # 用户看到的"已取消"必须是最终状态。
        log.info("task.finish_ignored", task_id=str(task_id), status=task.status)
        return

    spec = ERRORS.get(error_code or "")
    await repo.mark_finished(
        db,
        task,
        status=status,
        output=output,
        error_code=error_code,
        error_detail=error_detail,
        counts_as_waste=bool(spec and spec.counts_as_waste),
    )
    await events.emit(
        db,
        org_id=task.org_id,
        project_id=task.project_id,
        type=(events.EVENT_TASK_SUCCEEDED if status == "succeeded" else events.EVENT_TASK_FAILED),
        data=_snapshot(task),
    )
    await db.commit()


def _snapshot(task: Task) -> dict[str, Any]:
    """事件里带任务的完整最终状态，不带增量指令。

    SSE 重连必然产生重复事件，客户端拿最终状态覆盖即可，无需去重逻辑。
    """
    return {
        "task_id": str(task.id),
        "project_id": str(task.project_id) if task.project_id else None,
        "type": task.type,
        "status": task.status,
        "progress": task.progress,
        "attempt": task.attempt,
        "error_code": task.error_code,
        "actual_cost": task.actual_cost,
    }
