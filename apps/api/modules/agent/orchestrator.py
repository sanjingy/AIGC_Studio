"""编排：决定下一步该做什么。

**每次都从 `projects.current_state_json` 重新计算，不持有内存状态**（ADR-008）。

这样进程重启、Worker 崩溃、用户隔天回来续做，行为完全一致。
用 LangGraph 的内存图状态会在这三种情况下各错一遍。

Director 是"读状态 → 决定下一步"的纯函数，不生产内容。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from agents import registry
from apps.api.core.logging import get_logger
from apps.api.modules.agent import repository as repo
from apps.api.modules.agent import runner
from apps.api.modules.project import service as project_service

log = get_logger(__name__)

Stage = Literal["routing", "story", "visual", "await_setup", "await_storyboard", "done"]

# 阶段推进图。写成数据而不是散落的 if/else——
# "下一步是什么"必须只有一处答案。
_NEXT: dict[Stage, Stage] = {
    "routing": "story",
    "story": "await_setup",
    "await_setup": "visual",
    "visual": "await_storyboard",
    "await_storyboard": "done",
}

# 哪些阶段是阻塞门（01_ProductSpec.md §5，默认 3 道）
_GATE_OF: dict[Stage, str] = {
    "await_setup": "setup",
    "await_storyboard": "storyboard",
}


@dataclass(frozen=True, slots=True)
class Advance:
    stage: Stage
    ran_role: str | None
    gate_opened: str | None
    blocked: bool
    output: dict[str, Any] | None


def current_stage(state: dict[str, Any]) -> Stage:
    return str(state.get("stage", "routing"))  # type: ignore[return-value]


async def advance(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    user_input: str = "",
) -> Advance:
    """把项目推进一步。

    遇到审核门就停下，返回 blocked=True。
    调用方（API 或 Worker）负责决定要不要继续调。
    """
    project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
    state: dict[str, Any] = dict(project.current_state_json or {})
    stage = current_stage(state)

    if stage == "done":
        return Advance(stage="done", ran_role=None, gate_opened=None, blocked=False, output=None)

    # 到了门口：开门并停下等人
    if gate := _GATE_OF.get(stage):
        pending = await repo.get_pending(db, org_id=org_id, project_id=project_id, gate=gate)
        if pending is None:
            await repo.create_approval(
                db,
                org_id=org_id,
                project_id=project_id,
                gate=gate,
                payload={"stage": stage, "summary": _gate_summary(state, gate)},
            )
            await db.commit()
            log.info("agent.gate_opened", project_id=str(project_id), gate=gate)
        return Advance(stage=stage, ran_role=None, gate_opened=gate, blocked=True, output=None)

    role = {"routing": "router", "story": "story", "visual": "visual"}[stage]
    spec = registry.default_for(role)

    result = await runner.run_agent(
        db,
        org_id=org_id,
        project_id=project_id,
        spec=spec,
        user_input=user_input or _input_for(role, state),
        variables=_variables_for(role, state),
    )
    output = result.output.model_dump(mode="json")

    # Router 要求澄清时原地停住，不要带着错误的路线往下跑——
    # 猜错路线会让用户白跑一整条生产链
    if role == "router" and output.get("requires_clarification"):
        state["router"] = output
        await _save(db, org_id=org_id, project_id=project_id, state=state)
        return Advance(
            stage="routing",
            ran_role=role,
            gate_opened=None,
            blocked=True,
            output=output,
        )

    state[role] = output
    state["stage"] = _NEXT[stage]
    await _save(db, org_id=org_id, project_id=project_id, state=state)

    return Advance(
        stage=state["stage"],
        ran_role=role,
        gate_opened=None,
        blocked=False,
        output=output,
    )


async def resolve_gate(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    approval_id: uuid.UUID,
    decision: str,
    resolved_by: uuid.UUID,
    comment: str | None = None,
) -> Advance:
    """处理审核结果。

    approved          → 进入下一阶段
    changes_requested → 退回上一个生产阶段重做
    rejected          → 项目停在这里
    """
    approval = await repo.get_approval(db, org_id=org_id, approval_id=approval_id)
    if approval is None or approval.project_id != project_id:
        from apps.api.core.errors import AppError

        raise AppError("common.not_found", message=f"approval {approval_id}")
    if approval.status != "pending":
        from apps.api.core.errors import AppError

        raise AppError("common.conflict", message=f"审核已处理过：{approval.status}")

    await repo.resolve_approval(
        db, approval, status=decision, resolved_by=resolved_by, comment=comment
    )

    project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
    state: dict[str, Any] = dict(project.current_state_json or {})
    stage = current_stage(state)

    if decision == "approved":
        state["stage"] = _NEXT[stage]
    elif decision == "changes_requested":
        # 退回产出这批内容的那个阶段重做
        state["stage"] = {"await_setup": "story", "await_storyboard": "visual"}[stage]
    else:
        state["stage"] = stage  # rejected：停住不动

    await _save(db, org_id=org_id, project_id=project_id, state=state)
    return Advance(
        stage=state["stage"],
        ran_role=None,
        gate_opened=None,
        blocked=decision == "rejected",
        output=None,
    )


async def _save(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, state: dict[str, Any]
) -> None:
    project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
    # 整体重新赋值，不原地改字典——JSONB 字段原地修改 SQLAlchemy 检测不到，
    # 会静默不写库，表现为"点了确认但状态没变"。
    project.current_state_json = dict(state)
    await db.commit()


def _input_for(role: str, state: dict[str, Any]) -> str:
    if role == "story":
        return f"路线：{state.get('router', {}).get('route')}\n请产出故事结构。"
    if role == "visual":
        story = state.get("story", {})
        return f"故事：{story.get('title')}\n{story.get('logline')}\n请产出角色、场景与分镜。"
    return ""


def _variables_for(role: str, state: dict[str, Any]) -> dict[str, Any]:
    router = state.get("router", {})
    return {
        "target_duration_seconds": router.get("estimated_duration_seconds", 60),
        "target_shots": router.get("estimated_shots", 12),
    }


def _gate_summary(state: dict[str, Any], gate: str) -> dict[str, Any]:
    """给前端展示用的门摘要。只放数字和标题，不放大段内容。"""
    if gate == "setup":
        story = state.get("story", {})
        return {
            "title": story.get("title"),
            "logline": story.get("logline"),
            "acts": len(story.get("acts", [])),
        }
    visual = state.get("visual", {})
    return {
        "characters": len(visual.get("characters", [])),
        "scenes": len(visual.get("scenes", [])),
        "shots": len(visual.get("shots", [])),
    }
