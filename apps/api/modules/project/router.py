from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, status

from apps.api.core.errors import AppError
from apps.api.modules.agent import service as agent_service
from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.consistency import service as consistency_service
from apps.api.modules.project import service
from apps.api.modules.project.models import ADAPTATION_MODES
from apps.api.modules.project.schemas import (
    ProjectCreateIn,
    ProjectLockVariablesIn,
    ProjectLockVariablesOut,
    ProjectModelPreferenceIn,
    ProjectOut,
    ProjectPage,
    ProjectStateOut,
    ProjectUpdateIn,
    StyleOptionOut,
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


async def _lock_out(
    db: DbSession, project_id: uuid.UUID, lock: object | None
) -> ProjectLockVariablesOut:
    """把锁定变量拼成响应，并带上可选项。

    `lock is None` = 这个项目还没走到门①，一行都没有。此时返回一份空值 +
    完整可选项，而不是 404：界面要在门① 之前就能展示"可以选哪些画风"，
    而"还没选过"本身是一个合法状态，不是资源不存在。
    """
    options = [
        StyleOptionOut.model_validate(e) for e in await consistency_service.list_style_catalog(db)
    ]
    if lock is None:
        return ProjectLockVariablesOut(
            project_id=project_id,
            style_key="",
            era="",
            region="",
            ethnicity="",
            era_evidence="",
            adaptation_mode="adapt",
            origin="detected",
            confirmed_at=None,
            anchors_confirmed_at=None,
            legacy_unconfirmed=False,
            style_options=options,
            adaptation_options=list(ADAPTATION_MODES),
        )
    out = ProjectLockVariablesOut.model_validate(lock)
    return out.model_copy(
        update={"style_options": options, "adaptation_options": list(ADAPTATION_MODES)}
    )


@router.get("/{project_id}/lock-variables", response_model=ProjectLockVariablesOut)
async def get_lock_variables(
    project_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ProjectLockVariablesOut:
    """门① 锁定的项目级变量：画风、时代背景与人种、改编模式。

    跨租户与不存在一律 404（由 `service.get_project` 保证），不返回 403。
    """
    await service.get_project(db, org_id=user.org_id, project_id=project_id)
    lock = await service.get_lock_variables(db, org_id=user.org_id, project_id=project_id)
    return await _lock_out(db, project_id, lock)


@router.put("/{project_id}/lock-variables", response_model=ProjectLockVariablesOut)
async def set_lock_variables(
    project_id: uuid.UUID,
    payload: ProjectLockVariablesIn,
    user: CurrentUser,
    db: DbSession,
) -> ProjectLockVariablesOut:
    """改门① 的锁定变量。只改传了的字段，没传的保持不动。

    **PUT 而不是 POST**：这是"这个项目的锁定变量就是它"，重复调用结果
    相同，没有第二份被创建出来。POST 到同一路径会让人以为每调一次
    就多一份设定。

    确认过之后仍然允许改时代背景与改编模式——它们只影响还没跑的阶段。
    画风是例外：一旦风格档案建出来就冻结了，改它要重出全部已生成的画面，
    service 层会直接拒绝，而不是让一次无效操作看起来成功了。
    """
    lock = await service.set_lock_variables(
        db,
        org_id=user.org_id,
        project_id=project_id,
        style_key=payload.style_key,
        era=payload.era,
        region=payload.region,
        ethnicity=payload.ethnicity,
        adaptation_mode=payload.adaptation_mode,
    )
    return await _lock_out(db, project_id, lock)


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
