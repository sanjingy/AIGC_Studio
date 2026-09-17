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
from apps.api.modules.agent import llm, orchestrator
from apps.api.modules.agent import service as agent_service
from apps.api.modules.consistency import service as consistency
from apps.api.modules.consistency.models import SceneProfile, ShotConditioning
from apps.api.modules.project import service as project_service
from apps.api.modules.prompting import rules as prompt_rules
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


async def _model_input(client: AsyncClient, pid: str, kind: str, subject_key: str) -> str:
    """这一次**送进模型**的上下文全文。

    提示词改由 Agent 合成之后（ADR-036），"档案里的某个字段有没有进图"不再
    由我们的代码决定，而是由模型写不写。平台能保证并且必须保证的是
    **它拿到了这些东西**——所以这类断言落在送进去的上下文上，而不是落在
    MockLLM 产出的那段文字上（那只会测出 Mock 的文采，测不出链路）。

    模型有没有照做，由 `rules.check_output` 那一道守着，见
    `tests/unit/test_prompt_builders.py`。
    """
    async with session_scope() as db:
        runs = await agent_service.list_prompt_runs(
            db,
            org_id=await _org(client),
            project_id=uuid.UUID(pid),
            kind=kind,
            subject_key=subject_key,
            limit=1,
        )
    assert runs, f"{kind}/{subject_key} 没有留下提示词运行记录 —— 生产路径没走新 Agent"
    return str(dict(runs[0].input_json)["user_input"])


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

    assert "主角" in payload["prompt"], "提示词里必须有这个角色"
    assert positive in payload["prompt"], "人物版风格词必须由系统注入"
    assert scene_only not in payload["prompt"], "基准立绘不该带场景版风格词"
    assert "纯色背景" in payload["prompt"], "基准立绘必须是中性构图"
    assert payload["negative_prompt"] == negative
    assert payload["seed"] == seed_base
    assert payload["project_id"] == pid
    assert payload["subject_kind"] == "character"
    assert payload["subject_ref"] == "zhu_jue"

    # 这张图的血缘：哪一次推理写的词、按哪一版规则、依据什么内容。
    # 缺了它们，"这张图为什么长这样"只能靠猜。
    assert payload["prompt_run_id"]
    assert payload["rule_version"] == prompt_rules.RULE_VERSION
    assert payload["basis_digest"]

    # 结构化外貌**既要送进模型，也要活到成品词里**。
    #
    # 只断言前者是不够的（这正是上一轮返工的根因）：平台把档案送到了，模型
    # 把发型整句删掉、把服装归纳成两个字，任务照建、图照出，而这张立绘是
    # 后续每一镜的比对基准。成品词这一侧由 `rules.check_output` 拿**上下文里
    # 的已知事实**去比对，不合格根本建不出任务——所以这里断言得到的 payload
    # 里确实有这些事实，等于同时证明了那道校验真的在生产路径上。
    user_input = await _model_input(alice, pid, prompt_rules.KIND_CHARACTER, "zhu_jue")
    assert "黑色短发，额前碎发" in user_input, "角色的发型没送给模型"
    assert "深灰西装外套" in user_input, "角色的服装没送给模型"

    assert "黑色短发" in payload["prompt"], "角色的发型没能活到最终出图的提示词里"
    assert "额前碎发" in payload["prompt"]
    assert "深灰西装外套" in payload["prompt"], "角色的服装没能活到最终出图的提示词里"
    assert "黑色皮鞋" in payload["prompt"], "角色的鞋子没能活到最终出图的提示词里"

    # 计费：走的是普通任务那条路，预扣必须已经记上
    mid = await _balance(alice)
    assert mid["reserved"] == before["reserved"] + created["estimated_cost"]


async def test_unknown_character_is_404(alice: AsyncClient) -> None:
    pid = await _run_to_storyboard(alice)
    r = await alice.post(f"{P}/{pid}/images/characters/nobody")
    assert r.status_code == 404


async def test_a_rewritten_appearance_fact_is_refused_and_costs_nothing(
    alice: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型改写了一处已知事实、同时把自报字段修得自洽 —— 仍然被拒。

    这是本轮返工的独立反例，测的是校验的**判据**而不是它的结果：

    - 只动一处（把档案上的「深灰西装外套」归纳成「西装」）。其余一个字不改，
      所以过不了的原因只可能是那一处；
    - 同时把 `subject_identity` / `accessories` 两个**模型自报**字段写成与档案
      自洽的样子。只信任自报字段的校验在这里会全绿——而那正是上一轮的漏洞。

    还要证明被拒之后**没花钱**：不建出图任务、预扣余额一分没动，但失败要留
    得下记录，否则用户只看到界面报红、点开日志什么都没有。
    """
    pid = await _run_to_storyboard(alice)
    original = llm._BUILDERS["CharacterPortraitPrompt"]

    def tampered(seed: int, user: str) -> dict[str, Any]:
        output = dict(original(seed, user))
        output["prompt"] = str(output["prompt"]).replace("深灰西装外套", "西装")
        output["subject_identity"] = "日本"
        output["accessories"] = "警号铜牌"
        return output

    monkeypatch.setitem(llm._BUILDERS, "CharacterPortraitPrompt", tampered)

    before_balance = await _balance(alice)
    before_images = len((await alice.get(f"{P}/{pid}/images")).json())

    r = await alice.post(f"{P}/{pid}/images/characters/zhu_jue")

    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "prompt.output.invalid"
    assert "深灰西装外套" in r.json()["error"]["message"], "错误里要点名是哪一处事实丢了"

    # 不建图任务、不预扣
    assert len((await alice.get(f"{P}/{pid}/images")).json()) == before_images
    assert await _balance(alice) == before_balance, "被拒的请求动了余额"

    # 但这次失败要查得到：模型到底写了什么、被哪一条规则拦下的
    records = (await alice.get(f"{P}/{pid}/generation-records")).json()
    assert any(x["error_code"] == "prompt.output.invalid" for x in records), (
        f"校验失败没留下可查的记录：{[x['error_code'] for x in records]}"
    )


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

    # 锚点：这两样不送给模型，场景出图就只是"看着像"而没有一致性
    user_input = await _model_input(alice, pid, prompt_rules.KIND_SCENE, "gate")
    assert "铁门外的路面" in user_input, "摄影主轴的站位没送给模型"
    assert "朝向建筑正面" in user_input
    assert "锈迹铁门" in user_input, "固定参照物的名称没送给模型"
    assert "右扇下缘锈穿" in user_input, "固定参照物的描述没送给模型"

    # 成品词这一侧守两样：B5 的硬结构（四格、三重否定、无文字标注），
    # 以及**档案上的空间事实真的活到了最终提示词里**——主轴三段、每一条锚点
    # 的名称与描述。后者是这张图能当空间基准的全部理由：只送进模型不算数，
    # 模型不写，出来的四格仍然各朝各的方向。
    prompt = payload["prompt"]
    assert any(g in prompt for g in ("2x2", "2×2")), "场景概念图不是 2×2 四视图"
    for _field, label in prompt_rules.QUADRANT_LABELS:
        assert label in prompt, f"四视图缺了{label}"
    for clause in prompt_rules.NO_PEOPLE_CLAUSES:
        assert clause in prompt, f"人物排除的三重否定缺了：{clause}"

    assert "铁门外的路面" in prompt, "摄影主轴的站位没能活到最终提示词里"
    assert "朝向建筑正面" in prompt, "摄影主轴的朝向没能活到最终提示词里"
    assert "锈迹铁门" in prompt, "固定参照物的名称没能活到最终提示词里"
    assert "右扇下缘锈穿" in prompt, "固定参照物的描述没能活到最终提示词里"
    assert any(p in prompt for p in prompt_rules.AXIS_BASIS_PHRASES), "没有把摄影主轴点明为构图依据"

    assert positive in prompt, "场景版风格词必须由系统注入"
    assert character_only not in prompt, "场景参考图不该带人物质感词"
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

    # mock 分镜表第 1 镜的 scene_ref 是 gate。空间锚点既要送进模型，
    # **也要活到成品词里**——"身后是什么"必须从锚点推演，而推演过没有
    # 只能看最终提示词里有没有留下那几条事实。只断言输入的话，模型套用
    # 一个固定背景照样全绿，而下一镜的背景又会变成别的。
    user_input = await _model_input(alice, pid, prompt_rules.KIND_SHOT_IMAGE, "1")
    assert "铁门外的路面" in user_input, "摄影主轴没送给这一镜"
    assert "锈迹铁门" in user_input, "固定参照物的名称没送给这一镜"
    assert "右扇下缘锈穿" in user_input, "固定参照物的描述没送给这一镜"

    prompt = payload["prompt"]
    assert "铁门外的路面" in prompt, "摄影主轴的站位没能活到这一镜的最终提示词"
    assert "朝向建筑正面" in prompt, "摄影主轴的朝向没能活到这一镜的最终提示词"
    assert "红砖三层建筑的正门石阶" in prompt, "摄影主轴的远景末端没能活到这一镜的最终提示词"
    assert "锈迹铁门" in prompt, "固定参照物的名称没能活到这一镜的最终提示词"
    assert "右扇下缘锈穿" in prompt, "固定参照物的描述没能活到这一镜的最终提示词"
    # **每一条**锚点，不是"至少一条"：留哪几条由模型自己挑的话，它挑剩下的
    # 那几条下一镜就会换个样子，同一个院子又开始漂。
    assert "右下角螺丝缺一颗" in prompt, "第二条锚点的描述没能活到这一镜的最终提示词"

    # 五要素里的"身后背景"必须真的落在成品词上（schema 层强制非空，
    # `rules` 层强制它出现在正文里）——它是原文点名最容易出错的一项。
    assert payload["prompt"].strip()

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


async def test_shot_without_scene_profile_is_refused(alice: AsyncClient) -> None:
    """项目没有场景档案时，镜头**不出图**，给一个明确的中文错误。

    ⚠️ **这是一次产品级行为变更**（ADR-036，需 Lead 确认）。这条用例原先
    断言的是相反的行为："没有场景档案也照样出图，只是不带空间信息"，理由
    是优雅降级，别让存量项目从"能出图"变成"点了报错"。

    改变它的是新契约里的两条：C4 五要素要求**身后背景必须从空间锚点推演**，
    而"缺关键前置数据返回明确中文错误、不自由猜关键身份"是 ADR-037 第 2 条。
    没有场景档案时模型手里没有任何空间信息，它仍然要把这一镜画出来——
    结果是它自己编一个背景，而下一镜编的又不一样。那正是这次整改要消灭的
    东西，所以降级在这里不再成立。

    代价是真实的：跑过分镜但没有场景档案的存量项目，出图入口会从"能点"
    变成"点了报 409"。他们需要重跑一次场景阶段。
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

    before = len((await alice.get(f"{P}/{pid}/images")).json())
    r = await alice.post(f"{P}/{pid}/images/shots/1")

    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "consistency.profile.missing"
    assert r.json()["error"]["detail"]["missing_stage"] == "scenes"
    # 文案要指到**缺的那一步**。同一个错误码在缺角色和缺场景两条路径上都会抛，
    # 而它原来只说"还没有角色设定"——缺场景的用户照着做会去重跑角色阶段，
    # 跑完回来仍然出不了图，而错误一个字都没变。
    user_message = r.json()["error"]["user_message"]
    assert "场景" in user_message, f"缺的是场景档案，文案却没提场景：{user_message}"

    # 不建任务、不预扣：判不合格就不该留下一条注定失败的出图记录
    assert len((await alice.get(f"{P}/{pid}/images")).json()) == before

    # 但这次失败要能在生成记录里找到，否则用户只看到界面报红
    records = (await alice.get(f"{P}/{pid}/generation-records")).json()
    assert any(x["error_code"] == "consistency.profile.missing" for x in records), (
        "前置失败没留下任何可查的记录"
    )


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
