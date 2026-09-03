from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, status

from apps.api.core.errors import AppError
from apps.api.modules.agent import service as agent_service
from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.project import service
from apps.api.modules.project.schemas import (
    ProjectCreateIn,
    ProjectModelPreferenceIn,
    ProjectOut,
    ProjectPage,
    ProjectStateOut,
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


@router.get("/{project_id}/state", response_model=ProjectStateOut)
async def get_project_state(
    project_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ProjectStateOut:
    """读这个项目的编排状态（ADR-008 的唯一权威），**只读**。

    为什么需要它：在这之前 `current_state_json` 没有任何 GET 接口。
    `ProjectOut` 只透出了它派生的 `stale_roles`，阶段和五份产出都读不到，
    于是：

    - 前端只能从 `agent_runs` 反推当前阶段，反推不出被 `changes_requested`
      退回的情形以外的边界；
    - ADR-029 的字段级 Patch 写进 `current_state_json`，而界面读 `agent_runs`
      ——保存成功、刷新回旧值，等于没有写路径。

    阶段走 `agent.service.current_stage`，不是直接把 JSONB 里的字符串抛出去：
    存量项目还带着 `story` / `visual` 这类旧阶段名，那两个值在现行 `_NEXT`
    里根本不存在。

    跨租户与不存在一律 404（由 `service.get_project` 保证），不返回 403。
    """
    row = await service.get_project(db, org_id=user.org_id, project_id=project_id)
    state = dict(row.current_state_json or {})
    return ProjectStateOut(
        project_id=row.id,
        stage=agent_service.current_stage(state),
        current_state_json=state,
        stale_roles=agent_service.stale_roles_of(state),
        updated_at=row.updated_at,
    )


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


@router.patch("/{project_id}/model-preference", response_model=ProjectOut)
async def set_model_preference(
    project_id: uuid.UUID,
    payload: ProjectModelPreferenceIn,
    user: CurrentUser,
    db: DbSession,
) -> ProjectOut:
    """按能力覆盖这个项目用哪个模型（ADR-024）。

    单独一条端点而不是塞进 `PATCH /projects/{id}`：那条是"改项目的属性"，
    每个字段一列；这条改的是一张 JSONB 字典里的**一个键**，语义是
    "合并"不是"替换"。混在一起，前端就得为了改一个模型把整份偏好
    读出来再整个传回去——两个标签页同时开着必然互相覆盖。

    `model_id=null` 表示清掉该能力的偏好，回到 Gateway 的默认优先级。
    """
    row = await service.set_model_preference(
        db,
        org_id=user.org_id,
        project_id=project_id,
        capability=payload.capability,
        model_id=payload.model_id,
    )
    return ProjectOut.model_validate(row)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await service.delete_project(db, org_id=user.org_id, project_id=project_id)
