"""Agent 数据访问。模块私有。用户侧查询强制带 org_id。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.agent.models import AgentRun, AgentStep, Approval, ConversationMessage


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


async def list_outputs_by_agent(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_ids: Sequence[uuid.UUID],
    agent_ids: Sequence[str],
    limit: int,
) -> list[AgentRun]:
    """指定项目里、指定 Agent 产出的成功运行，新的在前。

    资产库的"角色/场景档案"用它。这里不做跨项目的去重合并——
    同名角色在两个项目里是两份独立设定，合并需要一致性引擎参与，
    不是一个列表查询该做的判断。
    """
    if not project_ids or not agent_ids:
        return []
    stmt = (
        select(AgentRun)
        .where(
            AgentRun.org_id == org_id,
            AgentRun.project_id.in_(project_ids),
            AgentRun.agent_id.in_(agent_ids),
            AgentRun.status == "succeeded",
            AgentRun.output_json.is_not(None),
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


# ------------------------------------------------------------------ 对话修订


async def add_message(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    target_role: str,
    author: str,
    text: str,
    revision: int,
    run_id: uuid.UUID | None = None,
    changed_fields: list[str] | None = None,
) -> ConversationMessage:
    row = ConversationMessage(
        org_id=org_id,
        project_id=project_id,
        target_role=target_role,
        author=author,
        text=text,
        revision=revision,
        run_id=run_id,
        changed_fields=changed_fields or [],
    )
    db.add(row)
    await db.flush()
    return row


async def next_revision(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, role: str
) -> int:
    stmt = (
        select(ConversationMessage.revision)
        .where(
            ConversationMessage.org_id == org_id,
            ConversationMessage.project_id == project_id,
            ConversationMessage.target_role == role,
        )
        .order_by(ConversationMessage.revision.desc())
        .limit(1)
    )
    latest = (await db.execute(stmt)).scalar_one_or_none()
    return (latest or 0) + 1


async def list_messages(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, limit: int
) -> list[ConversationMessage]:
    stmt = (
        select(ConversationMessage)
        .where(
            ConversationMessage.org_id == org_id,
            ConversationMessage.project_id == project_id,
            ConversationMessage.deleted_at.is_(None),
        )
        .order_by(ConversationMessage.created_at)
        .limit(limit)
    )
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


async def list_runs_by_ids(
    db: AsyncSession, *, org_id: uuid.UUID, run_ids: Sequence[uuid.UUID]
) -> list[AgentRun]:
    """按 id 批量取运行记录。资产库的文件夹视图用：那里要拿的是
    "用户归了类的那一版"，不是"每个项目最新的那一版"。"""
    if not run_ids:
        return []
    stmt = select(AgentRun).where(
        AgentRun.org_id == org_id,
        AgentRun.deleted_at.is_(None),
        AgentRun.id.in_(list(run_ids)),
    )
    return list((await db.execute(stmt)).scalars())


# ------------------------------------------------------------ 成品提示词运行
#
# 成品提示词（ADR-036）也是一次 Agent 运行，所以它存在 `agent_runs` /
# `agent_steps` 里，**不另建一张表**：那会变成第二份执行状态，而这个仓库
# 已经有一条硬规则说执行状态只认 `tasks.status`（ADR-008）。检索维度钉在
# `input_json` 的两个键上：
#
#     prompt_kind   character / scene / shot_image / shot_video
#     subject_key   角色或场景的 ref，镜头则是镜号的十进制字符串


async def list_prompt_runs(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    kind: str,
    subject_key: str,
    limit: int,
    succeeded_only: bool = True,
) -> list[AgentRun]:
    """某个项目里某个对象的提示词运行，新的在前。"""
    conditions = [
        AgentRun.org_id == org_id,
        AgentRun.project_id == project_id,
        AgentRun.deleted_at.is_(None),
        AgentRun.input_json["prompt_kind"].astext == kind,
        AgentRun.input_json["subject_key"].astext == subject_key,
    ]
    if succeeded_only:
        conditions.append(AgentRun.status == "succeeded")
    stmt = (
        select(AgentRun)
        .where(*conditions)
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())
