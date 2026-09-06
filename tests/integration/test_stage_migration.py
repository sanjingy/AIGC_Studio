"""存量项目在四道门上线之后仍然走得动（ADR-037 第 6 条）。

—— 为什么这个文件必须存在 ——

`Stage` 存在 `projects.current_state_json` 里（ADR-008），所以改阶段图就是
一次数据迁移。这个仓库踩过一次：2026-08-18 阶段图由 story/visual 两步改成
五步，存量项目带着旧阶段名撞进 `_NEXT[stage]`，KeyError 变 500，用户看到的
是"点继续彻底没反应"。教训写在 `orchestrator._LEGACY_STAGES` 的注释里。

ADR-037 这一次**没有作废任何阶段名**：`await_plan` / `await_anchors` 是插在
既有阶段之间的两个新值，存量项目的 stage 一个都没变。所以这次要防的不是
KeyError，而是另外两件事：

1. 每一种可能存在库里的 stage 值都还能 `advance` —— 这条用**穷举**来保证，
   不是挑几个有代表性的值试试。判据从 `orchestrator.Stage` 的字面量集合 +
   `_LEGACY_STAGES` 的键自动枚举，将来再加阶段值时这里会自然覆盖到。
2. 已经越过门① 位置的项目**不得被退回去重新确认**，但它们的锁定变量必须
   能被区分出"从来没有人看过一眼"（`origin='migrated'`），否则界面会把
   缺省画风显示成用户自己选的。
"""

from __future__ import annotations

import typing
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.agent import orchestrator

pytestmark = pytest.mark.integration

P = "/api/v1/projects"

NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名被调职的刑警。"

# 库里可能存着的全部 stage 值 = 现行枚举 + 已翻译的旧名。
# 从类型和表里枚举，不手写清单：手写的清单加阶段时不会自己变长。
CURRENT_STAGES: tuple[str, ...] = typing.get_args(orchestrator.Stage)
LEGACY_STAGES: tuple[str, ...] = tuple(orchestrator._LEGACY_STAGES)
ALL_STORED_STAGES: tuple[str, ...] = CURRENT_STAGES + LEGACY_STAGES

# 一份"什么产出都有"的状态。把 stage 换成任意值就能模拟一个停在那一步的
# 存量项目，而不必真的跑一遍流水线（跑一遍要十几秒，穷举十来个阶段就是几分钟）。
FULL_OUTPUTS: dict[str, Any] = {
    "router": {"route": "NOVEL_TO_ANIME", "estimated_shots": 12},
    "plot_index": {
        "genre": "悬疑",
        "logline": "被调职的刑警接受观察力考验",
        "nodes": [{"index": 1, "summary": "初到新单位报到"}],
    },
    "screenplay": {
        "title": "资料馆的第一天",
        "episodes": [
            {
                "index": 1,
                "title": "第一集",
                "scenes": [
                    {
                        "id": "1-1",
                        "location": "资料馆门口",
                        "character_refs": ["zhu_jue"],
                        "beats": [{"kind": "action", "text": "主角推开铁门"}],
                    }
                ],
            }
        ],
    },
    "characters": {"characters": [{"ref": "zhu_jue", "name": "主角"}]},
    "scenes": {
        "scenes": [
            {
                "ref": "gate",
                "name": "资料馆门口",
                "camera_axis": {"position": "铁门外路面", "facing": "朝向建筑正面"},
                "fixed_references": ["铁门在画面正前方"],
            }
        ]
    },
    "storyboard": {
        "nodes": [{"index": 1, "scene_ref": "gate", "summary": "门口报到"}],
        "shots": [{"index": 1, "node_index": 1, "scene_ref": "gate", "shot_size": "全景"}],
    },
}


async def _project(client: AsyncClient, title: str) -> str:
    return str((await client.post(P, json={"title": title})).json()["id"])


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


async def _force_stage(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    pid: str,
    stage: str,
    outputs: dict[str, Any] | None = None,
) -> None:
    """直接把库里的状态改成"停在 stage 上的存量项目"。

    绕过编排器写状态是有意的：这里要造的正是**编排器今天写不出来的**
    那种状态——旧版本留下的。走接口造不出来，也就测不到。
    """
    from apps.api.modules.project import service as project_service

    project = await project_service.get_project(db, org_id=org_id, project_id=uuid.UUID(pid))
    project.current_state_json = {
        **(outputs if outputs is not None else FULL_OUTPUTS),
        "stage": stage,
    }
    await db.commit()


# ------------------------------------------------------------------ 穷举每一种存量阶段


@pytest.mark.parametrize("stage", ALL_STORED_STAGES)
async def test_every_stored_stage_can_still_advance(
    alice: AsyncClient, db: AsyncSession, stage: str
) -> None:
    """库里存着任何一个历史 stage 值，`advance` 都不能 500。

    `done` 是唯一一个"推不动"的合法终点，它返回 200 + 不做任何事，
    而不是报错——用户在完成的项目上再点一次继续不该看到错误。
    """
    org_id = await _org(alice)
    pid = await _project(alice, f"legacy-{stage}")
    await _force_stage(db, org_id=org_id, pid=pid, stage=stage)

    r = await alice.post(f"{P}/{pid}/advance?to_gate=false", json={"user_input": ""})
    assert r.status_code == 200, f"stage={stage} 推不动：{r.text}"

    # 推完之后的 stage 必须仍然是现行枚举里的值，不能留下一个谁也不认识的字符串
    after = (await alice.get(f"{P}/{pid}/state")).json()["stage"]
    assert after in CURRENT_STAGES, f"stage={stage} 推成了未知阶段 {after}"


@pytest.mark.parametrize("stage", ALL_STORED_STAGES)
async def test_every_stored_stage_reads_back_as_a_known_stage(
    alice: AsyncClient, db: AsyncSession, stage: str
) -> None:
    """状态查询接口也要认得旧阶段名。

    `GET /state` 走的是 `agent.service.current_stage`（含旧名翻译），
    不是把 JSONB 里那个字符串直接抛出去。少了这层翻译，前端拿到 `story`
    这种值会在阶段条上匹配不到任何一格，表现为"进度条空了"。
    """
    org_id = await _org(alice)
    pid = await _project(alice, f"legacy-read-{stage}")
    await _force_stage(db, org_id=org_id, pid=pid, stage=stage)

    body = (await alice.get(f"{P}/{pid}/state")).json()
    assert body["stage"] in CURRENT_STAGES, f"{stage} 读回来是 {body['stage']}"


async def test_new_gates_are_not_in_the_legacy_translation_table() -> None:
    """这一次**不需要**给 `_LEGACY_STAGES` 加东西，把这件事钉下来。

    加了反而是错的：`await_plan` / `await_anchors` 是新值，库里不可能存过
    它们，往翻译表里加只会让人以为曾经有过这两个旧名。
    """
    assert "await_plan" not in orchestrator._LEGACY_STAGES
    assert "await_anchors" not in orchestrator._LEGACY_STAGES
    # 反过来，旧名翻译出来的目标必须仍然是现行枚举里的值
    assert set(orchestrator._LEGACY_STAGES.values()) <= set(CURRENT_STAGES)


# ------------------------------------------------------------------ 越过门① 的存量项目


async def test_a_project_past_the_plan_gate_is_never_sent_back(
    alice: AsyncClient, db: AsyncSession
) -> None:
    """已经越过门① 位置的存量项目，不得被退回去重新确认（ADR-037 第 6 条）。

    退回去等于让一批跑到一半的项目全部回到起点，而它们的剧本、角色、
    场景都已经按旧规则产出了，重跑一遍要真花钱。
    """
    org_id = await _org(alice)
    pid = await _project(alice, "legacy-past-gate")
    # 停在角色阶段：门① 的位置在它上游
    await _force_stage(db, org_id=org_id, pid=pid, stage="characters")

    r = await alice.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": ""})
    assert r.status_code == 200, r.text
    # 下一个停下来的门是锚点门（门③），不是被拽回门①
    assert r.json()["gate_opened"] == "anchors"


async def test_legacy_project_lock_row_is_labelled_migrated(
    alice: AsyncClient, db: AsyncSession
) -> None:
    """存量项目补出来的锁定变量必须标成 `migrated`，不能伪装成 detected。

    这条是"必须能区分用户确认过的和迁移补的"（WL 任务书第 2 节）的落点：
    标错了，界面会把一个缺省画风显示成用户自己选的，而他从来没被问过。
    """
    org_id = await _org(alice)
    pid = await _project(alice, "legacy-lock-origin")
    await _force_stage(db, org_id=org_id, pid=pid, stage="scenes")

    # 这个项目从来没开过门①，所以现在一行锁定变量都没有
    before = (await alice.get(f"{P}/{pid}/lock-variables")).json()
    assert before["origin"] == "detected"
    assert before["confirmed_at"] is None

    # 一路推到锚点门并通过它 —— 这是它第一次真正需要一行锁定变量
    r = await alice.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": ""})
    assert r.json()["gate_opened"] == "anchors", r.text
    rows = (await alice.get(f"{P}/{pid}/approvals")).json()
    pending = next(x for x in rows if x["status"] == "pending")
    approval_id = str(pending["id"])
    r = await alice.post(f"{P}/{pid}/approvals/{approval_id}", json={"decision": "approved"})
    assert r.status_code == 200, r.text

    after = (await alice.get(f"{P}/{pid}/lock-variables")).json()
    assert after["origin"] == "migrated", "从没过过门① 的项目不能标成 detected"
    assert after["confirmed_at"] is None, "门③ 不代表门① 也确认过"
    assert after["anchors_confirmed_at"] is not None
    assert after["legacy_unconfirmed"] is True, "界面要能如实标注'历史项目，未经确认'"


async def test_migrated_project_falls_back_to_evidence_based_era(
    alice: AsyncClient, db: AsyncSession
) -> None:
    """迁移补的行把时代背景留空，运行时要退回按证据推，而不是拿空串去出图。

    留空是有意的（ADR-037 第 2 条禁止默认套用本国），但留空不能变成
    "提示词里的时代背景是一个空字符串"——那等于把这一项从提示词里删掉。
    """
    org_id = await _org(alice)
    pid = await _project(alice, "legacy-era-fallback")
    await _force_stage(db, org_id=org_id, pid=pid, stage="characters")

    from apps.api.modules.project import service as project_service

    lock = await project_service.ensure_lock_variables(
        db, org_id=org_id, project_id=uuid.UUID(pid), origin="migrated"
    )
    await db.commit()
    assert lock.era == ""

    variables = orchestrator.variables_for("characters", FULL_OUTPUTS, lock)
    assert variables["era"], "时代背景不能是空串"
    assert variables["default_ethnicity"], "人种基准不能是空串"
    assert "未判定" in variables["era"] or variables["era"] == "现代"
    # 兜底文案必须是**要求判定的指令**，不是一个具体人种
    assert "不得默认套用" in variables["default_ethnicity"]


async def test_lock_variables_are_org_scoped(alice: AsyncClient, bob: AsyncClient) -> None:
    """锁定变量里有画风与时代背景，跨租户读到就是把别人的创作设定拼进自己的提示词。"""
    pid = await _project(alice, "lock-isolation")
    assert (await bob.get(f"{P}/{pid}/lock-variables")).status_code == 404
    r = await bob.put(f"{P}/{pid}/lock-variables", json={"adaptation_mode": "rewrite"})
    assert r.status_code == 404, "跨租户返 404 不返 403"
