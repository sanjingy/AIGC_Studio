"""从产品界面出图：角色基准立绘 + 单个分镜。

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
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from apps.api.core.db import session_scope
from apps.api.modules.agent import orchestrator
from apps.api.modules.consistency import service as consistency
from apps.api.modules.consistency.models import ShotConditioning
from apps.api.modules.project import service as project_service
from apps.api.modules.task import service as task_service
from apps.api.modules.task.models import Task

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
    """跑到第二道门：角色档案、场景档案、分镜表都已产出。"""
    pid = await _project(client)
    r = await client.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": NOVEL})
    assert r.status_code == 200, r.text
    await _approve_pending(client, pid)
    r = await client.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": ""})
    assert r.json()["gate_opened"] == "storyboard", r.text
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
    assert style.positive_tokens, "风格档案必须带系统注入的风格词"
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
        style.positive_tokens = sentinel
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
    assert style.positive_tokens == sentinel, "冻结的风格被覆盖了"

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
            style.positive_tokens,
            style.negative_tokens,
            style.seed_base,
        )

    assert "主角" in payload["prompt"], "提示词里必须有角色的结构化外貌"
    assert "黑色短发，额前碎发" in payload["prompt"]
    assert positive in payload["prompt"], "风格词必须由系统注入"
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
        assert style.positive_tokens in payload["prompt"]
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
    shot = (await alice.post(f"{P}/{pid}/images/shots/2")).json()

    rows = (await alice.get(f"{P}/{pid}/images")).json()
    by_task = {r["task_id"]: r for r in rows}

    assert by_task[portrait["id"]]["subject_kind"] == "character"
    assert by_task[portrait["id"]]["subject_ref"] == "zhu_jue"
    assert by_task[portrait["id"]]["asset_id"] is None, "还没跑，不该有图"
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
    assert (await bob.post(f"{P}/{pid}/images/shots/1")).status_code == 404
    assert (await bob.get(f"{P}/{pid}/images")).status_code == 404
