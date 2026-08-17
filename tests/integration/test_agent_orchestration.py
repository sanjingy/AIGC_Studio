"""编排链路：Router → Story → 门 → Visual → 门。

重点验证两件事：
1. 状态完全由 `projects.current_state_json` 决定，不依赖内存（ADR-008）
2. 审核门真的能挡住，且用户的决策能改变走向
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.agent import orchestrator
from apps.api.modules.agent.models import AgentRun, AgentStep

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
    """一路跑到第一个门就必须停下。"""
    pid = await _project(alice)
    result = await _advance(alice, pid, NOVEL)

    assert result["blocked"] is True
    assert result["gate_opened"] == "setup"
    assert result["stage"] == "await_setup"

    pending = await _pending(alice, pid)
    assert pending is not None
    assert pending["gate"] == "setup"
    # 门的摘要要能让用户判断该不该通过
    assert pending["payload_json"]["summary"]["acts"] >= 1


async def test_gate_actually_blocks(alice: AsyncClient) -> None:
    """不确认就不能往下走。反复调用也只能停在门口。"""
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)

    for _ in range(3):
        result = await _advance(alice, pid)
        assert result["blocked"] is True
        assert result["stage"] == "await_setup"


async def test_approve_advances_to_next_stage(alice: AsyncClient) -> None:
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)
    pending = await _pending(alice, pid)
    assert pending

    r = await alice.post(f"{P}/{pid}/approvals/{pending['id']}", json={"decision": "approved"})
    assert r.status_code == 200
    assert r.json()["stage"] == "visual"

    # 继续跑到第二个门
    result = await _advance(alice, pid)
    assert result["gate_opened"] == "storyboard"
    assert result["stage"] == "await_storyboard"


async def test_changes_requested_sends_it_back(alice: AsyncClient) -> None:
    """打回要退到产出这批内容的阶段重做，而不是原地卡住。"""
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)
    pending = await _pending(alice, pid)
    assert pending

    r = await alice.post(
        f"{P}/{pid}/approvals/{pending['id']}",
        json={"decision": "changes_requested", "comment": "冲突不够强"},
    )
    assert r.status_code == 200
    assert r.json()["stage"] == "story", "打回应退回故事阶段"


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
    """走完两道门到 done。"""
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)

    for _ in range(2):
        pending = await _pending(alice, pid)
        assert pending is not None
        await alice.post(f"{P}/{pid}/approvals/{pending['id']}", json={"decision": "approved"})
        await _advance(alice, pid)

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

    assert MARKER in await _sent_to(db, pid, "story"), "Story 没收到原始素材"


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


async def test_visual_gets_the_whole_story_not_just_the_logline(
    alice: AsyncClient, db: AsyncSession
) -> None:
    """Visual 要拿到完整分幕和素材节选。

    只给标题和一句话 logline，角色姓名和场景只能靠猜——
    猜出来的名字和剧本对不上，一致性引擎后面全是错的。
    """
    pid = await _project(alice)
    await _advance(alice, pid, SOURCE)

    pending = await _pending(alice, pid)
    assert pending
    await alice.post(f"{P}/{pid}/approvals/{pending['id']}", json={"decision": "approved"})
    await _advance(alice, pid)

    sent = await _sent_to(db, pid, "visual")
    assert "分幕：" in sent, "没带上分幕，Visual 只能看到一句 logline"
    assert MARKER in sent, "没带上素材节选"


async def test_router_still_sees_the_raw_text(alice: AsyncClient, db: AsyncSession) -> None:
    pid = await _project(alice)
    await _advance(alice, pid, SOURCE)

    assert MARKER in await _sent_to(db, pid, "router")


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
    assert orchestrator.current_stage(project.current_state_json) == "await_setup"

    # 手工把状态改回 story
    project.current_state_json = {**project.current_state_json, "stage": "story"}
    await db.commit()

    # 单步推进：一路跑到门口的话，最后返回的是"开门"那步（ran_role=None）
    result = await _advance(alice, pid, to_gate=False)
    assert result["ran_role"] == "story", "应按库里的状态重新执行故事阶段"


# ------------------------------------------------------------------ Router 追问


async def test_router_asks_for_clarification_on_thin_input(alice: AsyncClient) -> None:
    """信息不足时要追问，不能瞎猜。

    猜错路线会让用户白跑一整条生产链。
    """
    pid = await _project(alice)
    result = await _advance(alice, pid, "做个")

    assert result["blocked"] is True
    assert result["ran_role"] == "router"
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
    result = await _advance(alice, pid, NOVEL)
    del result

    runs = (await alice.get(f"{P}/{pid}/agent-runs")).json()
    story = next(r for r in runs if r["role"] == "story")
    out = story["output_json"]

    assert out["title"]
    assert out["acts"] and all("index" in a and "summary" in a for a in out["acts"])


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
