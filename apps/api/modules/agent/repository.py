"""Agent 数据访问。模块私有。用户侧查询强制带 org_id。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.agent.models import AgentRun, AgentStep, Approval


async def create_run(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    agent_id: str,
    role: str,
    input_json: dict[str, Any],
) -> AgentRun:
    row = AgentRun(
        org_id=org_id,
        project_id=project_id,
        agent_id=agent_id,
        role=role,
        input_json=input_json,
    )
    db.add(row)
    await db.flush()
    return row


async def finish_run(
    db: AsyncSession,
    run: AgentRun,
    *,
    status: str,
    output: dict[str, Any] | None = None,
    model_id: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    attempts: int = 1,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> None:
    run.status = status
    run.output_json = output
    run.model_id = model_id
    run.tokens_in = tokens_in
    run.tokens_out = tokens_out
    run.attempts = attempts
    run.error_code = error_code
    run.error_detail = error_detail
    run.finished_at = datetime.now(UTC)


async def add_step(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    step_index: int,
    kind: str,
    resolved_prompt: str | None = None,
    raw_output: str | None = None,
    error: str | None = None,
    duration_ms: int = 0,
) -> None:
    db.add(
        AgentStep(
            org_id=org_id,
            run_id=run_id,
            step_index=step_index,
            kind=kind,
            resolved_prompt=resolved_prompt,
            raw_output=raw_output,
            error=error,
            duration_ms=duration_ms,
        )
    )
    await db.flush()


async def list_runs(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, limit: int
) -> list[AgentRun]:
    stmt = (
        select(AgentRun)
        .where(
            AgentRun.org_id == org_id,
            AgentRun.project_id == project_id,
            AgentRun.deleted_at.is_(None),
        )
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())


async def get_run(db: AsyncSession, *, org_id: uuid.UUID, run_id: uuid.UUID) -> AgentRun | None:
    stmt = select(AgentRun).where(
        AgentRun.id == run_id, AgentRun.org_id == org_id, AgentRun.deleted_at.is_(None)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_steps(db: AsyncSession, *, run_id: uuid.UUID) -> list[AgentStep]:
    stmt = select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.step_index)
    return list((await db.execute(stmt)).scalars())


# ------------------------------------------------------------------ approvals


async def create_approval(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    gate: str,
    payload: dict[str, Any],
) -> Approval:
    row = Approval(org_id=org_id, project_id=project_id, gate=gate, payload_json=payload)
    db.add(row)
    await db.flush()
    return row


async def get_pending(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, gate: str
) -> Approval | None:
    stmt = select(Approval).where(
        Approval.org_id == org_id,
        Approval.project_id == project_id,
        Approval.gate == gate,
        Approval.status == "pending",
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_approval(
    db: AsyncSession, *, org_id: uuid.UUID, approval_id: uuid.UUID
) -> Approval | None:
    stmt = select(Approval).where(Approval.id == approval_id, Approval.org_id == org_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_approvals(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[Approval]:
    stmt = (
        select(Approval)
        .where(Approval.org_id == org_id, Approval.project_id == project_id)
        .order_by(Approval.created_at)
    )
    return list((await db.execute(stmt)).scalars())


async def resolve_approval(
    db: AsyncSession,
    approval: Approval,
    *,
    status: str,
    resolved_by: uuid.UUID,
    comment: str | None,
) -> None:
    approval.status = status
    approval.resolved_by = resolved_by
    approval.comment = comment
    approval.resolved_at = datetime.now(UTC)
