from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, status

from apps.api.core.errors import AppError
from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.project import service
from apps.api.modules.project.schemas import (
    ProjectCreateIn,
    ProjectOut,
    ProjectPage,
    ProjectUpdateIn,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _decode_cursor(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AppError("common.validation_failed", message="cursor 格式非法") from exc


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreateIn, user: CurrentUser, db: DbSession) -> ProjectOut:
    row = await service.create_project(
        db,
        org_id=user.org_id,
        owner_user_id=user.id,
        title=payload.title,
        route_type=payload.route_type,
        budget_cap_credits=payload.budget_cap_credits,
    )
    return ProjectOut.model_validate(row)


@router.get("", response_model=ProjectPage)
async def list_projects(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
    project_status: str | None = Query(None, alias="status"),
) -> ProjectPage:
    rows, next_cursor = await service.list_projects(
        db,
        org_id=user.org_id,
        limit=limit,
        cursor=_decode_cursor(cursor),
        status=project_status,
    )
    return ProjectPage(
        items=[ProjectOut.model_validate(r) for r in rows],
        next_cursor=next_cursor.isoformat() if next_cursor else None,
    )


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ProjectOut:
    row = await service.get_project(db, org_id=user.org_id, project_id=project_id)
    return ProjectOut.model_validate(row)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID, payload: ProjectUpdateIn, user: CurrentUser, db: DbSession
) -> ProjectOut:
    row = await service.update_project(
        db,
        org_id=user.org_id,
        project_id=project_id,
        title=payload.title,
        route_type=payload.route_type,
        budget_cap_credits=payload.budget_cap_credits,
    )
    return ProjectOut.model_validate(row)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await service.delete_project(db, org_id=user.org_id, project_id=project_id)
