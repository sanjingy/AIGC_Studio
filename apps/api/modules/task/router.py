from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Header, Query, Response, status

from apps.api.core.errors import AppError
from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.project import service as project_service
from apps.api.modules.task import service
from apps.api.modules.task.schemas import TaskCreateIn, TaskOut, TaskPage

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _decode_cursor(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AppError("common.validation_failed", message="cursor 格式非法") from exc


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreateIn,
    user: CurrentUser,
    db: DbSession,
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> TaskOut:
    if payload.project_id is not None:
        await project_service.get_project(db, org_id=user.org_id, project_id=payload.project_id)

    task, created = await service.create_task(
        db,
        org_id=user.org_id,
        project_id=payload.project_id,
        created_by=user.id,
        task_type=payload.type,
        input_json=payload.input,
        priority=payload.priority,
        idempotency_key=idempotency_key,
    )
    # 幂等命中时返回 200 而非 201，让客户端能分辨"新建了"和"已存在"
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return TaskOut.model_validate(task)


@router.get("", response_model=TaskPage)
async def list_tasks(
    user: CurrentUser,
    db: DbSession,
    project_id: uuid.UUID | None = None,
    task_status: str | None = Query(None, alias="status"),
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = None,
) -> TaskPage:
    rows, next_cursor = await service.list_tasks(
        db,
        org_id=user.org_id,
        project_id=project_id,
        status=task_status,
        limit=limit,
        cursor=_decode_cursor(cursor),
    )
    return TaskPage(
        items=[TaskOut.model_validate(r) for r in rows],
        next_cursor=next_cursor.isoformat() if next_cursor else None,
    )


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TaskOut:
    return TaskOut.model_validate(await service.get_task(db, org_id=user.org_id, task_id=task_id))


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(task_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TaskOut:
    return TaskOut.model_validate(
        await service.cancel_task(db, org_id=user.org_id, task_id=task_id)
    )


@router.post("/{task_id}/retry", response_model=TaskOut)
async def retry_task(task_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TaskOut:
    return TaskOut.model_validate(await service.retry_task(db, org_id=user.org_id, task_id=task_id))
