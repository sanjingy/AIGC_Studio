"""Agent 模块的唯一对外入口。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agents import registry
from agents import schemas as agent_schemas
from agents.spec import AgentSpec
from apps.api.core.errors import AppError
from apps.api.modules.agent import orchestrator, runner
from apps.api.modules.agent import repository as repo
from apps.api.modules.agent import revise as revise_mod
from apps.api.modules.agent.models import AgentRun, AgentStep, Approval, ConversationMessage

MAX_RUNS = 100
MAX_MESSAGES = 200
MAX_OUTPUTS = 400


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


async def list_outputs_by_agent(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_ids: Sequence[uuid.UUID],
    agent_ids: Sequence[str],
    limit: int = MAX_OUTPUTS,
) -> list[AgentRun]:
    """跨项目取某几个 Agent 的成功产出。

    资产库要展示角色档案 / 场景档案，那些是结构化文本产出，
    存在 agent_runs.output_json 里，不在 assets 表里。调用方给项目集合，
    这里只负责查——"哪些项目属于这个用户"是项目模块的判断，不在这儿做。
    """
    return await repo.list_outputs_by_agent(
        db,
        org_id=org_id,
        project_ids=project_ids,
        agent_ids=agent_ids,
        limit=min(limit, MAX_OUTPUTS),
    )


async def get_run(db: AsyncSession, *, org_id: uuid.UUID, run_id: uuid.UUID) -> AgentRun:
    """按 id 取一次运行记录。跨租户同样 404，不 403。"""
    run = await repo.get_run(db, org_id=org_id, run_id=run_id)
    if run is None:
        raise AppError("common.not_found", message=f"agent run {run_id}")
    return run


async def list_runs_by_ids(
    db: AsyncSession, *, org_id: uuid.UUID, run_ids: Sequence[uuid.UUID]
) -> list[AgentRun]:
    """按 id 批量取运行记录。

    资产库的文件夹视图要的是"用户当初归了类的那一版"，而
    `list_outputs_by_agent` 给的是"每个项目最新的那一版"——用后者会让
    归好类的旧版本在文件夹里凭空消失。
    """
    return await repo.list_runs_by_ids(db, org_id=org_id, run_ids=run_ids[:MAX_OUTPUTS])


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


def get_spec(agent_id: str) -> AgentSpec:
    """按 id 取 Agent 定义。取不到就是配置错误，不是用户错误。"""
    spec = registry.get(agent_id)
    if spec is None:
        raise AppError("common.internal", message=f"Agent {agent_id} 不存在")
    return spec


def default_variables(role: str) -> dict[str, Any]:
    """没有项目状态时的提示词变量。

    时代背景/默认人种这些变量在编排链路里是从项目 state 推出来的。
    不挂项目的调用没有 state，就走同一套推导的缺省分支——**不另写一份**，
    否则同一个 Agent 在两条路径上会拿到两套不同的变量，产出跟着分叉。
    """
    return orchestrator.variables_for(role, {})


async def generate_structured(
    *,
    agent_id: str,
    user_input: str,
    variables: dict[str, Any] | None = None,
    system_suffix: str = "",
) -> runner.Completion:
    """跑一次**不挂项目**的 Agent 调用，拿结构化产出。

    与 `advance` / `revise` 的区别只有一个：没有项目，因此不写
    `agent_runs`（那张表的 project_id 是 NOT NULL）。提示词合成、输出契约、
    schema 校验与重试完全共用 `runner.complete_structured`——
    调用方负责把过程落到自己的表里，以及负责计费。
    """
    spec = get_spec(agent_id)
    return await runner.complete_structured(
        spec=spec,
        user_input=user_input,
        variables=variables if variables is not None else default_variables(spec.role),
        system_suffix=system_suffix,
    )


# ---------------------------------------------------------------- 供字段级编辑复用
#
# 下面三个函数是 content 模块（`PATCH /projects/{id}/outputs/{role}`）唯一
# 需要的编排知识。**它们必须留在这里而不是被 content 抄一份**：
# "哪些阶段的产出可以改"、"它按哪个 schema"、"改了它谁会过期"
# 这三件事的真相在编排器里，抄出去必然漂移，而漂移的表现是
# 用户改完上游、下游没被标过期，界面显示一切正常但内容对不上。


def editable_roles() -> tuple[str, ...]:
    """可被字段级编辑的阶段产出。与聊天修订的范围一致。

    routing 不在内：那是调度决策不是内容，改路线该重跑而不是改字段。
    """
    return tuple(revise_mod.REVISABLE_ROLES)


def output_schema_for(role: str) -> type[BaseModel]:
    """这个阶段的产出该按哪个 Pydantic schema 校验。

    从 Agent spec 的 `output_schema` 反查，不在 content 里另建一张
    role → schema 的表：换 Agent、改 schema 名只该改一处。
    """
    if role not in editable_roles():
        raise AppError(
            "common.validation_failed",
            message=f"{role} 的产出不支持字段级编辑",
            detail={"editable": list(editable_roles())},
        )
    spec = get_spec(orchestrator.spec_id_for(role))
    if not spec.output_schema:
        raise AppError("common.internal", message=f"Agent {spec.id} 没有声明 output_schema")
    try:
        return agent_schemas.resolve(spec.output_schema)
    except LookupError as exc:
        raise AppError("common.internal", message=str(exc)) from exc


def current_stage(state: dict[str, Any]) -> str:
    """这份编排状态停在哪个阶段。

    只是把 `orchestrator.current_stage` 透出到 service 层，给别的模块用
    （`project.router` 的 `GET /projects/{id}/state`）。**不在外面另抄一张
    阶段表**——"下一步是什么"必须只有一处答案，而那一处是 `_NEXT`。

    它同时负责翻译旧阶段名（`story` / `visual`），所以外部拿到的一定是
    现行枚举里的值。
    """
    return orchestrator.current_stage(state)


def stale_roles_of(state: dict[str, Any]) -> list[str]:
    """过期记账。同样只是透出，顺序按生产顺序，且只保留真的还有产出的阶段。"""
    return orchestrator.stale_roles(state)


def mark_role_edited(state: dict[str, Any], role: str) -> list[str]:
    """产出被直接改过之后的过期记账，就地改 `state`，返回新增过期的下游。

    与 `revise` 走的是同一对函数（`mark_stale` / `mark_fresh`），
    因为"改了上游、下游停在旧版"这件事与改动是模型做的还是人做的无关。
    """
    stale = [r for r in revise_mod.downstream_of(role) if r in state]
    orchestrator.mark_stale(state, stale)
    orchestrator.mark_fresh(state, role)
    return stale
