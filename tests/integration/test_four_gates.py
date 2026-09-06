"""四道门（ADR-037）：每一道的开门 / 通过 / 打回 / 打回后重跑。

阶段图从两道门变成四道：

    routing → plot_index →【plan】→ screenplay →【setup】
            → characters → scenes →【anchors】→ storyboard →【storyboard】→ done

这个文件按门组织，一道门一节。每一节都覆盖同样四件事，因为**这四件事是
一道门的完整生命周期**，缺任何一件都会留下一类只在生产上才发现的故障：

- 开门：门真的挡住了，且摘要里有用户判断得了的东西（不然只能盲点通过）
- 通过：退到正确的下一个阶段（不是原地不动，也不是跳过一步）
- 打回：退回**产出这批内容的那个阶段**（不是退到上一道门，也不是卡死）
- 打回后重跑：退回去之后真的能再跑一遍，并重新开同一道门
  ——这一条最容易漏，`_REDO_FROM` 写错方向时前三条全绿，只有它会红

两道既有门（setup / storyboard）的行为 ADR-037 明写"一个字都不要改"，
所以这里对它们的断言与改动前一致，等于一份回归基线。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import advance_to_gate, pending_approval

pytestmark = pytest.mark.integration

P = "/api/v1/projects"

NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名被调职的刑警。"


async def _project(client: AsyncClient, title: str = "four-gates") -> str:
    return str((await client.post(P, json={"title": title})).json()["id"])


async def _advance(client: AsyncClient, pid: str, text: str = "") -> dict[str, Any]:
    r = await client.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": text})
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _resolve(
    client: AsyncClient, pid: str, decision: str, comment: str = ""
) -> dict[str, Any]:
    pending = await pending_approval(client, pid)
    assert pending is not None, "门没开"
    approval_id = str(pending["id"])
    body: dict[str, Any] = {"decision": decision}
    if comment:
        body["comment"] = comment
    r = await client.post(f"{P}/{pid}/approvals/{approval_id}", json=body)
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _summary(client: AsyncClient, pid: str) -> dict[str, Any]:
    pending = await pending_approval(client, pid)
    assert pending is not None
    return dict(pending["payload_json"]["summary"])


async def _agent_runs(client: AsyncClient, pid: str, agent_id: str) -> list[dict[str, Any]]:
    rows = (await client.get(f"{P}/{pid}/agent-runs")).json()
    return [dict(r) for r in rows if r["agent_id"] == agent_id]


# ---------------------------------------------------------------- 门① 开拍前确认


async def test_plan_gate_opens_before_the_screenplay_is_written(alice: AsyncClient) -> None:
    """门① 必须开在剧本**之前**。

    这是这道门存在的全部理由：改编模式（改编 / 洗稿）决定剧本怎么写。
    等剧本写完再问，用户改一下就要把整份剧本重写一遍，那笔钱已经花了。
    """
    pid = await _project(alice)
    result = await _advance(alice, pid, NOVEL)

    assert result["gate_opened"] == "plan"
    assert result["stage"] == "await_plan"
    assert result["blocked"] is True

    state = (await alice.get(f"{P}/{pid}/state")).json()["current_state_json"]
    assert "plot_index" in state, "情节目录要先跑出来，门① 才有东西可确认"
    assert "screenplay" not in state, "剧本不该在门① 之前就写出来"


async def test_plan_gate_summary_carries_everything_the_user_must_decide(
    alice: AsyncClient,
) -> None:
    """一屏定四件事，四件事的**可选项和当前值**都得在摘要里。

    门① 上用户要回答的是"以上为本次将覆盖的全部情节，有没有遗漏"，
    只给一个节点数答不了；画风只给一个 key 也选不了——能让人判断的
    只有描述词本身。
    """
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)
    summary = await _summary(alice, pid)

    # 1. 情节目录：给全量，不只是条数
    assert summary["nodes_total"] >= 1
    assert len(summary["nodes"]) == summary["nodes_total"]
    assert all("summary" in n for n in summary["nodes"])
    assert summary["logline"]

    # 2. 时代背景与人种：给判定值 + 依据 + 判没判出来
    era = summary["era"]
    assert set(era) >= {"era", "region", "ethnicity", "evidence", "undetermined"}

    # 3. 画风：目录来自库（style_catalog），三套描述词一并给
    options = summary["style"]["options"]
    assert len(options) >= 2, "画风目录是数据，至少要有内置的那几条"
    first = options[0]
    assert first["character_tokens"] and first["scene_tokens"] and first["video_tokens"]

    # 4. 改编模式
    assert set(summary["adaptation"]["options"]) == {"adapt", "rewrite"}

    # 历史项目标记：新建的项目当然不是历史项目
    assert summary["legacy_unconfirmed"] is False


async def test_plan_gate_approval_stamps_the_lock_variables(alice: AsyncClient) -> None:
    """通过门① = 用户亲自确认过这批变量，且阶段推进到剧本。

    "阶段推进了"和"确认盖戳了"必须同时发生：分开提交会出现界面上
    解释不清的中间态（门已过但没记确认，或反过来）。
    """
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)

    assert (await _resolve(alice, pid, "approved"))["stage"] == "screenplay"

    lock = (await alice.get(f"{P}/{pid}/lock-variables")).json()
    assert lock["confirmed_at"] is not None
    assert lock["origin"] == "confirmed"
    assert lock["legacy_unconfirmed"] is False


async def test_plan_gate_rejection_goes_back_to_the_plot_index(alice: AsyncClient) -> None:
    """打回门① 退回情节目录——那是产出这批内容的阶段（ADR-037 第 5 条）。"""
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)

    result = await _resolve(alice, pid, "changes_requested", "漏了第三个情节节点")
    assert result["stage"] == "plot_index"

    lock = (await alice.get(f"{P}/{pid}/lock-variables")).json()
    assert lock["confirmed_at"] is None, "打回不能盖确认戳"


async def test_plan_gate_reruns_and_reopens_after_rejection(alice: AsyncClient) -> None:
    """打回之后要能真的重跑，并重新开同一道门。"""
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)
    await _resolve(alice, pid, "changes_requested", "再拆细一点")

    result = await _advance(alice, pid)
    assert result["gate_opened"] == "plan", "重跑之后要重新开门① 而不是直接放行"
    assert result["stage"] == "await_plan"

    assert len(await _agent_runs(alice, pid, "story.plot_index.v1")) == 2


async def test_plan_rerun_does_not_clobber_a_user_edited_era(alice: AsyncClient) -> None:
    """用户在门① 上改过的人种判定，不能被一次情节目录重跑冲掉。

    用户改它正是因为模型判错了。重跑再写一次 = 把他的修正抹掉，
    而且不报任何错。
    """
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)

    r = await alice.put(
        f"{P}/{pid}/lock-variables",
        json={"era": "民国", "region": "中国", "ethnicity": "东亚面孔"},
    )
    assert r.status_code == 200, r.text

    await _resolve(alice, pid, "approved")
    # 把阶段拨回情节目录再跑一次（等价于后续被打回重做）
    r = await alice.post(f"{P}/{pid}/advance?to_gate=false", json={"user_input": ""})
    assert r.status_code == 200, r.text

    lock = (await alice.get(f"{P}/{pid}/lock-variables")).json()
    assert lock["era"] == "民国"
    assert lock["ethnicity"] == "东亚面孔"


# ---------------------------------------------------------------- 门② 剧本确认


async def test_setup_gate_behaviour_is_unchanged(alice: AsyncClient) -> None:
    """ADR-037 明写既有两道门"行为一个字都不要改"。这是回归基线。"""
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "setup", user_input=NOVEL)

    summary = await _summary(alice, pid)
    assert summary["episodes"] >= 1
    assert summary["scenes"] >= 1
    # 情节覆盖率：改编有没有漏掉原著情节，这是能核对的数字
    assert summary["nodes_total"] >= 1
    assert summary["nodes_covered"] >= 1


async def test_setup_gate_blocks_then_approves_into_characters(alice: AsyncClient) -> None:
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "setup", user_input=NOVEL)

    for _ in range(3):
        assert (await _advance(alice, pid))["stage"] == "await_setup"

    assert (await _resolve(alice, pid, "approved"))["stage"] == "characters"


async def test_setup_gate_rejection_reruns_the_screenplay(alice: AsyncClient) -> None:
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "setup", user_input=NOVEL)

    assert (await _resolve(alice, pid, "changes_requested", "冲突不够强"))["stage"] == "screenplay"

    result = await _advance(alice, pid)
    assert result["gate_opened"] == "setup"
    assert len(await _agent_runs(alice, pid, "story.screenplay.v1")) == 2


# ---------------------------------------------------------------- 门③ 空间锚点


async def test_anchors_gate_opens_after_scenes_before_storyboard(alice: AsyncClient) -> None:
    """门③ 卡在场景档案与分镜之间。

    锚点是"这个空间长什么样"，分镜是"在这个空间里怎么拍"。顺序反过来
    就得先拆完镜再去锁空间，那时每一镜都已经按各自的空间关系写好了。
    """
    pid = await _project(alice)
    result = await advance_to_gate(alice, pid, "anchors", user_input=NOVEL)

    assert result["stage"] == "await_anchors"
    state = (await alice.get(f"{P}/{pid}/state")).json()["current_state_json"]
    assert "scenes" in state
    assert "storyboard" not in state, "分镜不该在锚点确认之前就拆出来"


async def test_anchors_gate_shows_every_scene_at_once(alice: AsyncClient) -> None:
    """一次性展示全部场景（ADR-037 禁止逐个确认）。

    出卡的和不出卡的分开列，但**两边加起来必须是全部场景**：
    只列出卡的那几个，用户没法回答"是不是漏了哪个场景"。
    """
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "anchors", user_input=NOVEL)
    summary = await _summary(alice, pid)

    assert summary["scenes_total"] >= 1
    assert len(summary["cards"]) + len(summary["inline"]) == summary["scenes_total"]
    # 判据是从剧本推的下界，不是分镜实测——摘要要如实说明来源
    assert summary["criteria_source"] == "screenplay"
    for card in summary["cards"]:
        assert card["reasons"], "出卡必须给出理由，不能让用户确认一张没有依据的卡"
        assert set(card["signals"]) == {"beats", "script_scenes", "max_cast", "action_beats"}


async def test_anchors_gate_approval_stamps_and_advances(alice: AsyncClient) -> None:
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "anchors", user_input=NOVEL)

    assert (await _resolve(alice, pid, "approved"))["stage"] == "storyboard"

    lock = (await alice.get(f"{P}/{pid}/lock-variables")).json()
    assert lock["anchors_confirmed_at"] is not None
    # 两道门分开记：门① 早就确认过了，但那是另一个时间戳
    assert lock["confirmed_at"] is not None


async def test_anchors_gate_rejection_goes_back_to_scenes(alice: AsyncClient) -> None:
    """打回门③ 退回场景档案：锚点长在场景档案上，退回分镜没有意义。"""
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "anchors", user_input=NOVEL)

    assert (await _resolve(alice, pid, "changes_requested", "书桌方位没写"))["stage"] == "scenes"

    lock = (await alice.get(f"{P}/{pid}/lock-variables")).json()
    assert lock["anchors_confirmed_at"] is None, "打回不能盖锚点确认戳"


async def test_anchors_gate_reruns_and_reopens_after_rejection(alice: AsyncClient) -> None:
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "anchors", user_input=NOVEL)
    await _resolve(alice, pid, "changes_requested", "固定参照物太少")

    result = await _advance(alice, pid)
    assert result["gate_opened"] == "anchors"
    assert result["stage"] == "await_anchors"

    assert len(await _agent_runs(alice, pid, "visual.scene.v1")) == 2


# ---------------------------------------------------------------- 门④ 分镜确认


async def test_storyboard_gate_behaviour_is_unchanged(alice: AsyncClient) -> None:
    """既有门的回归基线：摘要里的那几个数字一个都不能少。"""
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "storyboard", user_input=NOVEL)

    summary = await _summary(alice, pid)
    assert summary["characters"] >= 1
    assert summary["scenes"] >= 1
    assert summary["nodes"] >= 1
    assert summary["shots"] >= 1


async def test_storyboard_gate_reports_anchor_gaps_found_by_the_real_criteria(
    alice: AsyncClient,
) -> None:
    """门④ 顺带回答"哪些场景比门③ 上估的更吃重"。

    门③ 的判据是从剧本推的**下界**；分镜跑完才第一次知道真实镜号数与
    同框人数。空列表是常态也是预期——有值时才说明剧本的节拍粒度太粗。
    这里只断言这个字段存在且形状对：它是门④ 摘要的一部分，前端要拿它
    渲染，缺了就是静默少一块。
    """
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "storyboard", user_input=NOVEL)

    summary = await _summary(alice, pid)
    assert isinstance(summary["anchor_gaps"], list)
    for gap in summary["anchor_gaps"]:
        assert set(gap) == {"ref", "name", "reasons"}
        assert gap["reasons"]


async def test_storyboard_gate_approval_finishes_the_run(alice: AsyncClient) -> None:
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "storyboard", user_input=NOVEL)

    assert (await _resolve(alice, pid, "approved"))["stage"] == "done"


async def test_storyboard_gate_rejection_reruns_the_storyboard(alice: AsyncClient) -> None:
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "storyboard", user_input=NOVEL)

    result = await _resolve(alice, pid, "changes_requested", "第 3 镜没有运镜")
    assert result["stage"] == "storyboard"

    reopened = await _advance(alice, pid)
    assert reopened["gate_opened"] == "storyboard"
    assert len(await _agent_runs(alice, pid, "visual.storyboard.v1")) == 2


# ---------------------------------------------------------------- 跨门的不变量


async def test_every_gate_blocks_and_none_can_be_skipped(alice: AsyncClient) -> None:
    """四道门必须一道不落地按顺序开。

    这条是对 `_NEXT` / `_GATE_OF` 两张表的联合断言：任何一处漏配都会让
    某道门不开，而不开的门在界面上表现为"这一步系统自己决定了"。
    """
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)

    seen: list[str] = []
    for _ in range(8):
        pending = await pending_approval(alice, pid)
        if pending is None:
            break
        seen.append(str(pending["gate"]))
        await _resolve(alice, pid, "approved")
        await _advance(alice, pid)

    assert seen == ["plan", "setup", "anchors", "storyboard"]
    assert (await alice.get(f"{P}/{pid}/state")).json()["stage"] == "done"


async def test_gates_are_org_scoped(alice: AsyncClient, bob: AsyncClient) -> None:
    """别的租户看不到这些门，也决议不了它们。跨租户一律 404，不返 403。"""
    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)
    pending = await pending_approval(alice, pid)
    assert pending is not None
    approval_id = str(pending["id"])

    assert (await bob.get(f"{P}/{pid}/approvals")).json() == []
    r = await bob.post(f"{P}/{pid}/approvals/{approval_id}", json={"decision": "approved"})
    assert r.status_code == 404
    assert (await bob.get(f"{P}/{pid}/lock-variables")).status_code == 404


async def test_lock_variables_survive_a_rejection_and_rerun(alice: AsyncClient) -> None:
    """锁定变量不放在 `current_state_json` 里，所以重跑冲不掉它。

    这正是它单独一张表的理由：`current_state_json` 会被打回、重跑、
    字段级编辑整份换掉，而"这个项目是什么"必须在所有重跑之间存活。
    """
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "setup", user_input=NOVEL)

    before = (await alice.get(f"{P}/{pid}/lock-variables")).json()
    assert before["confirmed_at"] is not None

    await _resolve(alice, pid, "changes_requested", "重写")
    await _advance(alice, pid)

    after = (await alice.get(f"{P}/{pid}/lock-variables")).json()
    assert after["confirmed_at"] == before["confirmed_at"]
    assert after["style_key"] == before["style_key"]
    assert after["adaptation_mode"] == before["adaptation_mode"]


async def test_adaptation_mode_reaches_the_screenplay_prompt(
    alice: AsyncClient, db: AsyncSession
) -> None:
    """洗稿模式必须真的进到剧本提示词里。

    这是门① 上唯一一个**改变下游行为**的选择。存下来却不注入提示词，
    用户选了洗稿仍然得到改编，而且无处可查。
    """
    from sqlalchemy import select

    from apps.api.modules.agent.models import AgentRun, AgentStep

    pid = await _project(alice)
    await _advance(alice, pid, NOVEL)

    r = await alice.put(f"{P}/{pid}/lock-variables", json={"adaptation_mode": "rewrite"})
    assert r.status_code == 200, r.text
    assert r.json()["adaptation_mode"] == "rewrite"

    await _resolve(alice, pid, "approved")
    await _advance(alice, pid)

    # 断言的是**送进模型的那段全文**（`agent_steps.resolved_prompt`），
    # 不是 spec 里的模板：模板里只有 `{adaptation_instruction}` 这个占位符，
    # 断言模板等于断言"占位符还在"，占位符没被替换时照样绿。
    run_id = (
        await db.execute(
            select(AgentRun.id)
            .where(
                AgentRun.project_id == uuid.UUID(pid),
                AgentRun.agent_id == "story.screenplay.v1",
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one()
    prompt = (
        await db.execute(
            select(AgentStep.resolved_prompt)
            .where(AgentStep.run_id == run_id, AgentStep.resolved_prompt.is_not(None))
            .order_by(AgentStep.step_index)
            .limit(1)
        )
    ).scalar_one()
    assert "洗稿" in prompt
    assert "换成原创" in prompt
