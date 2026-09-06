"""编排：决定下一步该做什么。

**每次都从 `projects.current_state_json` 重新计算，不持有内存状态**（ADR-008）。

这样进程重启、Worker 崩溃、用户隔天回来续做，行为完全一致。
用 LangGraph 的内存图状态会在这三种情况下各错一遍。

Director 是"读状态 → 决定下一步"的纯函数，不生产内容。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from agents import registry
from apps.api.core.logging import get_logger
from apps.api.modules.agent import anchors, runner
from apps.api.modules.agent import repository as repo
from apps.api.modules.project import service as project_service

log = get_logger(__name__)

Stage = Literal[
    "routing",
    "plot_index",
    "await_plan",
    "screenplay",
    "await_setup",
    "characters",
    "scenes",
    "await_anchors",
    "storyboard",
    "await_storyboard",
    "done",
]

# 阶段推进图。写成数据而不是散落的 if/else——
# "下一步是什么"必须只有一处答案。
#
# 顺序照搬三份提示词的工作流，每一步只干一件事：
#
#   情节目录 → 【开拍前确认】→ 剧本改编 → 【剧本确认】
#   → 角色档案 → 场景档案 → 【空间锚点确认】→ 分镜表 → 【分镜确认】
#
# 曾经这里只有 story 和 visual 两步，一个 Agent 一次性吐出角色+场景+分镜。
# 结果是分镜只有"景别 + 一句话画面"，没有节点清单、没有运镜、没有对白、
# 没有出场人物——因为一次调用里塞不下三份提示词的要求。
#
# 2026-09-05（ADR-037）门从两道变四道，新增 `await_plan` 与 `await_anchors`。
# 门① 一次问四件事（情节目录、时代背景与人种、画风、改编/洗稿）而不是拆成
# 四道门：这四件事**在信息上是同时可决的**，都只依赖原文与情节目录，
# 不依赖彼此，拆开只增加点击不增加判断质量。
_NEXT: dict[Stage, Stage] = {
    "routing": "plot_index",
    "plot_index": "await_plan",
    "await_plan": "screenplay",
    "screenplay": "await_setup",
    "await_setup": "characters",
    "characters": "scenes",
    "scenes": "await_anchors",
    "await_anchors": "storyboard",
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

# 哪些阶段是阻塞门（ADR-037，四道）
#
#   plan        开拍前确认：情节目录 / 时代背景与人种 / 画风 / 改编模式
#   setup       剧本门（既有，行为一个字没改）
#   anchors     空间锚点门：一次性确认全部要出卡的场景，禁止逐个确认
#   storyboard  分镜门（既有，行为一个字没改）
_GATE_OF: dict[Stage, str] = {
    "await_plan": "plan",
    "await_setup": "setup",
    "await_anchors": "anchors",
    "await_storyboard": "storyboard",
}

# 门被打回时退回哪个阶段重做（ADR-037 第 5 条）
_REDO_FROM: dict[Stage, Stage] = {
    "await_plan": "plot_index",
    "await_setup": "screenplay",
    "await_anchors": "scenes",
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
# 2026-09-05（ADR-037）门由 2 道变 4 道时**没有**往这张表里加东西，
# 这一点值得写下来：新增的是两个 `await_*` 阶段，插在既有阶段之间，
# 存量项目的 stage 值一个都没作废，`_NEXT[stage]` 全部仍然命中。
# 需要处理的存量问题不在阶段名上，而在数据上——已经越过新门位置的项目
# 从来没被问过画风与锚点，它们按缺省值补齐并标注"历史项目，未经确认"
# （`project_lock_variables.origin = 'migrated'`，ADR-037 第 6 条）。
# **不得把它们退回去重新确认**：那等于让一批跑到一半的项目全部回到起点。
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


# ---------------------------------------------------------------- 过期记账
#
# 「哪些已产出的阶段因为上游被改过而停留在旧版本」。
#
# 这是**记账，不是第二张阶段图**：它不参与 `_NEXT`，不影响任何阶段
# 何时产出、门何时打开。它只回答一个问题——这份产出还新鲜吗。
#
# 存进 `current_state_json` 而不是只在那一次 HTTP 响应里返回：
# 状态的唯一权威在库里（ADR-008），只放在响应里的东西刷新一次就没了，
# 用户改完上游、关掉页面、隔天回来，会以为下游已经同步过了。

STALE_ROLES_KEY = "stale_roles"


def stale_roles(state: dict[str, Any]) -> list[str]:
    """读记账。按生产顺序返回，且只保留真的还有产出的阶段。"""
    raw = state.get(STALE_ROLES_KEY)
    marked = {r for r in raw if isinstance(r, str)} if isinstance(raw, list) else set()
    return [r for r in PRODUCING_STAGES if r in marked and r in state]


def _write_stale(state: dict[str, Any], roles: set[str]) -> list[str]:
    ordered = [r for r in PRODUCING_STAGES if r in roles and r in state]
    if ordered:
        state[STALE_ROLES_KEY] = ordered
    else:
        # 空列表就把键删掉，state 里不留下一堆 `"stale_roles": []` 的噪音
        state.pop(STALE_ROLES_KEY, None)
    return ordered


def mark_stale(state: dict[str, Any], roles: Iterable[str]) -> list[str]:
    """把这些阶段并入过期记账（去重，按生产顺序）。"""
    return _write_stale(state, {*stale_roles(state), *roles})


def mark_fresh(state: dict[str, Any], role: str) -> list[str]:
    """这个阶段刚产出或刚被修订，一定是新鲜的——从记账里划掉。"""
    return _write_stale(state, {r for r in stale_roles(state) if r != role})


def variables_for(stage: str, state: dict[str, Any], lock: Any | None = None) -> dict[str, Any]:
    return _variables_for(stage, state, lock)


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
            summary = await _gate_summary(
                db, org_id=org_id, project_id=project_id, state=state, gate=gate
            )
            await repo.create_approval(
                db,
                org_id=org_id,
                project_id=project_id,
                gate=gate,
                payload={"stage": stage, "summary": summary},
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

    # 门① 锁定的画风/时代背景/改编模式要进提示词变量。读不到（还没走到门①、
    # 或存量项目没补上）就为 None，`_variables_for` 会退回按原文证据推。
    lock = await project_service.get_lock_variables(db, org_id=org_id, project_id=project_id)

    result = await runner.run_agent(
        db,
        org_id=org_id,
        project_id=project_id,
        spec=spec,
        # 一律从 state 拼输入，不直接用 user_input。
        # 直接用会让"素材"和"这一步要干什么"混成一句话，且后续阶段
        # （user_input 为空）走的是另一条代码路径——两条路径必然漂移。
        user_input=_input_for(role, state),
        variables=_variables_for(role, state, lock),
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
    # 正向产出永远是新鲜的：刚跑出来的这一版就是最新的上游。
    mark_fresh(state, stage)
    state["stage"] = _NEXT[stage]
    await _save(db, org_id=org_id, project_id=project_id, state=state)

    if stage == "plot_index":
        await _sync_detected_era(db, org_id=org_id, project_id=project_id, state=state)
    elif stage == "characters":
        await _sync_consistency(db, org_id=org_id, project_id=project_id, output=output)
    elif stage == "scenes":
        await _sync_scene_consistency(db, org_id=org_id, project_id=project_id, output=output)

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
        # 门① / 门③ 通过 = 用户亲自确认过那批锁定变量。**在同一个事务里盖戳**：
        # `_save` 下面才 commit，分开提交会出现"阶段推进了但没记确认"
        # 或者反过来的中间态，而这两种状态在界面上都解释不清。
        if approval.gate == "plan":
            await project_service.confirm_lock_variables(
                db, org_id=org_id, project_id=project_id, confirmed_by=resolved_by
            )
        elif approval.gate == "anchors":
            await project_service.confirm_anchors(db, org_id=org_id, project_id=project_id)
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


async def _sync_detected_era(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, state: dict[str, Any]
) -> None:
    """情节目录跑完后，把它判定的时代背景写进锁定变量，给门① 当默认值。

    **只在锁定变量还没被用户确认时写**。情节目录被打回重跑（门① 的
    `changes_requested` 就退回这里）之后再跑一次，不该把用户上一轮
    已经改过的人种判定冲掉——用户改它正是因为模型判错了。

    与 `_sync_consistency` 同样放在 `_save` 之后、失败不拖累这一步本身：
    这一步是一次真实的 LLM 调用，钱已经花了、产出已经入库，为一次写库
    失败把整步判失败等于让用户再花一次钱。而且这份数据是可补的——
    门① 打开时 `_plan_gate_summary` 会再写一次（`ensure_lock_variables`
    幂等），所以这里漏了不会变成死局。
    """
    detected = detect_era(state)
    if not any(detected.values()):
        return
    try:
        lock = await project_service.ensure_lock_variables(
            db, org_id=org_id, project_id=project_id, detected=detected
        )
        if lock.confirmed_at is None:
            lock.era = detected["era"]
            lock.region = detected["region"]
            lock.ethnicity = detected["ethnicity"]
            lock.era_evidence = detected["era_evidence"]
        await db.commit()
        log.info(
            "agent.era_detected",
            project_id=str(project_id),
            era=lock.era,
            ethnicity=lock.ethnicity,
            confirmed=lock.confirmed_at is not None,
        )
    except Exception as exc:  # 兜住一切：这个 except 的存在意义就是不让它冒泡
        await db.rollback()
        log.error("agent.era_sync_failed", project_id=str(project_id), error=repr(exc))


async def _sync_consistency(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, output: dict[str, Any]
) -> None:
    """角色档案跑完后，把它落成一致性引擎的资产包。

    在这之前，`consistency_style_profiles` / `consistency_character_profiles`
    只有 `scripts/validation_slice.py` 手工建过——正常项目流程从来没写过，
    所以产品界面里出的图既没有风格锁定也没有角色基准。出图链路要的
    就是这两张表。

    **放在 `_save` 之后，且失败不拖累这一步本身**，理由有三条：

    1. 角色阶段的产出是一次真实的 LLM 调用，钱已经花了、结果已经入库。
       为一次写库失败把整步判失败，用户重跑就是再花一次钱。
    2. 一致性资产包是可补的：出图端点在合成提示词之前会用同一个函数
       再同步一次（两个动作都幂等），所以这里漏了不会变成死局。
    3. 阶段推进的唯一真相是 `projects.current_state_json`，它在上面
       已经提交过了。这里再抛异常只会让"状态已经推进了但接口报错"
       这种更难解释的状态出现在用户面前。

    但**不能静默**：写不进去意味着后面点"生成基准立绘"会拿到一个
    前置条件错误，日志里必须留下真正的原因。
    """
    from apps.api.modules.consistency import service as consistency

    try:
        style, profiles = await consistency.sync_from_characters_output(
            db, org_id=org_id, project_id=project_id, output=output
        )
        await db.commit()
        log.info(
            "agent.consistency_synced",
            project_id=str(project_id),
            style_id=str(style.id),
            characters=len(profiles),
        )
    except Exception as exc:  # 兜住一切：这个 except 的存在意义就是不让它冒泡
        await db.rollback()
        log.error(
            "agent.consistency_sync_failed",
            project_id=str(project_id),
            error=repr(exc),
        )


async def _sync_scene_consistency(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, output: dict[str, Any]
) -> None:
    """场景档案跑完后，把它落成一致性引擎的资产包。

    `scene_profiles` 之于场景，等同于 `character_profiles` 之于角色：
    没有它，场景出图既拿不到摄影主轴也拿不到固定参照物，同一个房间
    两镜之间会挪位置。

    **放在 `_save` 之后，且失败不拖累这一步本身**——三条理由与
    `_sync_consistency` 一字不差，这里不重复推导，只重复结论：

    1. 场景阶段的产出是一次真实的 LLM 调用，钱已经花了、结果已经入库；
    2. 场景资产包是可补的，出图端点在合成提示词前会用同一个函数再同步
       一次（两个动作都幂等）；
    3. 阶段推进的唯一真相是 `projects.current_state_json`，它在上面已经
       提交过了，这里再抛异常只会制造更难解释的状态。

    同样**不能静默**：写不进去意味着后面点"生成参考图"会拿到一个前置
    条件错误，日志里必须留下真正的原因。
    """
    from apps.api.modules.consistency import service as consistency

    try:
        style, profiles = await consistency.sync_from_scenes_output(
            db, org_id=org_id, project_id=project_id, output=output
        )
        await db.commit()
        log.info(
            "agent.scene_consistency_synced",
            project_id=str(project_id),
            style_id=str(style.id),
            scenes=len(profiles),
        )
    except Exception as exc:  # 兜住一切：这个 except 的存在意义就是不让它冒泡
        await db.rollback()
        log.error(
            "agent.scene_consistency_sync_failed",
            project_id=str(project_id),
            error=repr(exc),
        )


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


def _location_moments(state: dict[str, Any]) -> str:
    """每个地点在剧本里出现过哪些时刻，一行一个。

    场景 Agent 要按这个生成光照状态。没有它，模型只能机械地把一天四段列全
    ——剧本里只在夜里出现过的渡口凭空多出一个"正午"，而多出来的状态是死的：
    没有任何镜头会引用它，用户却要在门③ 上确认它。

    时刻取自剧本每一场的 `time_mood`（"冬日上午 - 压抑"这种），原样透传不做
    解析：拆分它就要维护一张时间词表，而模型读整句比读一个被切坏的片段更准。
    """
    moments: dict[str, list[str]] = {}
    for ep in state.get("screenplay", {}).get("episodes", []):
        if not isinstance(ep, dict):
            continue
        for sc in ep.get("scenes", []):
            if not isinstance(sc, dict):
                continue
            loc = str(sc.get("location", "")).strip()
            if not loc:
                continue
            slot = moments.setdefault(loc, [])
            mood = str(sc.get("time_mood", "")).strip()
            if mood and mood not in slot:
                slot.append(mood)
    return "\n".join(
        f"- {loc}：{'、'.join(slots) if slots else '剧本未写明时刻'}"
        for loc, slots in sorted(moments.items())
    )


def _lighting_menu(state: dict[str, Any]) -> str:
    """每个场景声明了哪些光照状态，一行一个。分镜 Agent 从里面挑。"""
    lines: list[str] = []
    for scene in state.get("scenes", {}).get("scenes", []):
        if not isinstance(scene, dict):
            continue
        ref = str(scene.get("ref", "")).strip()
        if not ref:
            continue
        names = [
            name
            for s in scene.get("lighting_states", []) or []
            if isinstance(s, dict) and (name := str(s.get("name", "")).strip())
        ]
        default = str(scene.get("default_lighting", "")).strip()
        marked = [f"{n}（默认）" if n == default else n for n in names]
        lines.append(f"- {ref}：{'、'.join(marked) if marked else '未声明，留空即可'}")
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
        return (
            f"{_screenplay_block(state)}\n\n"
            f"剧本涉及的地点与它们出现过的时刻：\n{_location_moments(state)}\n\n"
            f"【原著节选】\n{source[:SOURCE_EXCERPT_CHARS]}\n\n"
            "请为每个地点建立场景档案。"
            "lighting_states 只覆盖上面列出的时刻，不要把一天四段机械地列全。"
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
                # 光照状态是**按场景**声明的有限集合，所以必须按场景分行发下去。
                # 合成一个大列表会让模型把 A 场景的"夜巡灯"填给 B 场景，
                # 而那条引用解析不到，只会静默降级成 B 的默认光。
                "各场景可用的光照状态（lighting_ref 只能填对应场景的这几个之一）：",
                _lighting_menu(state),
                "",
                "请先列分镜节点清单，再逐节点拆镜号。",
                "character_refs 和 scene_ref 只能用上面列出的 ref，不要新造。",
            ]
        )

    return ""


# 题材 → 时代背景 → 人种，**兜底用**。
#
# 这张表在 ADR-037 之前是唯一的判定来源，且默认值是"现代中国，东亚面孔"。
# ADR-037 第 2 条明确禁止这么做：**判定结果不得默认套用本国**——判错会让
# 角色人种、服装、发型与场景建筑、街景、室内陈设全部错位，且错误一路
# 传导到分镜，那时的返工成本是"全部重出"。
#
# 所以现在的优先级是：
#
#   1. 门① 锁定的值（`project_lock_variables`，用户确认过或至少看过）
#   2. 情节目录 Agent 按原文证据给的判定（`plot_index.era` / `.ethnicity`）
#   3. 题材关键词——只在题材本身就说明了时代时命中，命不中就不猜
#
# 三条都落空时给的不是一个具体人种，而是一句**要求判定的指令**：
# 让下游 Agent 去看剧本，而不是替它按本国填一个。
_ERA_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("古装", "仙侠", "武侠", "宫斗"), "古代东方", "东亚面孔"),
    (("西方奇幻", "魔法", "骑士"), "西方奇幻", "欧洲面孔"),
    (("科幻", "未来", "赛博"), "未来世界", "按剧本内证据判定"),
)
_ERA_UNDETERMINED = (
    "未判定",
    "按剧本内证据判定人种，不得默认套用中国现代人",
)


def detect_era(state: dict[str, Any]) -> dict[str, str]:
    """按原文证据推一版时代背景，给门① 当默认值。

    优先用情节目录 Agent 自己判的（它读过全文，证据比关键词强），
    退而求其次才看题材关键词。两条都落空就**留空**——留空会在门① 上
    显示成"未判定，请确认"，而填一个"现代中国"会显示成一个看起来
    已经想好了的答案，用户多半直接点通过。
    """
    plot = state.get("plot_index", {})
    era = str(plot.get("era", "")).strip()
    region = str(plot.get("region", "")).strip()
    ethnicity = str(plot.get("ethnicity", "")).strip()
    evidence = str(plot.get("era_evidence", "")).strip()

    if not era or not ethnicity:
        genre = str(plot.get("genre", ""))
        for keywords, guess_era, guess_ethnicity in _ERA_RULES:
            if any(k in genre for k in keywords):
                era = era or guess_era
                ethnicity = ethnicity or guess_ethnicity
                evidence = evidence or f"题材关键词：{genre}"
                break

    return {"era": era, "region": region, "ethnicity": ethnicity, "era_evidence": evidence}


def _era_of(state: dict[str, Any], lock: Any | None = None) -> tuple[str, str]:
    """给提示词用的 (时代背景, 人种) 二元组。

    `lock` 是门① 锁定的变量，非空且填了值时**永远优先**：用户确认过的
    判定不能被一次情节目录重跑冲掉。
    """
    if lock is not None and (str(lock.era).strip() or str(lock.ethnicity).strip()):
        era = str(lock.era).strip() or _ERA_UNDETERMINED[0]
        ethnicity = str(lock.ethnicity).strip() or _ERA_UNDETERMINED[1]
        if region := str(lock.region).strip():
            era = f"{era}（{region}）"
        return era, ethnicity

    detected = detect_era(state)
    return (
        detected["era"] or _ERA_UNDETERMINED[0],
        detected["ethnicity"] or _ERA_UNDETERMINED[1],
    )


# 改编模式 → 给 `story.screenplay.v1` 的那段指令。
#
# 整条流水线上**唯一一个用户必须做的分支选择**（ADR-037 第 2 条），
# 所以它落成一段明确的指令而不是一个布尔量：提示词里出现的是完整要求，
# Agent 不需要知道有几种模式，也不需要自己解释 "rewrite" 是什么意思。
_ADAPTATION_INSTRUCTIONS: dict[str, str] = {
    "adapt": (
        "本次是**改编**：保留原著的角色名、地名、场景设定与台词内容，"
        "只把叙述性文字转成可拍的场次。不要替换任何人名或地名。"
    ),
    "rewrite": (
        "本次是**洗稿**：保留原著的情节骨架、冲突结构与节奏，"
        "但角色名、地名与具体台词全部换成原创。换名要成体系"
        "（同一个角色全剧同一个新名字，且新名字要与判定出的时代背景、"
        "国别相符），不要中途改口，也不要把原著的专有名词残留在台词里。"
    ),
}


def adaptation_instruction(mode: str) -> str:
    return _ADAPTATION_INSTRUCTIONS.get(mode, _ADAPTATION_INSTRUCTIONS["adapt"])


def _variables_for(stage: str, state: dict[str, Any], lock: Any | None = None) -> dict[str, Any]:
    router = state.get("router", {})
    era, ethnicity = _era_of(state, lock)
    plot = state.get("plot_index", {})
    mode = str(getattr(lock, "adaptation_mode", "") or "adapt")
    return {
        "target_duration_seconds": router.get("estimated_duration_seconds", 60),
        "target_shots": router.get("estimated_shots", 12),
        "era": era,
        "default_ethnicity": ethnicity,
        "plot_index": _plot_index_block(state) if plot else "（无）",
        "adaptation_mode": mode,
        "adaptation_instruction": adaptation_instruction(mode),
    }


async def _gate_summary(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    state: dict[str, Any],
    gate: str,
) -> dict[str, Any]:
    """给前端展示用的门摘要。

    两道既有门（setup / storyboard）只放数字和标题，不放大段内容——那两屏
    的正文前端本来就能从 `current_state_json` 读到。两道新门不一样：

    - 门① 要用户在**一屏**里定四件事，其中画风目录和改编模式的可选项
      前端没有别的地方能读到，必须跟着摘要一起给；
    - 门③ 要一次性展示全部锚点卡（ADR-037 明禁逐个确认），卡片内容就是
      这道门的正文。

    这个函数因此变成 async：画风目录在库里（`style_catalog`），
    不在代码里。
    """
    if gate == "plan":
        return await _plan_gate_summary(db, org_id=org_id, project_id=project_id, state=state)

    if gate == "anchors":
        return anchors.gate_payload(state)

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
        # 分镜跑完才知道的真实镜号数/同框人数/运镜，可能让某个场景越过
        # 锚点卡判据，而门③ 上按剧本估的下界没把它算进去。空列表是常态。
        # 详见 `anchors.py` 顶部对那处顺序矛盾的说明。
        "anchor_gaps": anchors.reconcile_after_storyboard(state),
    }


async def _plan_gate_summary(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, state: dict[str, Any]
) -> dict[str, Any]:
    """门① 的摘要：四件事一屏定完。

    锁定变量在这里**建行**（不是在项目创建时）：建行意味着"这个项目已经
    有画风了"，而在门① 打开之前它还没有。等到门口再建，"从没问过用户"
    和"用户选了缺省画风"就不会变成同一种状态。
    """
    from apps.api.modules.consistency import service as consistency
    from apps.api.modules.project import service as project_service

    lock = await project_service.ensure_lock_variables(
        db, org_id=org_id, project_id=project_id, detected=detect_era(state)
    )
    await db.commit()

    plot = state.get("plot_index", {})
    catalog = await consistency.list_style_catalog(db)
    return {
        # 情节目录**给全量**，不只给条数：这道门要用户回答的是
        # "以上为本次将覆盖的全部情节，有没有遗漏"，只给一个数字答不了。
        "genre": plot.get("genre", ""),
        "logline": plot.get("logline", ""),
        "nodes": [
            {"index": n.get("index"), "summary": n.get("summary")}
            for n in plot.get("nodes", [])
            if isinstance(n, dict)
        ],
        "nodes_total": len(plot.get("nodes", [])),
        "era": {
            "era": lock.era,
            "region": lock.region,
            "ethnicity": lock.ethnicity,
            "evidence": lock.era_evidence,
            # 判不出来时如实说，不要在界面上显示一个像是想好了的答案
            "undetermined": not (lock.era and lock.ethnicity),
        },
        "style": {
            "selected": lock.style_key,
            "options": [
                {
                    "key": e.key,
                    "name": e.name,
                    "description": e.description,
                    "character_tokens": e.character_tokens,
                    "scene_tokens": e.scene_tokens,
                    "video_tokens": e.video_tokens,
                }
                for e in catalog
            ],
        },
        "adaptation": {
            "selected": lock.adaptation_mode,
            "options": list(project_service.ADAPTATION_MODES),
        },
        "legacy_unconfirmed": lock.legacy_unconfirmed,
    }
