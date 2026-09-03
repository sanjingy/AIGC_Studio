from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query

from apps.api.core.errors import AppError
from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.content import service
from apps.api.modules.content.schemas import (
    OutputPatchIn,
    PatchResultOut,
    RevisionBatchOut,
    RevisionOut,
    RevisionPage,
    UndoIn,
)

router = APIRouter(prefix="/projects", tags=["content"])


def _decode_cursor(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AppError("common.validation_failed", message="cursor 格式非法") from exc


def _result(outcome: service.PatchOutcome) -> PatchResultOut:
    return PatchResultOut(
        batch_id=outcome.batch_id,
        role=outcome.role,
        changed=outcome.changed,
        output=outcome.output,
        stale_roles=outcome.stale_roles,
    )


@router.patch("/{project_id}/outputs/{role}", response_model=PatchResultOut)
async def patch_output(
    project_id: uuid.UUID,
    role: str,
    payload: OutputPatchIn,
    user: CurrentUser,
    db: DbSession,
) -> PatchResultOut:
    """按字段改这个阶段的产出。**一次请求 = 一次 UI 操作 = 一个可撤销的批次。**

    为什么是 PATCH 而不是 PUT：PUT 的语义是"整块就是它"，那会让前端
    把整份产出读出来再传回来——两个标签页同时开着必然互相覆盖，
    而且整块替换绕过了逐字段记账，撤销时无从判断哪一处是这次改的。

    为什么是一个数组而不是一次一条：撤销以批为单位。用户改了 5 个字段
    点一次保存，撤销要把 5 个一起退回去；一次一条就得让前端自己去
    拼这个批次，而它拼错了没人拦得住。

    **这条路径一分钱不花**：不建任务、不跑 Agent、不预扣也不结算。
    """
    outcome = await service.patch_output(
        db,
        org_id=user.org_id,
        project_id=project_id,
        role=role,
        patches=[(p.path, p.value) for p in payload.patches],
        reason=payload.reason,
        actor_user_id=user.id,
    )
    return _result(outcome)


@router.get("/{project_id}/revisions", response_model=RevisionPage)
async def list_revisions(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
    role: str | None = Query(None, description="只看某个阶段产出的改动"),
) -> RevisionPage:
    """变更历史，按批分组、按时间倒序。

    分页按**批**而不是按行：一批有几行是不定的，按行截断会把一批劈成
    两页，用户看到一条"改了 2 处"而实际改了 5 处的记录。
    """
    items, next_cursor = await service.list_revisions(
        db,
        org_id=user.org_id,
        project_id=project_id,
        limit=limit,
        cursor=_decode_cursor(cursor),
        role=role,
    )
    return RevisionPage(
        items=[
            RevisionBatchOut(
                batch_id=b.batch_id,
                role=b.role,
                source=b.source,
                reason=b.reason,
                actor_user_id=b.actor_user_id,
                created_at=b.created_at,
                changes=[RevisionOut.model_validate(r) for r in b.rows],
                undone_by_batch_id=b.undone_by_batch_id,
                undoes_batch_id=b.undoes_batch_id,
            )
            for b in items
        ],
        next_cursor=next_cursor.isoformat() if next_cursor else None,
    )


@router.post("/{project_id}/revisions/{batch_id}/undo", response_model=PatchResultOut)
async def undo_revision_batch(
    project_id: uuid.UUID,
    batch_id: uuid.UUID,
    payload: UndoIn,
    user: CurrentUser,
    db: DbSession,
) -> PatchResultOut:
    """撤销一批改动。

    实现是"反向重放一批 patch"，不是 DELETE：撤销本身也进变更历史，
    因此可审计、也可以再被撤销（相当于重做），且不需要第二套写代码。

    只要有一个字段在这批之后又被改过就整批 409——部分撤销出来的是一个
    既不是改前也不是改后的第三种状态，没有人要过它。
    """
    outcome = await service.undo_batch(
        db,
        org_id=user.org_id,
        project_id=project_id,
        batch_id=batch_id,
        reason=payload.reason,
        actor_user_id=user.id,
    )
    return _result(outcome)
