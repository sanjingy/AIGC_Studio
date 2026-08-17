"""Agent 模块的唯一对外入口。"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from agents import registry
from apps.api.core.errors import AppError
from apps.api.modules.agent import orchestrator
from apps.api.modules.agent import repository as repo
from apps.api.modules.agent import revise as revise_mod
from apps.api.modules.agent.models import AgentRun, AgentStep, Approval, ConversationMessage

MAX_RUNS = 100
MAX_MESSAGES = 200


async def advance(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, user_input: str = ""
) -> orchestrator.Advance:
    return await orchestrator.advance(
        db, org_id=org_id, project_id=project_id, user_input=user_input
    )


async def run_to_gate(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    user_input: str = "",
    max_iterations: int = 8,
) -> orchestrator.Advance:
    """一直推进到遇上审核门或结束。

    max_iterations 是防打转的硬上限：编排图出 bug 时不能变成死循环，
    那会持续烧 Credits 直到熔断。
    """
    result = await orchestrator.advance(
        db, org_id=org_id, project_id=project_id, user_input=user_input
    )
    for _ in range(max_iterations - 1):
        if result.blocked or result.stage == "done":
            break
        result = await orchestrator.advance(db, org_id=org_id, project_id=project_id)
    return result


async def resolve_gate(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    approval_id: uuid.UUID,
    decision: str,
    resolved_by: uuid.UUID,
    comment: str | None = None,
) -> orchestrator.Advance:
    return await orchestrator.resolve_gate(
        db,
        org_id=org_id,
        project_id=project_id,
        approval_id=approval_id,
        decision=decision,
        resolved_by=resolved_by,
        comment=comment,
    )


async def list_approvals(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[Approval]:
    return await repo.list_approvals(db, org_id=org_id, project_id=project_id)


async def list_runs(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, limit: int = 30
) -> list[AgentRun]:
    return await repo.list_runs(
        db, org_id=org_id, project_id=project_id, limit=min(limit, MAX_RUNS)
    )


async def get_run_steps(
    db: AsyncSession, *, org_id: uuid.UUID, run_id: uuid.UUID
) -> list[AgentStep]:
    run = await repo.get_run(db, org_id=org_id, run_id=run_id)
    if run is None:
        raise AppError("common.not_found", message=f"agent run {run_id}")
    return await repo.list_steps(db, run_id=run_id)


async def revise(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    target_role: str,
    instruction: str,
) -> revise_mod.ReviseResult:
    return await revise_mod.revise(
        db,
        org_id=org_id,
        project_id=project_id,
        target_role=target_role,
        instruction=instruction,
    )


async def list_messages(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, limit: int = 100
) -> list[ConversationMessage]:
    return await repo.list_messages(
        db, org_id=org_id, project_id=project_id, limit=min(limit, MAX_MESSAGES)
    )


def registry_snapshot() -> registry.LoadReport:
    return registry.registry()


def reload_registry() -> registry.LoadReport:
    """热加载自定义 Agent，不用重启进程。"""
    return registry.registry(reload=True)
