"""从产品界面出图：角色基准立绘 + 场景基准参考图 + 单个分镜。

这条链路在此之前是断的。`worker/jobs/generation.py` 能出图，
`consistency/compose.py` 能合成提示词，但正常项目流程从来没在
`style_profiles` / `character_profiles` 里写过一行——只有
`scripts/validation_slice.py` 手工建过。所以"用户在浏览器里走一遍到
分镜出图"这条 M1 验收标准从来没真正成立过。

**这里的用例一律不让出图任务真的跑起来**：`_no_enqueue` 把入队掐掉。
Worker 是独立容器、跑在 `ENV=local` 下，它不认测试环境的 Mock 开关，
一旦捞到 `image.generate` 就会拿平台的真 Key 去打万相——每跑一遍
测试套件都在花钱。这个仓库已经因为"测试打真实上游"栽过一次，
不能再栽第二次。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from apps.api.core.db import session_scope
from apps.api.modules.agent import orchestrator
from apps.api.modules.consistency import service as consistency
from apps.api.modules.consistency.models import SceneProfile, ShotConditioning
from apps.api.modules.project import service as project_service
from apps.api.modules.task import service as task_service
from apps.api.modules.task.models import Task
from tests.conftest import advance_to_gate

pytestmark = pytest.mark.integration

P = "/api/v1/projects"

NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名被调职的刑警。"


@pytest.fixture(autouse=True)
def _no_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """掐掉入队。

    任务照建、照预扣、照落库，只是没有人去执行它——这正是这些用例
    要断言的范围。真实出图由人工验收，不由测试花钱验证。
    """

    async def _noop(task_id: uuid.UUID) -> None:
        del task_id

    monkeypatch.setattr(task_service, "_enqueue", _noop)


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


async def _project(client: AsyncClient, title: str = "image-gen") -> str:
    return str((await client.post(P, json={"title": title})).json()["id"])


async def _approve_pending(client: AsyncClient, pid: str) -> None:
    rows = (await client.get(f"{P}/{pid}/approvals")).json()
    pending = next(r for r in rows if r["status"] == "pending")
    r = await client.post(f"{P}/{pid}/approvals/{pending['id']}", json={"decision": "approved"})
    assert r.status_code == 200, r.text


async def _run_to_storyboard(client: AsyncClient) -> str:
    """跑到分镜门：角色档案、场景档案、分镜表都已产出。

    途中的开拍前确认门、剧本门、空间锚点门由 `advance_to_gate` 一并通过；
    这里不写死门的道数，加一道门不该让十来个出图用例一起变红。
    """
    pid = await _project(client)
    await advance_to_gate(client, pid, "storyboard", user_input=NOVEL)
    return pid


async def _task_row(task_id: str) -> Task:
    async with session_scope() as db:
        row = (await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))).scalar_one()
        return row


async def _balance(client: AsyncClient) -> dict[str, int]:
    return dict((await client.get("/api/v1/credits/balance")).json())


# ---------------------------------------------------------------- 编排器接线


async def test_characters_stage_lands_consistency_profiles(alice: AsyncClient) -> None:
    """跑完角色阶段，一致性引擎里必须真的有档案。

    这是整条出图链路的地基：没有风格档案就没有系统注入的风格词，
    没有角色档案就没有可拼装的外貌字段。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)

    async with session_scope() as db:
        style = await consistency.get_style(db, org_id=org_id, project_id=uuid.UUID(pid))
        characters = await consistency.list_characters(db, org_id=org_id, project_id=uuid.UUID(pid))

    assert style is not None, "角色阶段跑完了却没有风格档案"
    # 三套各注入各的（ADR-036 第 3 条），三套都必须有内容——
    # 少一套就意味着那一类产物会拿到空风格词，画风漂移且无人察觉。
    assert style.character_tokens, "风格档案必须带人物版风格词"
    assert style.scene_tokens, "风格档案必须带场景版风格词"
    assert style.video_tokens, "风格档案必须带视频版风格词"
    assert style.style_key, "必须记下当初从目录里选的是哪一条画风"
    refs = {c.ref for c in characters}
    assert {"zhu_jue", "guan_zhang"} <= refs, f"角色档案没落库：{refs}"

    # 外貌必须是结构化字段，不是一段自由文本——提示词要按字段拼装
    zhu = next(c for c in characters if c.ref == "zhu_jue")
    assert zhu.appearance_json["hair"]
    assert zhu.appearance_json["outfit"]


async def test_rerun_does_not_overwrite_frozen_profiles(alice: AsyncClient) -> None:
    """已冻结的风格和角色，重跑一次角色阶段也不能被改掉。

    风格一旦有镜头产出即冻结。静默改掉会让前后镜头画风不一致，
    而且没有任何地方会报错——只有用户自己看出来"这两镜不像一部片"。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)
    project_id = uuid.UUID(pid)

    sentinel = "已冻结的风格词"
    async with session_scope() as db:
        style = await consistency.get_style(db, org_id=org_id, project_id=project_id)
        assert style is not None
        style.character_tokens = sentinel
        await consistency.lock_style(db, style)

        characters = await consistency.list_characters(db, org_id=org_id, project_id=project_id)
        zhu = next(c for c in characters if c.ref == "zhu_jue")
        zhu.appearance_json = {**zhu.appearance_json, "hair": "冻结后的发型"}
        zhu.locked_at = style.locked_at
        style_id, char_id = style.id, zhu.id
        await db.commit()

    # 把阶段拨回角色档案，再跑一次（等价于用户回头改角色）
    async with session_scope() as db:
        project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
        project.current_state_json = {**dict(project.current_state_json), "stage": "characters"}
        await db.commit()

    async with session_scope() as db:
        result = await orchestrator.advance(db, org_id=org_id, project_id=project_id)
    assert result.ran_role == "characters"

    async with session_scope() as db:
        style = await consistency.get_style(db, org_id=org_id, project_id=project_id)
        characters = await consistency.list_characters(db, org_id=org_id, project_id=project_id)

    assert style is not None
    assert style.id == style_id, "不能因为重跑就新建一份风格档案"
    assert style.character_tokens == sentinel, "冻结的风格被覆盖了"

    zhu = next(c for c in characters if c.ref == "zhu_jue")
    assert zhu.id == char_id
    assert zhu.appearance_json["hair"] == "冻结后的发型", "冻结的角色被覆盖了"


# ---------------------------------------------------------------- 角色基准立绘


async def test_portrait_task_carries_composed_prompt(alice: AsyncClient) -> None:
    """立绘的提示词必须由系统合成，且带上风格词。"""
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)

    before = await _balance(alice)
    r = await alice.post(f"{P}/{pid}/images/characters/zhu_jue")
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["type"] == "image.generate"
    assert created["status"] == "queued"
    assert created["estimated_cost"] > 0

    row = await _task_row(created["id"])
    payload: dict[str, Any] = dict(row.input_json)

    async with session_scope() as db:
        style = await consistency.get_style(db, org_id=org_id, project_id=uuid.UUID(pid))
        assert style is not None
        positive, negative, seed_base = (
            style.character_tokens,
            style.negative_tokens,
            style.seed_base,
        )
        scene_only = style.scene_tokens

    assert "主角" in payload["prompt"], "提示词里必须有角色的结构化外貌"
    assert "黑色短发，额前碎发" in payload["prompt"]
    assert positive in payload["prompt"], "人物版风格词必须由系统注入"
    assert scene_only not in payload["prompt"], "基准立绘不该带场景版风格词"
    assert "纯色背景" in payload["prompt"], "基准立绘必须是中性构图"
    assert payload["negative_prompt"] == negative
    assert payload["seed"] == seed_base
    assert payload["project_id"] == pid
    assert payload["subject_kind"] == "character"
    assert payload["subject_ref"] == "zhu_jue"

    # 计费：走的是普通任务那条路，预扣必须已经记上
    mid = await _balance(alice)
    assert mid["reserved"] == before["reserved"] + created["estimated_cost"]


async def test_unknown_character_is_404(alice: AsyncClient) -> None:
    pid = await _run_to_storyboard(alice)
    r = await alice.post(f"{P}/{pid}/images/characters/nobody")
    assert r.status_code == 404


async def test_portrait_before_characters_stage_explains_itself(alice: AsyncClient) -> None:
    """还没跑到角色阶段就要出图，要给一个能看懂的前置条件错误。

    这里返 500 或者放行到 Worker 再炸，用户看到的都是"生成失败"，
    但他真正需要知道的是"回去把角色那一步跑完"。
    """
    pid = await _project(alice, "empty")
    r = await alice.post(f"{P}/{pid}/images/characters/zhu_jue")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "consistency.profile.missing"
    assert r.json()["error"]["user_message"]


# ---------------------------------------------------------------- 场景基准参考图


async def test_scenes_stage_lands_scene_profiles(alice: AsyncClient) -> None:
    """跑完场景阶段，一致性引擎里必须真的有场景档案。

    摄影主轴和固定参照物是场景一致性的全部依据。它们在
    `agents/schemas.py` 的 `SceneSheet` 里本来就有，但在这之前从来没有
    任何代码把它们写进库——场景出图拿不到锚点，等于没有一致性。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)

    async with session_scope() as db:
        scenes = await consistency.list_scenes(db, org_id=org_id, project_id=uuid.UUID(pid))

    refs = {s.ref for s in scenes}
    assert {"gate", "office"} <= refs, f"场景档案没落库：{refs}"

    gate = next(s for s in scenes if s.ref == "gate")
    axis = gate.spatial_json["camera_axis"]
    assert axis["position"] and axis["facing"] and axis["far_end"], "摄影主轴必须逐字段存下来"
    anchors = gate.spatial_json["fixed_references"]
    assert anchors, "固定参照物必须存下来"
    # 两段式：名称给人和给引用，描述给出图模型。落库时少哪一半都不成立。
    assert all(a["name"] and a["description"] for a in anchors), f"锚点不是两段式：{anchors}"
    # 光照是一组具名状态，且默认状态必须指得到实处——否则漏填 lighting_ref
    # 的镜头行为未定义。
    states = gate.spatial_json["lighting_states"]
    assert states, "光照状态必须存下来"
    assert gate.spatial_json["default_lighting"] in {s["name"] for s in states}
    assert gate.spatial_json["setting"]


async def test_scene_rerun_does_not_overwrite_frozen_profile(alice: AsyncClient) -> None:
    """已冻结的场景，重跑场景阶段也不能被改掉。

    同一场景的后续镜头都以它的摄影主轴为基准，中途改掉等于让同一个
    房间在前后两镜里变成两个房间。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)
    project_id = uuid.UUID(pid)

    async with session_scope() as db:
        scenes = await consistency.list_scenes(db, org_id=org_id, project_id=project_id)
        gate = next(s for s in scenes if s.ref == "gate")
        gate.spatial_json = {
            **gate.spatial_json,
            "lighting_states": [
                {"name": "冻结后", "description": "冻结后的光影", "origin": "authored"}
            ],
            "default_lighting": "冻结后",
        }
        gate.locked_at = datetime.now(UTC)
        scene_id = gate.id
        await db.commit()

    async with session_scope() as db:
        project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
        project.current_state_json = {**dict(project.current_state_json), "stage": "scenes"}
        await db.commit()

    async with session_scope() as db:
        result = await orchestrator.advance(db, org_id=org_id, project_id=project_id)
    assert result.ran_role == "scenes"

    async with session_scope() as db:
        scenes = await consistency.list_scenes(db, org_id=org_id, project_id=project_id)

    gate = next(s for s in scenes if s.ref == "gate")
    assert gate.id == scene_id, "不能因为重跑就新建一份场景档案"
    states = gate.spatial_json["lighting_states"]
    assert [s["description"] for s in states] == ["冻结后的光影"], "冻结的场景被覆盖了"


async def test_scene_reference_task_carries_spatial_anchors(alice: AsyncClient) -> None:
    """场景参考图的提示词必须由系统合成，且带上摄影主轴与固定参照物。"""
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)

    r = await alice.post(f"{P}/{pid}/images/scenes/gate")
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["type"] == "image.generate"
    assert created["estimated_cost"] > 0

    row = await _task_row(created["id"])
    payload: dict[str, Any] = dict(row.input_json)

    async with session_scope() as db:
        style = await consistency.get_style(db, org_id=org_id, project_id=uuid.UUID(pid))
        assert style is not None
        positive, negative, seed_base = (
            style.scene_tokens,
            style.negative_tokens,
            style.seed_base,
        )
        character_only = style.character_tokens

    # 锚点：这两样不进提示词，场景出图就只是"看着像"而没有一致性
    assert "铁门外的路面" in payload["prompt"], "摄影主轴的站位没进提示词"
    assert "朝向建筑正面" in payload["prompt"]
    assert "锈迹铁门" in payload["prompt"], "固定参照物的名称没进提示词"
    assert "右扇下缘锈穿" in payload["prompt"], "固定参照物的描述没进提示词"
    assert "摄影主轴" in payload["prompt"], "主轴必须是构图指令，不只是描述"
    assert positive in payload["prompt"], "场景版风格词必须由系统注入"
    assert character_only not in payload["prompt"], "场景参考图不该带人物质感词"
    assert payload["negative_prompt"] == negative
    assert payload["seed"] == seed_base
    assert payload["subject_kind"] == "scene"
    assert payload["subject_ref"] == "gate"


async def test_unknown_scene_is_404(alice: AsyncClient) -> None:
    pid = await _run_to_storyboard(alice)
    r = await alice.post(f"{P}/{pid}/images/scenes/nowhere")
    assert r.status_code == 404


async def test_scene_reference_before_scenes_stage_explains_itself(alice: AsyncClient) -> None:
    pid = await _project(alice, "empty-scenes")
    r = await alice.post(f"{P}/{pid}/images/scenes/gate")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "consistency.profile.missing"
    assert r.json()["error"]["detail"]["missing_stage"] == "scenes"


# ---------------------------------------------------------------- 分镜出图


async def test_shot_task_composes_from_storyboard_and_records_conditioning(
    alice: AsyncClient,
) -> None:
    """分镜出图要用分镜表的画面内容 + 出场人物的角色档案 + 系统风格词。"""
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)

    r = await alice.post(f"{P}/{pid}/images/shots/1")
    assert r.status_code == 201, r.text
    created = r.json()

    row = await _task_row(created["id"])
    payload: dict[str, Any] = dict(row.input_json)
    assert "第 1 镜的画面内容" in payload["prompt"]
    assert "主角" in payload["prompt"], "出场人物的外貌必须拼进提示词"
    assert payload["subject_kind"] == "shot"
    assert payload["shot_index"] == 1

    async with session_scope() as db:
        style = await consistency.get_style(db, org_id=org_id, project_id=uuid.UUID(pid))
        assert style is not None
        # 这一镜有角色出场，注入的应是人物版而不是场景版
        assert style.character_tokens in payload["prompt"]
        # 项目级基准 seed + 镜号偏移：同一镜重跑得到同一张图
        assert payload["seed"] == style.seed_base + 1

        conditioning = list(
            (
                await db.execute(
                    select(ShotConditioning).where(
                        ShotConditioning.project_id == uuid.UUID(pid),
                        ShotConditioning.shot_index == 1,
                    )
                )
            ).scalars()
        )

    assert len(conditioning) == 1, "这一镜拿什么去生成的必须落库，否则崩了查不清"
    record = conditioning[0]
    assert record.resolved_prompt == payload["prompt"], "存的必须是全文，不是摘要"
    assert record.negative_prompt == payload["negative_prompt"]
    assert record.style_profile_id == style.id
    assert record.character_profile_ids, "出场人物用了哪一版角色档案要记下来"
    # 预扣按 attempt+1 记账，条件化记录要对得上，否则事后对不回是哪一次
    assert record.attempt == 1


async def test_shot_prompt_carries_its_scene(alice: AsyncClient) -> None:
    """镜头提示词里必须说清楚这一镜发生在哪个场景。

    在这之前 `compose_shot` 只有"角色 + 画面 + 风格"——同一个房间的
    两镜之间没有任何共同的空间锚点，书桌这镜在左边下镜在右边。
    """
    pid = await _run_to_storyboard(alice)

    r = await alice.post(f"{P}/{pid}/images/shots/1")
    assert r.status_code == 201, r.text
    created = r.json()

    row = await _task_row(created["id"])
    payload: dict[str, Any] = dict(row.input_json)

    # mock 分镜表第 1 镜的 scene_ref 是 gate
    assert "铁门外的路面" in payload["prompt"], "摄影主轴没进镜头提示词"
    assert "锈迹铁门" in payload["prompt"], "固定参照物的名称没进镜头提示词"
    assert "右扇下缘锈穿" in payload["prompt"], "固定参照物的描述没进镜头提示词"
    # 顺序：角色 → 场景 → 画面 → 风格
    assert payload["prompt"].index("主角") < payload["prompt"].index("铁门外的路面")
    assert payload["prompt"].index("铁门外的路面") < payload["prompt"].index("第 1 镜的画面内容")

    async with session_scope() as db:
        record = (
            await db.execute(
                select(ShotConditioning).where(
                    ShotConditioning.project_id == uuid.UUID(pid),
                    ShotConditioning.shot_index == 1,
                )
            )
        ).scalar_one()
        scene = (
            await db.execute(
                select(SceneProfile).where(
                    SceneProfile.project_id == uuid.UUID(pid), SceneProfile.ref == "gate"
                )
            )
        ).scalar_one()
    assert record.scene_profile_id == scene.id, "这一镜用了哪个场景档案要记下来"


async def test_shot_prompt_carries_only_the_referenced_lighting_state(
    alice: AsyncClient,
) -> None:
    """这一镜引用了哪个光照状态，就只注入那一个的描述。

    这条是整个多光照状态改动的价值所在。把场景声明的全部状态一起塞进
    提示词，模型看到的是互相矛盾的指令（"上午"和"傍晚"同时成立），
    结果只会是它自己挑一个——那和改动之前"每镜自由发挥"没有区别。

    Mock 的 gate 场景声明了「上午」「傍晚」两个状态，第 1 镜引用「上午」。
    """
    pid = await _run_to_storyboard(alice)

    r = await alice.post(f"{P}/{pid}/images/shots/1")
    assert r.status_code == 201, r.text
    prompt = dict((await _task_row(r.json()["id"])).input_json)["prompt"]

    assert "均匀自然日光" in prompt, "被引用的光照状态没进提示词"
    assert "低角度侧光" not in prompt, "没被引用的那个状态漏进提示词了"


async def test_shot_referencing_an_unknown_lighting_state_falls_back(
    alice: AsyncClient,
) -> None:
    """分镜引用了这个场景没声明过的状态 → 用默认状态出图，不报错。

    降级而不是报错的理由见 `compose.resolve_lighting`：这条引用跨两份
    Agent 产出，schema 层判不了；而为一镜写错名字让整份分镜表失败，
    代价完全不成比例。降级之后的行为**正是这个字段存在之前的行为**。

    这里改的是**项目状态里的分镜表**，不是场景档案——模拟的正是模型
    自己编了一个状态名的情况。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)
    project_id = uuid.UUID(pid)

    async with session_scope() as db:
        project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
        state = dict(project.current_state_json)
        board = dict(state["storyboard"])
        shots = [dict(sh) for sh in board["shots"]]
        shots[0]["lighting_ref"] = "这个场景根本没声明过的状态"
        project.current_state_json = {**state, "storyboard": {**board, "shots": shots}}
        await db.commit()

    r = await alice.post(f"{P}/{pid}/images/shots/1")
    assert r.status_code == 201, "引用错一个名字不该让整镜出不了图"
    prompt = dict((await _task_row(r.json()["id"])).input_json)["prompt"]
    # gate 的默认状态是「上午」
    assert "均匀自然日光" in prompt, "没落到默认光照状态上"


async def test_shot_in_a_single_state_scene_uses_that_state(alice: AsyncClient) -> None:
    """只声明一个光照状态的场景，镜头怎么引用都得到那一个。

    室内不分时的场景就是这种。Mock 的 office 只有「常态」一个状态，
    分镜后半段的镜头都落在它上面。
    """
    pid = await _run_to_storyboard(alice)

    async with session_scope() as db:
        project = await project_service.get_project(
            db, org_id=await _org(alice), project_id=uuid.UUID(pid)
        )
        shots = project.current_state_json["storyboard"]["shots"]
    index = next(sh["index"] for sh in shots if sh["scene_ref"] == "office")

    r = await alice.post(f"{P}/{pid}/images/shots/{index}")
    assert r.status_code == 201, r.text
    prompt = dict((await _task_row(r.json()["id"])).input_json)["prompt"]
    assert "顶部吊灯与桌面台灯为主" in prompt


async def test_shot_without_scene_profile_still_renders(alice: AsyncClient) -> None:
    """项目没有场景档案时，镜头照样出图，只是不带空间信息。

    这是一条刻意的优雅降级：镜头出图在场景档案存在之前就跑通了，
    把场景变成硬前置条件会让存量项目从"能出图"变成"点了报 409"。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)
    project_id = uuid.UUID(pid)

    # 把 state 里的场景产出摘掉，等价于"存量项目跑过分镜但没有场景档案"
    async with session_scope() as db:
        project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
        state = {k: v for k, v in dict(project.current_state_json).items() if k != "scenes"}
        project.current_state_json = state
        await db.commit()

    r = await alice.post(f"{P}/{pid}/images/shots/1")
    assert r.status_code == 201, r.text

    row = await _task_row(r.json()["id"])
    payload: dict[str, Any] = dict(row.input_json)
    assert "第 1 镜的画面内容" in payload["prompt"], "画面内容还得在"
    assert "主角" in payload["prompt"], "角色还得在"
    assert "铁门外的路面" not in payload["prompt"], "没有场景档案就不该凭空造一个"


async def test_unknown_shot_is_404(alice: AsyncClient) -> None:
    pid = await _run_to_storyboard(alice)
    r = await alice.post(f"{P}/{pid}/images/shots/999")
    assert r.status_code == 404


async def test_shot_before_storyboard_explains_itself(alice: AsyncClient) -> None:
    pid = await _project(alice, "empty-shots")
    r = await alice.post(f"{P}/{pid}/images/shots/1")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "consistency.profile.missing"


# ---------------------------------------------------------------- 列表与隔离


async def test_render_list_reports_subject_and_status(alice: AsyncClient) -> None:
    pid = await _run_to_storyboard(alice)
    portrait = (await alice.post(f"{P}/{pid}/images/characters/zhu_jue")).json()
    scene = (await alice.post(f"{P}/{pid}/images/scenes/office")).json()
    shot = (await alice.post(f"{P}/{pid}/images/shots/2")).json()

    rows = (await alice.get(f"{P}/{pid}/images")).json()
    by_task = {r["task_id"]: r for r in rows}

    assert by_task[portrait["id"]]["subject_kind"] == "character"
    assert by_task[portrait["id"]]["subject_ref"] == "zhu_jue"
    assert by_task[portrait["id"]]["asset_id"] is None, "还没跑，不该有图"
    assert by_task[scene["id"]]["subject_kind"] == "scene"
    assert by_task[scene["id"]]["subject_ref"] == "office"
    assert by_task[scene["id"]]["shot_index"] is None
    assert by_task[shot["id"]]["subject_kind"] == "shot"
    assert by_task[shot["id"]]["shot_index"] == 2
    assert all(r["status"] == "queued" for r in rows)


async def test_plain_image_tasks_are_not_listed_as_renders(alice: AsyncClient) -> None:
    """从 /tasks 直接建的出图任务不属于任何角色或镜号，不能混进来。"""
    pid = await _run_to_storyboard(alice)
    r = await alice.post(
        "/api/v1/tasks",
        json={"type": "image.generate", "project_id": pid, "input": {"prompt": "随手一张", "n": 1}},
    )
    assert r.status_code == 201, r.text

    rows = (await alice.get(f"{P}/{pid}/images")).json()
    assert rows == []


async def test_cross_tenant_is_404_not_403(alice: AsyncClient, bob: AsyncClient) -> None:
    """别人的项目：一律 404。403 会确认这个 id 存在，可枚举。"""
    pid = await _run_to_storyboard(alice)

    assert (await bob.post(f"{P}/{pid}/images/characters/zhu_jue")).status_code == 404
    assert (await bob.post(f"{P}/{pid}/images/scenes/gate")).status_code == 404
    assert (await bob.post(f"{P}/{pid}/images/shots/1")).status_code == 404
    assert (await bob.get(f"{P}/{pid}/images")).status_code == 404
