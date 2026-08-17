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

Stage = Literal[
    "routing",
    "plot_index",
    "screenplay",
    "await_setup",
    "characters",
    "scenes",
    "storyboard",
    "await_storyboard",
    "done",
]

# 阶段推进图。写成数据而不是散落的 if/else——
# "下一步是什么"必须只有一处答案。
#
# 顺序照搬三份提示词的工作流，每一步只干一件事：
#
#   情节目录 → 剧本改编 → 【剧本确认】
#   → 角色档案 → 场景档案 → 分镜表 → 【分镜确认】
#
# 曾经这里只有 story 和 visual 两步，一个 Agent 一次性吐出角色+场景+分镜。
# 结果是分镜只有"景别 + 一句话画面"，没有节点清单、没有运镜、没有对白、
# 没有出场人物——因为一次调用里塞不下三份提示词的要求。
_NEXT: dict[Stage, Stage] = {
    "routing": "plot_index",
    "plot_index": "screenplay",
    "screenplay": "await_setup",
    "await_setup": "characters",
    "characters": "scenes",
    "scenes": "storyboard",
    "storyboard": "await_storyboard",
    "await_storyboard": "done",
}

# 每个生产阶段跑哪个 Agent。**钉死到具体 id**，不按 role 取默认——
# 同一个 role 下现在有多个 Agent，`default_for` 只能取一个。
_SPEC_OF: dict[Stage, str] = {
    "routing": "router.default.v1",
    "plot_index": "story.plot_index.v1",
    "screenplay": "story.screenplay.v1",
    "characters": "visual.character.v1",
    "scenes": "visual.scene.v1",
    "storyboard": "visual.storyboard.v1",
}

# 哪些阶段是阻塞门（01_ProductSpec.md §5，默认 3 道）
_GATE_OF: dict[Stage, str] = {
    "await_setup": "setup",
    "await_storyboard": "storyboard",
}

# 门被打回时退回哪个阶段重做
_REDO_FROM: dict[Stage, Stage] = {
    "await_setup": "screenplay",
    "await_storyboard": "storyboard",
}

# 产出存进 current_state_json 的哪个键。
# 除了 routing 沿用历史键名 "router"，其余与阶段同名。
_STATE_KEY: dict[Stage, str] = {"routing": "router"}

# 旧阶段名 → 新阶段。
#
# 2026-08-18 阶段图由 2 步生产（story / visual）改为 5 步。库里已经存在的
# 项目仍带着旧名字，不翻译就会在 `_NEXT[stage]` 上 KeyError 变 500，
# 表现为"点继续彻底没反应"。**改阶段枚举必须同时处理存量数据**——
# 状态存在库里（ADR-008）意味着枚举变更就是一次数据迁移。
_LEGACY_STAGES: dict[str, Stage] = {
    "story": "plot_index",
    "visual": "characters",
}

# 每个阶段依赖哪个上游产出（键名与产出它的阶段同名）。
_REQUIRES: dict[Stage, str] = {
    "screenplay": "plot_index",
    "characters": "screenplay",
    "scenes": "screenplay",
    "storyboard": "scenes",
}


def _key(stage: Stage) -> str:
    return _STATE_KEY.get(stage, stage)


# 供聊天修订复用：改某个阶段的产出，要用产出它的那个 Agent，
# 且要拿到同一套模板变量。两边各写一份必然漂移。
PRODUCING_STAGES: tuple[str, ...] = (
    "plot_index",
    "screenplay",
    "characters",
    "scenes",
    "storyboard",
)


def spec_id_for(stage: str) -> str:
    return _SPEC_OF[stage]  # type: ignore[index]


def variables_for(stage: str, state: dict[str, Any]) -> dict[str, Any]:
    return _variables_for(stage, state)


# 原始素材的存储上限，与 AdvanceIn.user_input 的上限一致
MAX_SOURCE_CHARS = 20_000

# 传给 Visual 的素材节选长度。Story 拿全文，Visual 只拿开头——
# 角色外貌与场景细节多在开头，而全文要再花一遍输入 token。
SOURCE_EXCERPT_CHARS = 6_000


@dataclass(frozen=True, slots=True)
class Advance:
    stage: Stage
    ran_role: str | None
    gate_opened: str | None
    blocked: bool
    output: dict[str, Any] | None


def current_stage(state: dict[str, Any]) -> Stage:
    raw = str(state.get("stage", "routing"))
    if legacy := _LEGACY_STAGES.get(raw):
        return legacy
    return raw  # type: ignore[return-value]


def rewind_to_runnable(stage: Stage, state: dict[str, Any]) -> Stage:
    """缺上游产出就往回退，直到能真正跑起来的那一步。

    老项目的 state 里只有 `story` / `visual` 的旧格式产出，新阶段读不到
    自己要的东西。不退而硬往下跑，就是拿着空数据去请求模型——
    烧钱，且产出必然是错的，还看不出为什么错。
    """
    seen: set[str] = set()
    while (need := _REQUIRES.get(stage)) and need not in state:
        if stage in seen:  # 防环，配置写错时不要变成死循环
            break
        seen.add(stage)
        stage = need  # type: ignore[assignment]
    return stage


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

    # 原始素材立刻落库，且在跑任何 Agent 之前。
    # 它是后续每个阶段的依据；只传给 Router 然后丢掉，就是
    # "生成的内容和上传的小说没关系"的成因。先存再跑还有一个好处：
    # Router 调用失败时用户不用把整篇小说重新贴一遍。
    if user_input.strip() and state.get("source") != user_input[:MAX_SOURCE_CHARS]:
        state["source"] = user_input[:MAX_SOURCE_CHARS]
        await _save(db, org_id=org_id, project_id=project_id, state=state)

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

    # 到这里 stage 一定是生产阶段。老项目可能缺上游产出，先退到能跑的那一步。
    stage = rewind_to_runnable(stage, state)

    spec_id = _SPEC_OF[stage]
    spec = registry.get(spec_id)
    if spec is None:
        from apps.api.core.errors import AppError

        raise AppError("common.internal", message=f"阶段 {stage} 引用的 Agent {spec_id} 不存在")
    role = stage

    result = await runner.run_agent(
        db,
        org_id=org_id,
        project_id=project_id,
        spec=spec,
        # 一律从 state 拼输入，不直接用 user_input。
        # 直接用会让"素材"和"这一步要干什么"混成一句话，且后续阶段
        # （user_input 为空）走的是另一条代码路径——两条路径必然漂移。
        user_input=_input_for(role, state),
        variables=_variables_for(role, state),
    )
    output = result.output.model_dump(mode="json")

    # Router 要求澄清时原地停住，不要带着错误的路线往下跑——
    # 猜错路线会让用户白跑一整条生产链
    if stage == "routing" and output.get("requires_clarification"):
        state["router"] = output
        await _save(db, org_id=org_id, project_id=project_id, state=state)
        return Advance(
            stage="routing",
            ran_role=role,
            gate_opened=None,
            blocked=True,
            output=output,
        )

    state[_key(stage)] = output
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
        state["stage"] = _REDO_FROM[stage]
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


def _plot_index_block(state: dict[str, Any]) -> str:
    """情节目录 + 角色称呼映射表，供剧本阶段逐节点覆盖。"""
    plot = state.get("plot_index", {})
    lines = [f"题材：{plot.get('genre', '')}", "", "情节目录："]
    lines += [f"{n.get('index')}. {n.get('summary')}" for n in plot.get("nodes", [])]

    aliases = [c for c in plot.get("characters", []) if c.get("aliases")]
    if aliases:
        lines += ["", "角色称呼映射表（台词归属必须按这张表换算回真名）："]
        lines += [f"{c['name']} = {' / '.join(c['aliases'])}" for c in aliases]
    return "\n".join(lines)


def _screenplay_block(state: dict[str, Any]) -> str:
    """剧本正文，展开成纯文本。

    下游拿到的必须是**完整剧本**而不是摘要：分镜要按场次逐条拆镜号，
    只给一句 logline 的话镜头内容只能靠编。
    """
    script = state.get("screenplay", {})
    lines = [f"《{script.get('title', '')}》", str(script.get("synopsis", "")), ""]

    for ep in script.get("episodes", []):
        lines.append(f"第 {ep.get('index')} 集 {ep.get('title', '')}")
        for sc in ep.get("scenes", []):
            lines.append(f"  {sc.get('id')} 【{sc.get('location')} - {sc.get('time_mood')}】")
            for b in sc.get("beats", []):
                kind = b.get("kind")
                who = b.get("character_ref", "")
                emotion = f"（{b['emotion']}）" if b.get("emotion") else ""
                if kind == "action":
                    lines.append(f"    △{b.get('text')}")
                elif kind == "sfx":
                    lines.append(f"    【音效：{b.get('text')}】")
                elif kind == "vo":
                    lines.append(f"    {who}(VO)：{b.get('text')}")
                else:
                    lines.append(f"    {who}{emotion}：{b.get('text')}")
            if sc.get("hook"):
                lines.append(f"    【钩子】{sc['hook']}")
    return "\n".join(lines)


def _input_for(stage: str, state: dict[str, Any]) -> str:
    """给每个阶段拼输入。

    **每个阶段都要能看到它该看的上游产出。** 曾经这里只给 Story 发
    "路线：X\\n请产出故事结构。"——用户上传的小说在 Router 之后就丢了，
    Story 只能凭空编一个故事出来。表现就是"生成的内容和我上传的小说
    完全不搭架"，而且它编得像模像样，不看输入根本看不出哪里错了。
    """
    source = str(state.get("source", "")).strip()
    route = state.get("router", {}).get("route", "")

    if stage == "routing":
        return source

    if stage == "plot_index":
        return f"路线：{route}\n\n【原著全文】\n{source}"

    if stage == "screenplay":
        return (
            f"路线：{route}\n\n"
            f"{_plot_index_block(state)}\n\n"
            f"【原著全文】\n{source}\n\n"
            "请逐节点改编为短剧剧本。人物姓名、地点、台词必须来自原著，"
            "**不要另编一个新故事**，也不要脑补原著里没有的后续剧情。"
        )

    if stage == "characters":
        return (
            f"{_screenplay_block(state)}\n\n"
            f"【原著节选】\n{source[:SOURCE_EXCERPT_CHARS]}\n\n"
            "请为剧本中出现的每个角色建立视觉档案。"
            "ref 用剧本里已有的 character_ref，不要另起名字。"
        )

    if stage == "scenes":
        scene_names = sorted(
            {
                sc.get("location", "")
                for ep in state.get("screenplay", {}).get("episodes", [])
                for sc in ep.get("scenes", [])
                if sc.get("location")
            }
        )
        return (
            f"{_screenplay_block(state)}\n\n"
            f"剧本涉及的地点：{'、'.join(scene_names)}\n\n"
            f"【原著节选】\n{source[:SOURCE_EXCERPT_CHARS]}\n\n"
            "请为每个地点建立场景档案。"
        )

    if stage == "storyboard":
        chars = state.get("characters", {}).get("characters", [])
        scenes = state.get("scenes", {}).get("scenes", [])
        return "\n".join(
            [
                _screenplay_block(state),
                "",
                "可用角色 ref：" + "、".join(f"{c.get('ref')}={c.get('name')}" for c in chars),
                "可用场景 ref：" + "、".join(f"{s.get('ref')}={s.get('name')}" for s in scenes),
                "",
                "请先列分镜节点清单，再逐节点拆镜号。",
                "character_refs 和 scene_ref 只能用上面列出的 ref，不要新造。",
            ]
        )

    return ""


# 题材 → 时代背景 → 默认人种。
# 中文剧本的角色默认东亚面孔；不给这个默认值，出图模型会按训练分布
# 画成欧美面孔，而"人种错了"是用户第一眼就会发现的问题。
_ERA_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("古装", "仙侠", "武侠", "宫斗", "架空"), "古代东方", "中国古人，东亚面孔"),
    (("西方奇幻", "魔法", "骑士"), "西方奇幻", "欧洲面孔"),
    (("科幻", "未来", "赛博"), "未来世界", "按角色名判断"),
)
_ERA_DEFAULT = ("现代中国", "中国现代人，东亚面孔")


def _era_of(state: dict[str, Any]) -> tuple[str, str]:
    genre = str(state.get("plot_index", {}).get("genre", ""))
    for keywords, era, ethnicity in _ERA_RULES:
        if any(k in genre for k in keywords):
            return era, ethnicity
    return _ERA_DEFAULT


def _variables_for(stage: str, state: dict[str, Any]) -> dict[str, Any]:
    router = state.get("router", {})
    era, ethnicity = _era_of(state)
    plot = state.get("plot_index", {})
    return {
        "target_duration_seconds": router.get("estimated_duration_seconds", 60),
        "target_shots": router.get("estimated_shots", 12),
        "era": era,
        "default_ethnicity": ethnicity,
        "plot_index": _plot_index_block(state) if plot else "（无）",
    }


def _gate_summary(state: dict[str, Any], gate: str) -> dict[str, Any]:
    """给前端展示用的门摘要。只放数字和标题，不放大段内容。"""
    if gate == "setup":
        script = state.get("screenplay", {})
        plot = state.get("plot_index", {})
        scenes = sum(len(ep.get("scenes", [])) for ep in script.get("episodes", []))
        covered = {
            c.get("node_index")
            for c in script.get("node_coverage", [])
            if c.get("scene_id") or c.get("merged_into")
        }
        nodes = len(plot.get("nodes", []))
        return {
            "title": script.get("title"),
            "synopsis": script.get("synopsis"),
            "episodes": len(script.get("episodes", [])),
            "scenes": scenes,
            # 情节覆盖率是"改编有没有漏掉原著情节"的可核对指标。
            # 模型自己写"✅ 已覆盖"没有约束力，这里数的是结构化引用。
            "nodes_total": nodes,
            "nodes_covered": len(covered),
        }

    storyboard = state.get("storyboard", {})
    return {
        "characters": len(state.get("characters", {}).get("characters", [])),
        "scenes": len(state.get("scenes", {}).get("scenes", [])),
        "nodes": len(storyboard.get("nodes", [])),
        "shots": len(storyboard.get("shots", [])),
    }
