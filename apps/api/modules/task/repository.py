"""任务数据访问。模块私有。用户侧查询强制带 org_id。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.task.models import OutboxEvent, Task


def _scoped(org_id: uuid.UUID) -> Select[tuple[Task]]:
    return select(Task).where(Task.org_id == org_id, Task.deleted_at.is_(None))


async def create(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    created_by: uuid.UUID,
    task_type: str,
    input_json: dict[str, object],
    priority: int,
    max_attempts: int,
    idempotency_key: str | None,
) -> Task:
    row = Task(
        org_id=org_id,
        project_id=project_id,
        created_by=created_by,
        type=task_type,
        input_json=input_json,
        priority=priority,
        max_attempts=max_attempts,
        idempotency_key=idempotency_key,
    )
    db.add(row)
    await db.flush()
    return row


async def get(db: AsyncSession, *, org_id: uuid.UUID, task_id: uuid.UUID) -> Task | None:
    return (await db.execute(_scoped(org_id).where(Task.id == task_id))).scalar_one_or_none()


async def get_by_idempotency_key(db: AsyncSession, *, key: str) -> Task | None:
    """按幂等键查。

    不带 org_id：幂等键全局唯一，且这里只用于"是否已经建过"的判断，
    命中后仍会校验归属。
    """
    return (await db.execute(select(Task).where(Task.idempotency_key == key))).scalar_one_or_none()


async def get_for_worker(db: AsyncSession, *, task_id: uuid.UUID) -> Task | None:
    """Worker 侧取任务。

    不带 org_id：Worker 是系统身份，处理所有租户的任务。
    这是有意的例外，所以单独成函数而不是给上面的加个可选参数——
    可选参数迟早会被误用在用户请求路径上。
    """
    return (
        await db.execute(select(Task).where(Task.id == task_id, Task.deleted_at.is_(None)))
    ).scalar_one_or_none()


async def list_page(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    status: str | None,
    limit: int,
    cursor: datetime | None,
) -> list[Task]:
    stmt = _scoped(org_id)
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
    if status:
        stmt = stmt.where(Task.status == status)
    if cursor:
        stmt = stmt.where(Task.created_at < cursor)
    return list((await db.execute(stmt.order_by(Task.created_at.desc()).limit(limit))).scalars())


async def mark_started(db: AsyncSession, task: Task) -> None:
    task.status = "running"
    task.attempt += 1
    task.started_at = datetime.now(UTC)
    task.progress = 0


async def mark_finished(
    db: AsyncSession,
    task: Task,
    *,
    status: str,
    output: dict[str, object] | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    counts_as_waste: bool = False,
) -> None:
    task.status = status
    task.output_json = output
    task.error_code = error_code
    task.error_detail = error_detail
    task.counts_as_waste = counts_as_waste
    task.finished_at = datetime.now(UTC)
    if status == "succeeded":
        task.progress = 100


# ------------------------------------------------------------------ outbox


async def fetch_unpublished(db: AsyncSession, *, limit: int) -> list[OutboxEvent]:
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.seq)
        .limit(limit)
        # 多个中继实例时避免重复投递；单实例也无害
        .with_for_update(skip_locked=True)
    )
    return list((await db.execute(stmt)).scalars())


async def mark_published(db: AsyncSession, *, ids: list[uuid.UUID]) -> None:
    if not ids:
        return
    await db.execute(
        update(OutboxEvent).where(OutboxEvent.id.in_(ids)).values(published_at=datetime.now(UTC))
    )
