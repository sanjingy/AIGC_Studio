from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from apps.api.modules.agent import service
from apps.api.modules.agent.schemas import (
    AdvanceIn,
    AdvanceOut,
    AgentRunOut,
    AgentSpecOut,
    ApprovalDecisionIn,
    ApprovalOut,
    RegistryOut,
)
from apps.api.modules.auth.deps import CurrentUser, DbSession

router = APIRouter(tags=["agents"])


@router.get("/agents", response_model=RegistryOut)
async def list_agents(user: CurrentUser) -> RegistryOut:
    """列出已加载的 Agent。

    同时返回加载失败的 spec 及原因——第三方 Agent 写错了要能看见，
    静默跳过会让人以为"我传的 Agent 没生效但不知道为什么"。
    """
    del user
    report = service.registry_snapshot()
    return RegistryOut(
        agents=[
            AgentSpecOut(
                id=s.id,
                name=s.name,
                description=s.description,
                role=s.role,
                source=s.source,
                tools=s.tools,
                max_steps=s.max_steps,
                budget_credits=s.budget_credits,
            )
            for s in report.specs.values()
        ],
        errors=report.errors,
    )


@router.post("/agents/reload", response_model=RegistryOut)
async def reload_agents(user: CurrentUser) -> RegistryOut:
    del user
    report = service.reload_registry()
    return RegistryOut(
        agents=[
            AgentSpecOut(
                id=s.id,
                name=s.name,
                description=s.description,
                role=s.role,
                source=s.source,
                tools=s.tools,
                max_steps=s.max_steps,
                budget_credits=s.budget_credits,
            )
            for s in report.specs.values()
        ],
        errors=report.errors,
    )


@router.post("/projects/{project_id}/advance", response_model=AdvanceOut)
async def advance(
    project_id: uuid.UUID,
    payload: AdvanceIn,
    user: CurrentUser,
    db: DbSession,
    to_gate: bool = Query(True, description="是否一直跑到下一个审核门"),
) -> AdvanceOut:
    runner_fn = service.run_to_gate if to_gate else service.advance
    result = await runner_fn(
        db, org_id=user.org_id, project_id=project_id, user_input=payload.user_input
    )
    return AdvanceOut(
        stage=result.stage,
        ran_role=result.ran_role,
        gate_opened=result.gate_opened,
        blocked=result.blocked,
        output=result.output,
    )


@router.get("/projects/{project_id}/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    project_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[ApprovalOut]:
    rows = await service.list_approvals(db, org_id=user.org_id, project_id=project_id)
    return [ApprovalOut.model_validate(r) for r in rows]


@router.post("/projects/{project_id}/approvals/{approval_id}", response_model=AdvanceOut)
async def resolve_approval(
    project_id: uuid.UUID,
    approval_id: uuid.UUID,
    payload: ApprovalDecisionIn,
    user: CurrentUser,
    db: DbSession,
) -> AdvanceOut:
    result = await service.resolve_gate(
        db,
        org_id=user.org_id,
        project_id=project_id,
        approval_id=approval_id,
        decision=payload.decision,
        resolved_by=user.id,
        comment=payload.comment,
    )
    return AdvanceOut(
        stage=result.stage,
        ran_role=result.ran_role,
        gate_opened=result.gate_opened,
        blocked=result.blocked,
        output=result.output,
    )


@router.get("/projects/{project_id}/agent-runs", response_model=list[AgentRunOut])
async def list_runs(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(30, ge=1, le=100),
) -> list[AgentRunOut]:
    rows = await service.list_runs(db, org_id=user.org_id, project_id=project_id, limit=limit)
    return [AgentRunOut.model_validate(r) for r in rows]
