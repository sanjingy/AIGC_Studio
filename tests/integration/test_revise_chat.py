"""聊天修订链路。

验证三条规则真的成立：
1. 聊天是输入，不是真相——状态仍写回 `projects.current_state_json`
2. 修订产出仍是同一个 schema，不因为"是聊天"就退化成自由文本
3. 每轮版本 +1，旧版留在 agent_runs 里
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import advance_to_gate

pytestmark = pytest.mark.integration

P = "/api/v1/projects"

NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名侦探。"


async def _project(client: AsyncClient) -> str:
    return str((await client.post(P, json={"title": "revise"})).json()["id"])


async def _with_story(client: AsyncClient) -> str:
    """跑到剧本门。开拍前确认门（门①）在它之前，由 `advance_to_gate` 顺手通过。"""
    pid = await _project(client)
    await advance_to_gate(client, pid, "setup", user_input=NOVEL)
    return pid


async def _approve_all(client: AsyncClient, pid: str) -> None:
    """一路通过剩下的门，直到分镜门——五个生产阶段全部有产出。"""
    await advance_to_gate(client, pid, "storyboard")


# ------------------------------------------------------------------ 主链路


async def test_revise_returns_the_same_schema(alice: AsyncClient) -> None:
    """产出仍是结构化的。聊天只是输入形式。"""
    pid = await _with_story(alice)

    r = await alice.post(
        f"{P}/{pid}/revise",
        json={"target_role": "screenplay", "instruction": "把基调改得更压抑一些"},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["target_role"] == "screenplay"
    assert body["revision"] == 1
    # Screenplay 的必填字段一个都不能少
    out = body["output"]
    assert out["title"] and out["synopsis"]
    assert out["episodes"] and out["node_coverage"]
    scenes = [sc for ep in out["episodes"] for sc in ep["scenes"]]
    assert scenes and all(sc["beats"] for sc in scenes)


async def test_state_is_written_back_to_the_project(alice: AsyncClient, db: AsyncSession) -> None:
    """状态的唯一权威仍是 current_state_json（ADR-008）。

    修订结果不写回去，下一步编排读到的还是旧版。
    """
    from apps.api.modules.project import service as project_service

    pid = await _with_story(alice)
    org_id = uuid.UUID((await alice.get("/api/v1/auth/me")).json()["org_id"])

    body = (
        await alice.post(
            f"{P}/{pid}/revise",
            json={"target_role": "screenplay", "instruction": "冲突再强一点"},
        )
    ).json()

    await db.commit()  # 丢掉本会话的快照，强制重读
    project = await project_service.get_project(db, org_id=org_id, project_id=uuid.UUID(pid))
    assert project.current_state_json["screenplay"] == body["output"]


async def test_revision_number_increments(alice: AsyncClient) -> None:
    """每轮 +1。"改回上一版"是高频需求，不能改一次丢一次。"""
    pid = await _with_story(alice)

    for expected in (1, 2, 3):
        r = await alice.post(
            f"{P}/{pid}/revise",
            json={"target_role": "screenplay", "instruction": f"第 {expected} 次修改"},
        )
        assert r.json()["revision"] == expected


async def test_conversation_records_both_sides(alice: AsyncClient) -> None:
    pid = await _with_story(alice)
    await alice.post(
        f"{P}/{pid}/revise",
        json={"target_role": "screenplay", "instruction": "主角改成女性"},
    )

    msgs = (await alice.get(f"{P}/{pid}/conversation")).json()
    assert [m["author"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["text"] == "主角改成女性"
    assert msgs[1]["revision"] == 1

    # assistant 消息必须指回那次运行，否则"它凭什么这么改"无从追查
    runs = (await alice.get(f"{P}/{pid}/agent-runs")).json()
    assert msgs[1]["run_id"] in {r["id"] for r in runs}


async def test_revise_contract_reaches_the_model(alice: AsyncClient, db: AsyncSession) -> None:
    """修订的角色指令必须真的下发到系统提示词里。

    只写在用户消息里是不够的——spec 的提示词说的是"创作"，
    模型会按创作的角色理解输入，把已有产出原样抄回来。
    """
    from sqlalchemy import select

    from apps.api.modules.agent.models import AgentRun, AgentStep

    pid = await _with_story(alice)
    await alice.post(
        f"{P}/{pid}/revise", json={"target_role": "screenplay", "instruction": "改基调"}
    )

    runs = list(
        (
            await db.execute(
                select(AgentRun)
                .where(
                    AgentRun.project_id == uuid.UUID(pid),
                    AgentRun.agent_id == "story.screenplay.v1",
                )
                .order_by(AgentRun.created_at.desc())
            )
        ).scalars()
    )
    steps = list(
        (await db.execute(select(AgentStep).where(AgentStep.run_id == runs[0].id))).scalars()
    )
    prompt = next(s.resolved_prompt for s in steps if s.resolved_prompt)
    assert "禁止原样返回当前产出" in prompt
    assert "修订已有产出" in prompt


async def test_old_versions_stay_in_agent_runs(alice: AsyncClient) -> None:
    """旧版不能被覆盖掉——回退要有东西可回。"""
    pid = await _with_story(alice)
    before = (await alice.get(f"{P}/{pid}/agent-runs")).json()

    await alice.post(
        f"{P}/{pid}/revise", json={"target_role": "screenplay", "instruction": "改一下"}
    )
    after = (await alice.get(f"{P}/{pid}/agent-runs")).json()

    assert len(after) == len(before) + 1
    story_runs = [r for r in after if r["agent_id"] == "story.screenplay.v1" and r["output_json"]]
    assert len(story_runs) >= 2, "原始版本必须还在"


async def test_upstream_revision_marks_downstream_stale(alice: AsyncClient) -> None:
    """改了故事，视觉是基于旧故事生成的。

    不说这件事，用户会以为改完就同步了，直到成片里对不上。
    """
    pid = await _with_story(alice)
    await _approve_all(alice, pid)

    r = await alice.post(
        f"{P}/{pid}/revise", json={"target_role": "screenplay", "instruction": "换个结局"}
    )
    assert r.json()["stale_roles"] == ["characters", "scenes", "storyboard"]


async def test_downstream_revision_has_no_stale(alice: AsyncClient) -> None:
    pid = await _with_story(alice)
    await _approve_all(alice, pid)

    r = await alice.post(
        f"{P}/{pid}/revise", json={"target_role": "storyboard", "instruction": "镜头再紧一点"}
    )
    assert r.json()["stale_roles"] == []


# ------------------------------------------------------------------ 拒绝的情况


async def test_cannot_revise_what_does_not_exist_yet(alice: AsyncClient) -> None:
    pid = await _project(alice)
    r = await alice.post(
        f"{P}/{pid}/revise", json={"target_role": "screenplay", "instruction": "改一下"}
    )
    assert r.status_code == 404


async def test_unrevisable_role_is_rejected(alice: AsyncClient) -> None:
    """router / director 的产出不给聊天改——那是调度决策，不是内容。"""
    pid = await _with_story(alice)
    r = await alice.post(
        f"{P}/{pid}/revise", json={"target_role": "router", "instruction": "换条路线"}
    )
    assert r.status_code == 422


async def test_empty_instruction_is_rejected(alice: AsyncClient) -> None:
    pid = await _with_story(alice)
    r = await alice.post(
        f"{P}/{pid}/revise", json={"target_role": "screenplay", "instruction": "   "}
    )
    assert r.status_code in (400, 422)


async def test_overlong_instruction_is_rejected(alice: AsyncClient) -> None:
    pid = await _with_story(alice)
    r = await alice.post(
        f"{P}/{pid}/revise",
        json={"target_role": "screenplay", "instruction": "改" * 3000},
    )
    assert r.status_code == 422


# ------------------------------------------------------------------ 隔离


async def test_revise_is_org_scoped(alice: AsyncClient, bob: AsyncClient) -> None:
    pid = await _with_story(alice)

    r = await bob.post(
        f"{P}/{pid}/revise", json={"target_role": "screenplay", "instruction": "偷改"}
    )
    assert r.status_code == 404
    assert (await bob.get(f"{P}/{pid}/conversation")).json() == []
