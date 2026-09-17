"""成品提示词这条生产路径：真实 HTTP 入口 → 新 Agent → 出图任务。

上一轮把"最终提示词由谁写"从确定性拼接换成了 Agent（ADR-036），换掉的是
**生产路径本身**。所以这个文件测的不是"提示词 API 能不能调通"，而是几件
换错了就会悄悄出事的事：

1. 三个出图端点**真的**走了新 Agent，不是留一个预览 API、生产仍走 `compose`；
2. 用户点名的那份提示词（`prompt_run_id`）跨对象、跨项目、跨租户、过期
   四种情况都拒绝——它决定了这张图长什么样，串一次就是画错一个角色；
3. **GET 一分钱不花也一个字不写**：只读路径会 `sync_*` 的话，用户点开看
   一眼就把档案冻进了库，version 往前跳而他什么都没做；
4. 幂等重放与本机离线预检都排在**任何推理之前**——排在后面，用户重发一次
   请求或者连接器没开，都要白花一次模型调用；
5. 还没轮到模型就失败的那些（前置内容不全、没锁画风）能在生成记录里找得到，
   否则用户看到界面报红、点开日志却什么都没有；
6. 记录列表不含全文，详情不回显原始堆栈、不串租户。

**这些用例一律不让出图任务真的跑起来**（`_no_enqueue`）：Worker 是独立容器、
跑在 `ENV=local` 下，它不认测试环境的 Mock 开关，捞到 `image.generate` 就会
拿平台的真 Key 去打万相。这个仓库为"测试打真实上游"栽过一次。

文本生成侧全程 `MockLLM`（`ENV=test` 强制），零上游费用。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from apps.api.core.config import Settings
from apps.api.core.db import session_scope
from apps.api.modules.agent import llm
from apps.api.modules.agent import service as agent_service
from apps.api.modules.consistency import service as consistency
from apps.api.modules.local_runtime import service as lr_service
from apps.api.modules.local_runtime import transport
from apps.api.modules.project import service as project_service
from apps.api.modules.prompting import rules
from apps.api.modules.task import service as task_service
from apps.api.modules.task.models import Task
from tests.conftest import advance_to_gate

pytestmark = pytest.mark.integration

P = "/api/v1/projects"
NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名被调职的刑警。"


@pytest.fixture(autouse=True)
def _no_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """掐掉入队。任务照建、照预扣、照落库，只是没有人去执行它。"""

    async def _noop(task_id: uuid.UUID) -> None:
        del task_id

    monkeypatch.setattr(task_service, "_enqueue", _noop)


class _CountingLLM:
    """数一数这条请求到底调了几次模型。

    "预检早于推理"、"幂等重放不再调用模型"、"GET 零推理"这三条都只能靠
    **调用次数**证明。断言响应体是证明不了的：一次白花的推理照样能返回
    一个正确的响应。
    """

    def __init__(self) -> None:
        self.calls = 0
        self._inner = llm.MockLLM()

    async def complete(self, request: llm.LLMRequest) -> llm.LLMResponse:
        self.calls += 1
        return await self._inner.complete(request)


@pytest.fixture
def counting_llm() -> Any:
    """装一个会计数的 provider，用例结束后还原。

    `runner` 每次调用都现取 `get_provider()`，所以这里换掉全局的那一个就够了。
    还原成 None 而不是 MockLLM：让下一个用例重新走一遍真实的选择逻辑
    （`ENV=test` → MockLLM），别把"选择逻辑"这件事也一起 mock 掉。
    """
    counter = _CountingLLM()
    llm.set_provider(counter)
    yield counter
    llm.set_provider(None)


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


async def _project(client: AsyncClient, title: str = "prompts") -> str:
    return str((await client.post(P, json={"title": title})).json()["id"])


async def _run_to_storyboard(client: AsyncClient, title: str = "prompts") -> str:
    """跑到分镜门：角色档案、场景档案、分镜表都已产出。"""
    pid = await _project(client, title)
    await advance_to_gate(client, pid, "storyboard", user_input=NOVEL)
    return pid


async def _task_count(project_id: str) -> int:
    async with session_scope() as db:
        return int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Task)
                    .where(Task.project_id == uuid.UUID(project_id))
                )
            ).scalar_one()
        )


async def _task_row(task_id: str) -> Task:
    async with session_scope() as db:
        return (await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))).scalar_one()


async def _prompt_runs(client: AsyncClient, pid: str, kind: str, subject_key: str) -> list[Any]:
    """某个对象的提示词运行记录（成功的，新的在前）。"""
    async with session_scope() as db:
        return list(
            await agent_service.list_prompt_runs(
                db,
                org_id=await _org(client),
                project_id=uuid.UUID(pid),
                kind=kind,
                subject_key=subject_key,
                limit=20,
            )
        )


async def _state(client: AsyncClient, pid: str) -> dict[str, Any]:
    async with session_scope() as db:
        project = await project_service.get_project(
            db, org_id=await _org(client), project_id=uuid.UUID(pid)
        )
        return dict(project.current_state_json or {})


async def _set_state(client: AsyncClient, pid: str, state: dict[str, Any]) -> None:
    async with session_scope() as db:
        project = await project_service.get_project(
            db, org_id=await _org(client), project_id=uuid.UUID(pid)
        )
        project.current_state_json = state
        await db.commit()


async def _mutate_shot(client: AsyncClient, pid: str, index: int, **fields: Any) -> None:
    """改分镜表上某一镜的字段，等价于用户在分镜工作台上动了它。"""
    state = await _state(client, pid)
    board = dict(state["storyboard"])
    shots = [dict(sh) for sh in board["shots"]]
    for shot in shots:
        if int(shot["index"]) == index:
            shot.update(fields)
    await _set_state(client, pid, {**state, "storyboard": {**board, "shots": shots}})


def _error(response: Any) -> str:
    """断言这次请求被拒了，返回错误码。"""
    assert response.status_code >= 400, f"本该被拒却成功了：{response.text}"
    return str(response.json()["error"]["code"])


async def _profile_fingerprint(client: AsyncClient, pid: str) -> list[tuple[str, int, Any]]:
    """角色与场景档案的 (ref, version, updated_at) 快照。

    只读路径动了库的话，这三样里至少有一样会变。
    """
    org_id = await _org(client)
    async with session_scope() as db:
        characters = await consistency.list_characters(db, org_id=org_id, project_id=uuid.UUID(pid))
        scenes = await consistency.list_scenes(db, org_id=org_id, project_id=uuid.UUID(pid))
    return sorted(
        [(c.ref, c.version, c.updated_at) for c in characters]
        + [(s.ref, s.version, s.updated_at) for s in scenes]
    )


# ------------------------------------------------- 生产路径真的走了新 Agent


@pytest.mark.parametrize(
    ("path", "kind", "subject_key"),
    [
        ("images/characters/zhu_jue", rules.KIND_CHARACTER, "zhu_jue"),
        ("images/scenes/gate", rules.KIND_SCENE, "gate"),
        ("images/shots/1", rules.KIND_SHOT_IMAGE, "1"),
    ],
)
async def test_render_entry_goes_through_the_new_prompt_agent(
    alice: AsyncClient, path: str, kind: str, subject_key: str
) -> None:
    """三个出图端点都真的调了新 Agent，且把血缘钉进了任务。

    这是 ADR-036 最容易被做成"只加一个预览 API、生产仍走 compose"的地方。
    判据必须落在**出图任务自己的 payload** 上：`prompt_run_id` 指向一次真实
    的提示词运行，`rule_version` 是当前规则版本，`basis_digest` 非空。
    """
    pid = await _run_to_storyboard(alice, f"agent-{kind}")

    r = await alice.post(f"{P}/{pid}/{path}")
    assert r.status_code == 201, r.text

    payload = dict((await _task_row(r.json()["id"])).input_json)
    assert payload["rule_version"] == rules.RULE_VERSION
    assert payload["basis_digest"], "依据摘要没写进任务，这张图的血缘就断了"
    assert payload["prompt"].strip(), "最终提示词必须是全文"

    runs = await _prompt_runs(alice, pid, kind, subject_key)
    assert runs, f"{kind} 没有留下任何提示词运行记录 —— 生产路径没走新 Agent"
    assert str(runs[0].id) == payload["prompt_run_id"]
    assert runs[0].agent_id == rules.AGENT_OF[kind], "调的不是这一类该用的 Agent"
    # 上下文真的送进了模型：风格词、档案、分镜行都在 `user_input` 里
    assert dict(runs[0].input_json)["prompt_kind"] == kind
    assert dict(runs[0].input_json)["subject_key"] == subject_key


async def test_the_model_receives_the_spatial_anchors(alice: AsyncClient) -> None:
    """摄影主轴与固定参照物必须**送进模型**。

    换成 Agent 之后，"锚点有没有进提示词"不再由我们的代码决定，而是由模型
    写不写。能由平台保证的是**它拿到了这些东西**——所以判据落在送进去的
    上下文上，而不是落在 Mock 产出的那段文字上（那只会测出 Mock 的文采）。

    模型有没有照做由 `rules.check_output` 那一道守着，见
    `tests/unit/test_prompt_builders.py`。
    """
    pid = await _run_to_storyboard(alice, "anchors")
    assert (await alice.post(f"{P}/{pid}/images/scenes/gate")).status_code == 201

    runs = await _prompt_runs(alice, pid, rules.KIND_SCENE, "gate")
    user_input = str(dict(runs[0].input_json)["user_input"])

    assert "铁门外的路面" in user_input, "摄影主轴的站位没送给模型"
    assert "朝向建筑正面" in user_input
    assert "锈迹铁门" in user_input, "固定参照物的名称没送给模型"
    assert "右扇下缘锈穿" in user_input, "固定参照物的描述没送给模型"


async def test_scene_prompt_is_a_four_view_grid_with_nobody_in_it(alice: AsyncClient) -> None:
    """B5：2×2 四格、四个格名齐全、人物排除三重否定一句不少。

    这张图是同一场景后续所有镜头的空间基准，里面有人就当不了基准。
    """
    pid = await _run_to_storyboard(alice, "b5")

    r = await alice.post(f"{P}/{pid}/prompts/{rules.KIND_SCENE}/gate")
    assert r.status_code == 201, r.text
    prompt = r.json()["prompt"]

    assert any(g in prompt for g in ("2x2", "2×2")), "没声明 2×2 网格"
    for _field, label in rules.QUADRANT_LABELS:
        assert label in prompt, f"缺了{label}"
    for clause in rules.NO_PEOPLE_CLAUSES:
        assert clause in prompt, f"三重否定缺了：{clause}"
    assert "无文字标注" in prompt


# ------------------------------------------- 景别/角度：影响输入，也影响过期


async def test_changing_shot_size_changes_the_input_and_makes_the_prompt_stale(
    alice: AsyncClient,
) -> None:
    """改了景别：送给模型的输入要变，已准备好的提示词要显示过期。

    在这之前出图只传 `content`，用户在分镜工作台上改的景别与角度全部丢在
    路上——界面显示改成功了，出来的图和改之前一样。这条用例是那件事的反面。
    """
    pid = await _run_to_storyboard(alice, "shot-size")

    first = await alice.post(f"{P}/{pid}/prompts/{rules.KIND_SHOT_IMAGE}/1")
    assert first.status_code == 201, first.text
    assert first.json()["stale"] is False
    before_digest = first.json()["basis_digest"]

    original = next(
        sh for sh in (await _state(alice, pid))["storyboard"]["shots"] if int(sh["index"]) == 1
    )
    assert original["shot_size"] != "极近特写"
    await _mutate_shot(alice, pid, 1, shot_size="极近特写", angle="大角度仰拍")

    # 只读查询立刻知道它过期了，且**不重新推理**（stale 是判断，不是修复）
    stale = await alice.get(f"{P}/{pid}/prompts/{rules.KIND_SHOT_IMAGE}/1")
    assert stale.status_code == 200, stale.text
    assert stale.json()["stale"] is True, "改了景别，旧提示词却没被标过期"
    assert stale.json()["basis_digest"] == before_digest, "GET 不该改写已有记录"

    # 重新准备：摘要变了，新的词里带着新的景别与角度
    again = await alice.post(f"{P}/{pid}/prompts/{rules.KIND_SHOT_IMAGE}/1")
    assert again.status_code == 201, again.text
    assert again.json()["basis_digest"] != before_digest
    assert again.json()["stale"] is False

    runs = await _prompt_runs(alice, pid, rules.KIND_SHOT_IMAGE, "1")
    user_input = str(dict(runs[0].input_json)["user_input"])
    assert "极近特写" in user_input, "改过的景别没送给模型"
    assert "大角度仰拍" in user_input, "改过的角度没送给模型"


async def test_the_same_input_reuses_the_prompt_instead_of_paying_again(
    alice: AsyncClient, counting_llm: Any
) -> None:
    """输入一个字没变时复用已有产出，不重复花一次推理。"""
    pid = await _run_to_storyboard(alice, "reuse")

    first = await alice.post(f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")
    assert first.status_code == 201, first.text
    after_first = counting_llm.calls
    assert after_first >= 1

    second = await alice.post(f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")
    assert second.status_code == 201, second.text
    assert second.json()["run_id"] == first.json()["run_id"], "没复用，建了第二份"
    assert counting_llm.calls == after_first, "复用路径上仍然调了模型"


async def test_a_different_instruction_is_a_different_prompt(alice: AsyncClient) -> None:
    """换一句创作要求就是另一份提示词，不会串用上一次的。"""
    pid = await _run_to_storyboard(alice, "instruction")

    plain = await alice.post(f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")
    tweaked = await alice.post(
        f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue",
        json={"instruction": "让他侧身一点"},
    )
    assert tweaked.status_code == 201, tweaked.text
    assert tweaked.json()["run_id"] != plain.json()["run_id"]


# --------------------------------------------- prompt_run_id：四道闸都要拦


async def test_a_prompt_pinned_from_another_subject_is_refused(alice: AsyncClient) -> None:
    """拿 A 角色的提示词去给 B 角色出图 —— 拒绝，且不建任务。

    串一次的后果是画错一个角色，而且任务会成功、Credits 会扣、
    没有任何地方说过这张图用的是别人的词。
    """
    pid = await _run_to_storyboard(alice, "pin-subject")
    prepared = await alice.post(f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")
    assert prepared.status_code == 201, prepared.text

    before = await _task_count(pid)
    r = await alice.post(
        f"{P}/{pid}/images/characters/guan_zhang",
        json={"prompt_run_id": prepared.json()["run_id"]},
    )
    assert _error(r) == "prompt.run.mismatch"
    assert await _task_count(pid) == before, "被拒的请求不该留下任务"


async def test_a_prompt_pinned_from_another_project_is_404(alice: AsyncClient) -> None:
    """跨项目点名：404。403 会确认这个 run 存在，可枚举。"""
    one = await _run_to_storyboard(alice, "pin-proj-a")
    two = await _run_to_storyboard(alice, "pin-proj-b")

    prepared = await alice.post(f"{P}/{one}/prompts/{rules.KIND_CHARACTER}/zhu_jue")
    assert prepared.status_code == 201, prepared.text

    r = await alice.post(
        f"{P}/{two}/images/characters/zhu_jue",
        json={"prompt_run_id": prepared.json()["run_id"]},
    )
    assert r.status_code == 404, r.text
    assert await _task_count(two) == 0


async def test_a_prompt_pinned_from_another_tenant_is_404(
    alice: AsyncClient, bob: AsyncClient
) -> None:
    """跨租户点名：404，且绝不能拿到对方的词。"""
    alice_pid = await _run_to_storyboard(alice, "pin-alice")
    bob_pid = await _run_to_storyboard(bob, "pin-bob")

    alice_run = (
        await alice.post(f"{P}/{alice_pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")
    ).json()["run_id"]

    r = await bob.post(
        f"{P}/{bob_pid}/images/characters/zhu_jue", json={"prompt_run_id": alice_run}
    )
    assert r.status_code == 404, r.text
    assert await _task_count(bob_pid) == 0


async def test_a_stale_pinned_prompt_is_refused(alice: AsyncClient) -> None:
    """点名的那份词依据的内容已经改过了 —— 拒绝，让用户重新准备。

    静默沿用会出一张按旧景别画的图，静默重算会让用户拿到一份他没看过的词。
    两种都不行，所以这里必须是一个明确的拒绝。
    """
    pid = await _run_to_storyboard(alice, "pin-stale")
    prepared = await alice.post(f"{P}/{pid}/prompts/{rules.KIND_SHOT_IMAGE}/1")
    assert prepared.status_code == 201, prepared.text

    await _mutate_shot(alice, pid, 1, shot_size="极近特写")

    before = await _task_count(pid)
    r = await alice.post(
        f"{P}/{pid}/images/shots/1", json={"prompt_run_id": prepared.json()["run_id"]}
    )
    assert _error(r) == "prompt.run.stale"
    assert await _task_count(pid) == before


async def test_a_pinned_prompt_that_is_still_current_is_accepted(alice: AsyncClient) -> None:
    """正路径：没过期的那份照用，且用的**就是它**，不是后端另准备的一份。"""
    pid = await _run_to_storyboard(alice, "pin-ok")
    prepared = await alice.post(f"{P}/{pid}/prompts/{rules.KIND_SHOT_IMAGE}/1")
    assert prepared.status_code == 201, prepared.text
    run_id = prepared.json()["run_id"]

    r = await alice.post(f"{P}/{pid}/images/shots/1", json={"prompt_run_id": run_id})
    assert r.status_code == 201, r.text

    payload = dict((await _task_row(r.json()["id"])).input_json)
    assert payload["prompt_run_id"] == run_id
    assert payload["prompt"] == prepared.json()["prompt"], "出图用的词和用户看过的那份不一样"


async def test_the_old_render_button_without_a_body_still_works(alice: AsyncClient) -> None:
    """不带请求体的老按钮照常能用 —— 只是走的是新 Agent。"""
    pid = await _run_to_storyboard(alice, "old-button")
    r = await alice.post(f"{P}/{pid}/images/characters/zhu_jue")
    assert r.status_code == 201, r.text
    assert dict((await _task_row(r.json()["id"])).input_json)["prompt_run_id"]


# ----------------------------------------------------- GET：零推理，零写入


async def test_get_prompt_never_calls_the_model(alice: AsyncClient, counting_llm: Any) -> None:
    """只读路径一次模型都不调 —— 点开看一眼不该是一次计费动作。"""
    pid = await _run_to_storyboard(alice, "get-free")
    baseline = counting_llm.calls

    empty = await alice.get(f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")
    assert empty.status_code == 200
    assert empty.json() is None, "还没准备过就该返回 null，不代生成"
    assert counting_llm.calls == baseline

    prepared = await alice.post(f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")
    assert prepared.status_code == 201, prepared.text
    after_prepare = counting_llm.calls

    for _ in range(3):
        again = await alice.get(f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")
        assert again.status_code == 200
        assert again.json()["run_id"] == prepared.json()["run_id"]
    assert counting_llm.calls == after_prepare, "GET 触发了推理"


async def test_get_prompt_writes_nothing_to_the_profiles(alice: AsyncClient) -> None:
    """只读路径不 `sync_*`、不递增 version、不动 `updated_at`。

    会写的话，用户点开看一眼就把上游产出冻进了库：档案 version 往前跳、
    `updated_at` 变了，而他什么都没做。更糟的是它让"看一眼"和"确认这一版"
    变成同一个动作。
    """
    pid = await _run_to_storyboard(alice, "get-readonly")
    assert (
        await alice.post(f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")
    ).status_code == 201

    before = await _profile_fingerprint(alice, pid)
    assert before, "前置没跑起来，这条用例会空过"

    for kind, key in (
        (rules.KIND_CHARACTER, "zhu_jue"),
        (rules.KIND_SCENE, "gate"),
        (rules.KIND_SHOT_IMAGE, "1"),
    ):
        assert (await alice.get(f"{P}/{pid}/prompts/{kind}/{key}")).status_code == 200

    assert await _profile_fingerprint(alice, pid) == before, "GET 改了档案"


async def test_get_does_not_create_missing_profiles(alice: AsyncClient) -> None:
    """还没跑过角色阶段的项目，GET 不许顺手把档案建出来。"""
    pid = await _project(alice, "get-empty")

    r = await alice.get(f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")
    assert r.status_code == 200
    assert r.json() is None
    assert await _profile_fingerprint(alice, pid) == []


async def test_get_prompt_across_tenants_is_404_not_null(
    alice: AsyncClient, bob: AsyncClient
) -> None:
    """别人的项目要 404，不能给 200 + null。

    200 + null 等于确认"这个项目不存在任何提示词"，而正确答案是
    "这个项目与你无关"。
    """
    pid = await _run_to_storyboard(alice, "get-tenant")
    assert (await bob.get(f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")).status_code == 404
    assert (await bob.post(f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")).status_code == 404


# ------------------------------------------ 幂等与预检都排在推理之前


async def test_an_idempotent_replay_does_not_pay_for_a_second_inference(
    alice: AsyncClient, counting_llm: Any
) -> None:
    """同一个幂等键重发：返回同一条任务，且**不再调一次模型**。

    幂等键的全部意义是"重发不该有副作用"。自从提示词改由 Agent 合成，
    组装入参就等于一次真实的模型调用——等 `create_task` 去短路已经晚了。
    """
    pid = await _run_to_storyboard(alice, "idempotent")
    key = f"render-{uuid.uuid4().hex}"

    first = await alice.post(
        f"{P}/{pid}/images/characters/zhu_jue", headers={"Idempotency-Key": key}
    )
    assert first.status_code == 201, first.text
    after_first = counting_llm.calls
    tasks_after_first = await _task_count(pid)

    second = await alice.post(
        f"{P}/{pid}/images/characters/zhu_jue", headers={"Idempotency-Key": key}
    )
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"], "幂等键没命中，建了第二条任务"
    assert counting_llm.calls == after_first, "幂等重放又白花了一次推理"
    assert await _task_count(pid) == tasks_after_first


async def test_the_local_preflight_runs_before_any_inference(
    alice: AsyncClient, counting_llm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """选了本机出图而连接器没开：拒绝，且**一次模型都没调**。

    先推理再发现连不上，等于白花一次钱，用户还会在任务中心看到一条本来
    就不该存在的失败记录。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, "preflight")

    settings = Settings(  # type: ignore[arg-type]
        env="test",
        local_cli_enabled=True,
        local_cli_org_id=org_id,
        local_cli_project_ids=[uuid.UUID(pid)],
        local_cli_provider="codex",
        local_cli_token="bridge-" + "k" * 40,
        local_cli_timeout_seconds=30,
        local_cli_image_timeout_seconds=60,
    )
    monkeypatch.setattr(lr_service, "get_settings", lambda: settings)

    r = transport._r()
    try:
        await r.delete(transport.heartbeat_key("codex"))
    finally:
        await transport._release(r)

    baseline = counting_llm.calls
    resp = await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "local"})

    assert _error(resp) == "local_runtime.offline"
    assert counting_llm.calls == baseline, "预检排在推理后面，白花了一次模型调用"
    assert await _task_count(pid) == 0


# ------------------------------------------ 缺前置 / 没锁画风：拒绝且可追溯


async def test_missing_style_tokens_refuses_and_creates_no_image_task(
    alice: AsyncClient,
) -> None:
    """项目的人物版画风描述词是空的 —— 拒绝出图，不建任务。

    没有风格词就没有"必须原样保留"的东西可校验，出的图也不属于任何一部片子。
    退回旧拼接"修好"它更糟：用户拿到一张与全片画风无关的图，而且无人告知。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, "unlocked")

    async with session_scope() as db:
        style = await consistency.get_style(db, org_id=org_id, project_id=uuid.UUID(pid))
        assert style is not None
        style.character_tokens = ""
        await db.commit()

    before = await _task_count(pid)
    r = await alice.post(f"{P}/{pid}/images/characters/zhu_jue")
    assert _error(r) == "prompt.style.unlocked"
    assert await _task_count(pid) == before


async def test_a_shot_referencing_a_character_without_a_profile_is_refused(
    alice: AsyncClient,
) -> None:
    """分镜引用了没有档案的角色 —— 拒绝，不猜这个人长什么样。

    以前这是"跳过并记 warning"：模型手里少一个人的外貌，而它仍然要把这一镜
    画出来，结果是它自己编一个人，下一镜又不一样。
    """
    pid = await _run_to_storyboard(alice, "ghost-character")
    await _mutate_shot(alice, pid, 1, character_refs=["zhu_jue", "you_ling"])

    before = await _task_count(pid)
    r = await alice.post(f"{P}/{pid}/images/shots/1")
    assert _error(r) == "prompt.context.incomplete"
    assert "you_ling" in r.json()["error"]["message"], "错误里要点名是哪个角色缺档案"
    assert await _task_count(pid) == before


async def test_a_shot_whose_scene_has_no_profile_points_at_the_scene_step(
    alice: AsyncClient,
) -> None:
    """分镜指向一个没有档案的场景 —— 拒绝，且错误要点到**场景**这一步。

    同一类前置缺失在缺角色时点名角色、缺场景时必须点名场景。文案指错一步，
    用户会去重跑角色阶段，跑完回来仍然出不了图，而错误一个字都没变。
    """
    pid = await _run_to_storyboard(alice, "ghost-scene")
    await _mutate_shot(alice, pid, 1, scene_ref="nowhere")

    before = await _task_count(pid)
    r = await alice.post(f"{P}/{pid}/images/shots/1")
    assert _error(r) == "prompt.context.incomplete"

    message = r.json()["error"]["message"]
    assert "场景" in message, f"缺的是场景档案，文案却没提场景：{message}"
    assert "nowhere" in message, "错误里要点名是哪个场景缺档案"
    assert await _task_count(pid) == before, "被拒的请求不该留下任务"


async def test_a_failure_before_any_inference_is_findable_in_the_records(
    alice: AsyncClient,
) -> None:
    """还没轮到模型就失败的那一次，必须能在生成记录里找到。

    没有它，这类失败在日志页彻底看不见：`build` 在 `agent_runs` 那一行创建
    之前就抛了，用户看到界面报红、点开生成记录却什么都没有，没法回答
    "我刚才到底点了什么、为什么不行"。
    """
    pid = await _run_to_storyboard(alice, "findable")
    await _mutate_shot(alice, pid, 1, character_refs=["you_ling"])

    r = await alice.post(f"{P}/{pid}/images/shots/1")
    assert _error(r) == "prompt.context.incomplete"

    records = (await alice.get(f"{P}/{pid}/generation-records")).json()
    failed = [x for x in records if x["error_code"] == "prompt.context.incomplete"]
    assert failed, f"前置失败没留下记录：{[x['error_code'] for x in records]}"

    row = failed[0]
    assert row["status"] == "failed"
    assert row["record_type"] == "agent"
    # 没有调用过模型，就不该编一个模型名出来
    assert row["model_id"] is None, "没发生过的推理不该被记成用了某个模型"
    assert row["subject_kind"] == rules.KIND_SHOT_IMAGE
    assert row["subject_key"] == "1"

    detail = await alice.get(f"{P}/{pid}/generation-records/agent/{row['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["steps"] == [], "没有尝试过，就不该有尝试步骤"


async def test_a_read_only_failure_leaves_no_record(alice: AsyncClient) -> None:
    """GET 失败不留痕。

    用户只是点开看了一眼，给他攒一串失败记录，真正的失败反而被淹掉。
    """
    pid = await _run_to_storyboard(alice, "no-noise")
    await _mutate_shot(alice, pid, 1, character_refs=["you_ling"])

    before = len((await alice.get(f"{P}/{pid}/generation-records")).json())
    assert (await alice.get(f"{P}/{pid}/prompts/{rules.KIND_SHOT_IMAGE}/1")).status_code == 200
    after = (await alice.get(f"{P}/{pid}/generation-records")).json()
    assert len(after) == before, "只读查询给日志里塞了失败记录"


# ---------------------------------------------------------- 生成记录的卫生


async def test_the_record_list_carries_no_full_text(alice: AsyncClient) -> None:
    """列表不含全文。

    塞全文会让一个 600 镜的项目一次拉几十兆，而用户在列表上要做的判断
    只有"哪一条失败了、点哪一条"。
    """
    pid = await _run_to_storyboard(alice, "no-fulltext")
    assert (await alice.post(f"{P}/{pid}/images/characters/zhu_jue")).status_code == 201

    rows = (await alice.get(f"{P}/{pid}/generation-records")).json()
    assert rows
    for row in rows:
        for banned in ("prompt", "user_input", "negative_prompt", "output", "steps"):
            assert banned not in row, f"列表里出现了全文字段 {banned}"


async def test_the_record_detail_hides_raw_output_and_stacks(alice: AsyncClient) -> None:
    """详情不回显模型原始输出，也不回显上游原文与堆栈。

    原始输出可能夹着模型的思考过程，或者把系统提示词整段复述回来；
    上游原文里出现过回显的认证头、本机主机名与仓库内绝对路径。
    """
    pid = await _run_to_storyboard(alice, "hygiene")
    prepared = await alice.post(f"{P}/{pid}/prompts/{rules.KIND_CHARACTER}/zhu_jue")
    assert prepared.status_code == 201, prepared.text

    records = (await alice.get(f"{P}/{pid}/generation-records")).json()
    row = next(x for x in records if x["id"] == prepared.json()["run_id"])

    detail = (await alice.get(f"{P}/{pid}/generation-records/agent/{row['id']}")).json()
    assert detail["steps"], "成功的推理应当留下尝试步骤"
    for step in detail["steps"]:
        assert step["raw_output"] is None, "原始输出漏给用户了"
    # 成品提示词是用户的作品，不是隐藏推理 —— 全文要给
    assert detail["prompt"].strip()
    assert detail["rule_version"] == rules.RULE_VERSION
    assert detail["incomplete"] is False

    blob = str(detail)
    for banned in ("Traceback", "Authorization", "sk-", "site-packages"):
        assert banned not in blob, f"详情里漏出了 {banned}"


async def test_a_record_detail_does_not_cross_projects_or_tenants(
    alice: AsyncClient, bob: AsyncClient
) -> None:
    """详情按项目隔离：换一个项目 id、换一个租户都拿不到。"""
    one = await _run_to_storyboard(alice, "detail-a")
    two = await _run_to_storyboard(alice, "detail-b")
    bob_pid = await _run_to_storyboard(bob, "detail-bob")

    run_id = (await alice.post(f"{P}/{one}/prompts/{rules.KIND_CHARACTER}/zhu_jue")).json()[
        "run_id"
    ]

    assert (await alice.get(f"{P}/{one}/generation-records/agent/{run_id}")).status_code == 200
    assert (await alice.get(f"{P}/{two}/generation-records/agent/{run_id}")).status_code == 404
    assert (await bob.get(f"{P}/{bob_pid}/generation-records/agent/{run_id}")).status_code == 404
    assert (await bob.get(f"{P}/{one}/generation-records")).status_code == 404


async def test_an_image_record_links_back_to_the_prompt_run(alice: AsyncClient) -> None:
    """出图记录要能回查"这张图的词是哪一次推理写的"。"""
    pid = await _run_to_storyboard(alice, "linkback")
    created = await alice.post(f"{P}/{pid}/images/characters/zhu_jue")
    assert created.status_code == 201, created.text

    detail = (await alice.get(f"{P}/{pid}/generation-records/image/{created.json()['id']}")).json()

    assert detail["record_type"] == "image"
    assert detail["prompt"].strip(), "出图记录要能看到用的是什么词"
    assert detail["rule_version"] == rules.RULE_VERSION
    assert detail["basis_digest"]
    # 上游还没跑（入队被掐掉了），没回报改写后的词就保持 null
    assert detail["actual_prompt"] is None
    assert detail["incomplete"] is False
