"""编排链路：路线 → 情节目录 →【门①】→ 剧本 →【门②】
→ 角色 → 场景 →【门③】→ 分镜 →【门④】→ done。

重点验证两件事：
1. 状态完全由 `projects.current_state_json` 决定，不依赖内存（ADR-008）
2. 四道门真的能挡住，且用户的决策能改变走向（ADR-037）

四道门各自的开门 / 通过 / 打回 / 打回后重跑在文件末尾的「四道门」一节里
逐道覆盖；这一节前面的用例只把门当成路上的关卡，用 `advance_to_gate`
穿过去，不写死道数。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.agent import orchestrator
from apps.api.modules.agent.models import AgentRun, AgentStep
from tests.conftest import advance_to_gate

pytestmark = pytest.mark.integration

P = "/api/v1/projects"

NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名侦探。"


async def _project(client: AsyncClient) -> str:
    return str((await client.post(P, json={"title": "orchestration"})).json()["id"])


async def _advance(client: AsyncClient, pid: str, text: str = "", *, to_gate: bool = True) -> dict:
    r = await client.post(
        f"{P}/{pid}/advance?to_gate={str(to_gate).lower()}", json={"user_input": text}
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _pending(client: AsyncClient, pid: str) -> dict | None:
    rows = (await client.get(f"{P}/{pid}/approvals")).json()
    return next((r for r in rows if r["status"] == "pending"), None)


# ------------------------------------------------------------------ 主链路


async def test_runs_until_first_gate(alice: AsyncClient) -> None:
    """一路跑到第一个门就必须停下。

    ADR-037 之后第一道门是**开拍前确认**（门①），不再是剧本门：
    画风、时代背景与改编模式必须在剧本写出来之前定下来，
    否则改编模式一改就要把整份剧本重写一遍。
    """
    pid = await _project(alice)
    result = await _advance(alice, pid, NOVEL)

    assert result["blocked"] is True
    assert result["gate_opened"] == "plan"
    assert result["stage"] == "await_plan"

    pending = await _pending(alice, pid)
    assert pending is not None
    assert pending["gate"] == "plan"

    # 剧本这时候还不该存在：门① 在 screenplay 之前
    runs = (await alice.get(f"{P}/{pid}/agent-runs")).json()
    assert not [r for r in runs if r["agent_id"] == "story.screenplay.v1"]


async def test_gate_actually_blocks(alice: AsyncClient) -> None:
    """不确认就不能往下走。反复调用也只能停在门口。"""
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)

    for _ in range(3):
        result = await _advance(alice, pid)
        assert result["blocked"] is True
        assert result["stage"] == "await_plan"


async def test_approve_advances_to_next_stage(alice: AsyncClient) -> None:
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)
    pending = await _pending(alice, pid)
    assert pending

    r = await alice.post(f"{P}/{pid}/approvals/{pending['id']}", json={"decision": "approved"})
    assert r.status_code == 200
    assert r.json()["stage"] == "screenplay"

    # 继续跑：下一个停下来的地方是剧本门
    result = await _advance(alice, pid)
    assert result["gate_opened"] == "setup"
    assert result["stage"] == "await_setup"


async def test_changes_requested_sends_it_back(alice: AsyncClient) -> None:
    """打回要退到产出这批内容的阶段重做，而不是原地卡住。"""
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "setup", user_input=NOVEL)
    pending = await _pending(alice, pid)
    assert pending

    r = await alice.post(
        f"{P}/{pid}/approvals/{pending['id']}",
        json={"decision": "changes_requested", "comment": "冲突不够强"},
    )
    assert r.status_code == 200
    assert r.json()["stage"] == "screenplay", "打回应退回剧本阶段重写"


async def test_same_approval_cannot_be_resolved_twice(alice: AsyncClient) -> None:
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)
    pending = await _pending(alice, pid)
    assert pending

    first = await alice.post(f"{P}/{pid}/approvals/{pending['id']}", json={"decision": "approved"})
    second = await alice.post(f"{P}/{pid}/approvals/{pending['id']}", json={"decision": "rejected"})
    assert first.status_code == 200
    assert second.status_code == 409


async def test_full_run_to_completion(alice: AsyncClient) -> None:
    """走完全部四道门到 done。

    循环上界写 8 而不是 4：这条用例要验的是"通过所有门之后能走到 done"，
    写死门的道数会让加一道门时这里静默地少通一道，然后在末尾那句
    `stage == "done"` 上报一个跟原因无关的错。
    """
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)

    gates_passed: list[str] = []
    for _ in range(8):
        pending = await _pending(alice, pid)
        if pending is None:
            break
        gates_passed.append(pending["gate"])
        await alice.post(f"{P}/{pid}/approvals/{pending['id']}", json={"decision": "approved"})
        await _advance(alice, pid)

    assert gates_passed == ["plan", "setup", "anchors", "storyboard"], gates_passed

    project = (await alice.get(f"{P}/{pid}/state")).json()
    assert project["stage"] == "done"

    state = (await alice.get(f"{P}/{pid}")).json()
    assert state["status"] in ("draft", "producing", "review", "completed")

    runs = (await alice.get(f"{P}/{pid}/agent-runs")).json()
    roles = {r["role"] for r in runs}
    assert {"router", "story", "visual"} <= roles


# ------------------------------------------------------------------ 原始素材传递

# 一个不可能被模型自己编出来的串。断言它出现在下游输入里，
# 才能证明素材是真的传下去了，而不是碰巧写得像。
MARKER = "警视厅附属犯罪资料馆"
SOURCE = (
    f"寺田聪站在锈迹斑斑的铁门前。门旁柱子上依稀可见「{MARKER}」的斑驳字样。"
    "馆长绯色冴子一袭白衣，肤色苍白，黑色长发。"
    "把这篇小说做成 5 分钟悬疑漫剧。"
)


async def _sent_to(db: AsyncSession, pid: str, role: str) -> str:
    """某个角色最后一次运行实际收到的输入。

    `input_json` 不走 API——它可能装着两万字原文，挂在列表接口上
    每次刷新都要传一遍。
    """
    from sqlalchemy import select

    stmt = (
        select(AgentRun)
        .where(AgentRun.project_id == uuid.UUID(pid), AgentRun.role == role)
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    run = (await db.execute(stmt)).scalars().one()
    return str(run.input_json["user_input"])


async def test_source_material_reaches_the_story_agent(
    alice: AsyncClient, db: AsyncSession
) -> None:
    """用户上传的原文必须传到 Story。

    曾经这里只发"路线：X\\n请产出故事结构。"——小说在 Router 之后就丢了，
    Story 凭空编一个故事出来，而且编得像模像样，不看输入根本看不出错在哪。
    这是 need.md 里"生成的故事和我上传的完全不搭架"的根因。
    """
    pid = await _project(alice)
    await _advance(alice, pid, SOURCE)

    assert MARKER in await _sent_to_agent(db, pid, "story.plot_index.v1"), "没收到原始素材"


async def test_source_material_is_persisted(alice: AsyncClient, db: AsyncSession) -> None:
    """素材落库，且在跑 Agent 之前落。

    存不下来，后面每个阶段都得靠用户重新贴一遍。
    """
    from apps.api.modules.project import service as project_service

    pid = await _project(alice)
    org_id = uuid.UUID((await alice.get("/api/v1/auth/me")).json()["org_id"])
    await _advance(alice, pid, SOURCE)

    await db.commit()
    project = await project_service.get_project(db, org_id=org_id, project_id=uuid.UUID(pid))
    assert MARKER in project.current_state_json["source"]


async def _sent_to_agent(db: AsyncSession, pid: str, agent_id: str) -> str:
    from sqlalchemy import select

    stmt = (
        select(AgentRun)
        .where(AgentRun.project_id == uuid.UUID(pid), AgentRun.agent_id == agent_id)
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    run = (await db.execute(stmt)).scalars().one()
    return str(run.input_json["user_input"])


async def test_downstream_stages_get_the_full_screenplay(
    alice: AsyncClient, db: AsyncSession
) -> None:
    """角色/场景/分镜都要拿到**完整剧本**，不是摘要。

    只给标题和一句 logline，角色姓名和场景只能靠猜；
    猜出来的名字和剧本对不上，一致性引擎后面全是错的。
    """
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "storyboard", user_input=SOURCE)

    chars = await _sent_to_agent(db, pid, "visual.character.v1")
    assert "△" in chars, "没带上剧本正文的动作行"
    assert MARKER in chars, "没带上原著节选"

    board = await _sent_to_agent(db, pid, "visual.storyboard.v1")
    assert "可用角色 ref：" in board, "分镜必须只能用已有的角色 ref，不能新造"
    assert "可用场景 ref：" in board


async def test_screenplay_gets_the_plot_index(alice: AsyncClient, db: AsyncSession) -> None:
    """剧本阶段要拿到情节目录，才能逐节点覆盖。

    剧本在门① 之后跑，所以要先通过门① 才有 `story.screenplay.v1` 这一条 run。
    """
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "setup", user_input=SOURCE)

    sent = await _sent_to_agent(db, pid, "story.screenplay.v1")
    assert "情节目录：" in sent
    assert MARKER in sent, "剧本阶段也要能看到原著全文"


async def test_router_still_sees_the_raw_text(alice: AsyncClient, db: AsyncSession) -> None:
    pid = await _project(alice)
    await _advance(alice, pid, SOURCE)

    assert MARKER in await _sent_to_agent(db, pid, "router.default.v1")


# ------------------------------------------------------------------ 存量数据


async def test_legacy_stage_names_still_advance(alice: AsyncClient, db: AsyncSession) -> None:
    """库里存着旧阶段名的项目不能一点就 500。

    状态存在库里（ADR-008），改阶段枚举就是一次数据迁移。
    2026-08-18 阶段图从 story/visual 两步改成五步时漏了这条，
    存量项目在 `_NEXT[stage]` 上 KeyError，用户看到的是"点继续没反应"。
    """
    from apps.api.modules.project import service as project_service

    pid = await _project(alice)
    org_id = uuid.UUID((await alice.get("/api/v1/auth/me")).json()["org_id"])
    await _advance(alice, pid, SOURCE)

    for legacy, expect_ran in (("story", "plot_index"), ("visual", "plot_index")):
        project = await project_service.get_project(db, org_id=org_id, project_id=uuid.UUID(pid))
        # 模拟旧版留下的状态：只有旧阶段名，没有新阶段的产出
        project.current_state_json = {"stage": legacy, "router": {"route": "NOVEL_TO_ANIME"}}
        await db.commit()

        result = await _advance(alice, pid, to_gate=False)
        # visual 缺上游产出，要退回到能真正跑起来的那一步而不是拿空数据硬跑
        assert result["ran_role"] == expect_ran, f"{legacy} 没有正确迁移"


async def test_rewind_skips_nothing_when_upstream_exists(
    alice: AsyncClient, db: AsyncSession
) -> None:
    """上游齐全时不要乱退——退了就是白花一次钱重做。"""
    from apps.api.modules.agent.orchestrator import rewind_to_runnable

    state = {"plot_index": {}, "screenplay": {}, "characters": {}, "scenes": {}}
    assert rewind_to_runnable("storyboard", state) == "storyboard"
    assert rewind_to_runnable("characters", state) == "characters"

    # 缺 scenes 时，storyboard 退到 scenes；再缺 screenplay 就一路退到 plot_index
    assert rewind_to_runnable("storyboard", {"plot_index": {}, "screenplay": {}}) == "scenes"
    assert rewind_to_runnable("storyboard", {}) == "plot_index"


# ------------------------------------------------------------------ 状态无内存依赖


async def test_stage_is_derived_purely_from_db(alice: AsyncClient, db: AsyncSession) -> None:
    """状态完全存在库里（ADR-008）。

    直接改库里的 stage，编排器就该按新状态走——
    这证明它没有依赖任何内存或图状态。进程重启、Worker 崩溃、
    用户隔天回来续做，行为才能一致。
    """
    from apps.api.modules.project import service as project_service

    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)

    project = await project_service.get_project(
        db,
        org_id=uuid.UUID((await alice.get("/api/v1/auth/me")).json()["org_id"]),
        project_id=uuid.UUID(pid),
    )
    assert orchestrator.current_stage(project.current_state_json) == "await_plan"

    # 手工把状态改回剧本阶段
    project.current_state_json = {**project.current_state_json, "stage": "screenplay"}
    await db.commit()

    # 单步推进：一路跑到门口的话，最后返回的是"开门"那步（ran_role=None）
    result = await _advance(alice, pid, to_gate=False)
    assert result["ran_role"] == "screenplay", "应按库里的状态重新执行剧本阶段"


# ------------------------------------------------------------------ Router 追问


async def test_router_asks_for_clarification_on_thin_input(alice: AsyncClient) -> None:
    """信息不足时要追问，不能瞎猜。

    猜错路线会让用户白跑一整条生产链。
    """
    pid = await _project(alice)
    result = await _advance(alice, pid, "做个")

    assert result["blocked"] is True
    assert result["ran_role"] == "routing"
    assert result["output"]["requires_clarification"] is True
    assert result["output"]["clarification_question"]
    assert result["stage"] == "routing", "追问时不能推进阶段"


# ------------------------------------------------------------------ 可追溯


async def test_agent_runs_record_the_resolved_prompt(alice: AsyncClient, db: AsyncSession) -> None:
    """必须存下真正下发的提示词全文（17_ConsistencyEngine.md §7）。

    没有它，"为什么这一步输出崩了"永远查不清，也没法复现。
    """
    from sqlalchemy import select

    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)

    runs = list(
        (await db.execute(select(AgentRun).where(AgentRun.project_id == uuid.UUID(pid)))).scalars()
    )
    assert runs

    steps = list(
        (
            await db.execute(select(AgentStep).where(AgentStep.run_id.in_([r.id for r in runs])))
        ).scalars()
    )
    assert any(s.resolved_prompt for s in steps), "至少要存下第一步的完整提示词"
    assert any(s.raw_output for s in steps), "原始输出也要留，用于排查解析失败"


async def test_agent_output_is_schema_validated(alice: AsyncClient) -> None:
    """输出必须匹配声明的 schema。"""
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "setup", user_input=NOVEL)

    runs = (await alice.get(f"{P}/{pid}/agent-runs")).json()
    script = next(r for r in runs if r["agent_id"] == "story.screenplay.v1")
    out = script["output_json"]

    assert out["title"]
    assert out["episodes"]
    scenes = [sc for ep in out["episodes"] for sc in ep["scenes"]]
    assert scenes and all(sc["beats"] for sc in scenes)
    # 每个节拍只装一件事，动作和台词不混写
    assert all(
        b["kind"] in ("action", "dialogue", "vo", "sfx") for sc in scenes for b in sc["beats"]
    )


# ------------------------------------------------------------------ 隔离


async def test_orchestration_is_org_scoped(alice: AsyncClient, bob: AsyncClient) -> None:
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)

    assert (await bob.post(f"{P}/{pid}/advance", json={"user_input": "x"})).status_code == 404
    assert (await bob.get(f"{P}/{pid}/approvals")).json() == []
    assert (await bob.get(f"{P}/{pid}/agent-runs")).json() == []


async def test_agents_endpoint_lists_registry(alice: AsyncClient) -> None:
    r = await alice.get("/api/v1/agents")
    assert r.status_code == 200
    body = r.json()
    assert {a["role"] for a in body["agents"]} >= {"router", "story", "visual"}
    # 加载失败的 spec 要能看见，静默跳过会让人不知道自己的 Agent 为什么没生效
    assert "errors" in body
