"""字段级确定性编辑：`PATCH /projects/{id}/outputs/{role}`。

这条路径存在的理由：在它之前，改一个角色的发色**只有** `POST /revise`
一条路——写一句话让同一个 Agent 把整块产出重写一遍。慢、要花钱、
而且模型可能顺手改坏别的字段。做表格式的编辑界面而不做这条路，
得到的只会是一个"看起来能编辑、点了却在调模型"的假界面。

所以这里的断言分成两半：一半验"改对了"（值落库、一致性投影跟着变、
下游被标过期），另一半验**没发生什么**（没建任务、没跑 Agent、
没有一条流水、余额一分不动）。后一半看着像凑数，但那正是这条路径的
全部风险所在：只要哪天有人把它接回 `create_task`，用户就会为
手动敲的两个字付费。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from apps.api.core.db import session_scope
from apps.api.modules.agent.models import AgentRun
from apps.api.modules.billing.models import CreditAccount, CreditTransaction
from apps.api.modules.consistency import service as consistency
from apps.api.modules.task.models import Task
from tests.conftest import advance_to_gate

pytestmark = pytest.mark.integration

P = "/api/v1/projects"

NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名被调职的刑警。"


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


async def run_to_storyboard(client: AsyncClient, title: str = "patch") -> str:
    """跑到分镜门：五个生产阶段全部有产出。

    途中每一道门由 `advance_to_gate` 通过，这里不写死门的道数。
    """
    pid = str((await client.post(P, json={"title": title})).json()["id"])
    await advance_to_gate(client, pid, "storyboard", user_input=NOVEL)
    return pid


async def patch(
    client: AsyncClient,
    pid: str,
    role: str,
    patches: list[dict],
    reason: str | None = None,
) -> tuple[int, dict]:
    body: dict = {"patches": patches}
    if reason is not None:
        body["reason"] = reason
    r = await client.patch(f"{P}/{pid}/outputs/{role}", json=body)
    return r.status_code, dict(r.json()) if r.content else {}


async def _characters(client: AsyncClient, pid: str) -> list[dict]:
    """从一次无害的读取里拿到当前的角色块。

    没有"读整块产出"的端点，所以借 revisions 的返回值不行——
    这里直接从库里读，测试断言的是落库结果本身。
    """
    async with session_scope() as db:
        from apps.api.modules.project import service as project_service

        project = await project_service.get_project(
            db, org_id=await _org(client), project_id=uuid.UUID(pid)
        )
        return list((project.current_state_json or {})["characters"]["characters"])


async def _stale(client: AsyncClient, pid: str) -> list[str]:
    return list((await client.get(f"{P}/{pid}")).json()["stale_roles"])


async def _counts(org_id: uuid.UUID, pid: str) -> tuple[int, int, int]:
    async with session_scope() as db:
        tasks = int(
            (
                await db.execute(
                    select(func.count()).select_from(Task).where(Task.project_id == uuid.UUID(pid))
                )
            ).scalar_one()
        )
        runs = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(AgentRun)
                    .where(AgentRun.project_id == uuid.UUID(pid))
                )
            ).scalar_one()
        )
        ledger = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(CreditTransaction)
                    .join(CreditAccount, CreditAccount.id == CreditTransaction.account_id)
                    .where(CreditAccount.org_id == org_id)
                )
            ).scalar_one()
        )
    return tasks, runs, ledger


# ---------------------------------------------------------------- 正常改动


async def test_patch_writes_the_field_and_only_that_field(alice: AsyncClient) -> None:
    pid = await run_to_storyboard(alice)
    before = await _characters(alice, pid)

    code, body = await patch(
        alice,
        pid,
        "characters",
        [{"path": "/characters/0/hair", "value": "栗棕色短发"}],
        reason="美术说黑发出图太闷",
    )
    assert code == 200, body
    assert body["changed"] == 1
    assert body["output"]["characters"][0]["hair"] == "栗棕色短发"

    after = await _characters(alice, pid)
    assert after[0]["hair"] == "栗棕色短发"
    # 同一个角色的其他字段、以及另一个角色，一个字都不该动
    assert after[0]["name"] == before[0]["name"]
    assert after[0]["outfit"] == before[0]["outfit"]
    assert after[1] == before[1]


async def test_one_request_is_one_batch(alice: AsyncClient) -> None:
    """一次 UI 操作改几个字段，落成一个可整体撤销的批次。"""
    pid = await run_to_storyboard(alice)
    code, body = await patch(
        alice,
        pid,
        "characters",
        [
            {"path": "/characters/0/name", "value": "陈默"},
            {"path": "/characters/1/name", "value": "白馆长"},
        ],
    )
    assert code == 200, body
    assert body["changed"] == 2

    rows = (await alice.get(f"{P}/{pid}/revisions")).json()["items"]
    assert len(rows) == 1, "两处改动应该只产生一个批次"
    assert len(rows[0]["changes"]) == 2


async def test_patch_marks_downstream_stale(alice: AsyncClient) -> None:
    """改了上游，下游停在旧版——规则与聊天修订完全一致。"""
    pid = await run_to_storyboard(alice)
    assert await _stale(alice, pid) == []

    code, body = await patch(
        alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默"}]
    )
    assert code == 200, body
    assert body["stale_roles"] == ["scenes", "storyboard"]
    # 只在响应里不算数：刷新页面还要读得到（ADR-008）
    assert await _stale(alice, pid) == ["scenes", "storyboard"]


async def test_patch_syncs_the_consistency_projection(alice: AsyncClient) -> None:
    """改了发色，`character_profiles` 也要跟着变。

    不同步的后果是用户改完之后出图仍然是黑发——他会认为"改了没用"，
    而这是最难被报成 bug 的一类失败（评估文档 §5.1 风险 1）。
    """
    org_id = await _org(alice)
    pid = await run_to_storyboard(alice)

    code, _ = await patch(
        alice, pid, "characters", [{"path": "/characters/0/hair", "value": "栗棕色短发"}]
    )
    assert code == 200

    async with session_scope() as db:
        profiles = await consistency.list_characters(db, org_id=org_id, project_id=uuid.UUID(pid))
    zhu = next(c for c in profiles if c.ref == "zhu_jue")
    assert zhu.appearance_json["hair"] == "栗棕色短发"


async def test_patch_syncs_scene_projection(alice: AsyncClient) -> None:
    org_id = await _org(alice)
    pid = await run_to_storyboard(alice)

    # 改的是**某一个光照状态的描述**，不是一个叫 lighting 的字段——
    # 光照已经是一组具名状态，"这个场景的光"没有单数形式了。
    code, body = await patch(
        alice,
        pid,
        "scenes",
        [{"path": "/scenes/0/lighting_states/0/description", "value": "顶灯惨白，无阴影"}],
    )
    assert code == 200, body
    ref = body["output"]["scenes"][0]["ref"]

    async with session_scope() as db:
        profiles = await consistency.list_scenes(db, org_id=org_id, project_id=uuid.UUID(pid))
    scene = next(s for s in profiles if s.ref == ref)
    assert scene.spatial_json["lighting_states"][0]["description"] == "顶灯惨白，无阴影"


async def test_patch_costs_nothing(alice: AsyncClient) -> None:
    """不建任务、不跑 Agent、不产生一条流水、余额不动。

    这是整个功能最重要的一条断言。手动改两个字还要扣 Credits 是纯粹的
    错误，而且是那种没人会报 bug、只会默默流失的错误。
    """
    org_id = await _org(alice)
    pid = await run_to_storyboard(alice)

    before = await _counts(org_id, pid)
    balance_before = (await alice.get("/api/v1/credits/balance")).json()

    code, _ = await patch(
        alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默"}]
    )
    assert code == 200

    assert await _counts(org_id, pid) == before, "手动 patch 建了任务/AgentRun/流水"
    assert (await alice.get("/api/v1/credits/balance")).json() == balance_before


# ---------------------------------------------------------------- 拒绝


async def test_schema_violation_is_422_and_leaves_nothing_behind(alice: AsyncClient) -> None:
    """非法值当场 422，且事务里不留脏数据——产出、历史都不该有任何变化。"""
    pid = await run_to_storyboard(alice)
    before = await _characters(alice, pid)

    code, body = await patch(
        alice, pid, "characters", [{"path": "/characters/0/camp", "value": "路人甲"}]
    )
    assert code == 422, body
    assert body["error"]["detail"]["errors"]

    assert await _characters(alice, pid) == before
    assert (await alice.get(f"{P}/{pid}/revisions")).json()["items"] == []


async def test_a_valid_field_that_breaks_the_whole_block_is_rejected(alice: AsyncClient) -> None:
    """整块校验，不是只校验被改的那个字段。

    `personality` 是个合法的字符串数组，但它有 min_length=1——
    只看"这是不是一个数组"永远发现不了。
    """
    pid = await run_to_storyboard(alice)
    code, body = await patch(
        alice, pid, "characters", [{"path": "/characters/0/personality", "value": []}]
    )
    assert code == 422, body


async def test_partial_batch_never_lands(alice: AsyncClient) -> None:
    """一批里有一条非法，整批都不生效。"""
    pid = await run_to_storyboard(alice)
    before = await _characters(alice, pid)

    code, _ = await patch(
        alice,
        pid,
        "characters",
        [
            {"path": "/characters/0/name", "value": "陈默"},
            {"path": "/characters/1/camp", "value": "路人甲"},
        ],
    )
    assert code == 422
    assert await _characters(alice, pid) == before, "第一条改动漏进去了"


@pytest.mark.parametrize(
    "path",
    [
        "/characters/9/name",  # 下标越界
        "/characters/0/__proto__",  # 原型污染
        "/characters/0/nope",  # 新建字段
        "/characters/-/name",  # 追加语义，不支持
        "characters/0/name",  # 不是合法的 JSON Pointer
    ],
)
async def test_illegal_paths_are_422(alice: AsyncClient, path: str) -> None:
    pid = await run_to_storyboard(alice)
    code, body = await patch(alice, pid, "characters", [{"path": path, "value": "x"}])
    assert code == 422, body


async def test_pollution_inside_the_value_is_422(alice: AsyncClient) -> None:
    pid = await run_to_storyboard(alice)
    code, body = await patch(
        alice,
        pid,
        "characters",
        [{"path": "/characters/0", "value": {"__proto__": {"isAdmin": True}}}],
    )
    assert code == 422, body


async def test_overlapping_paths_in_one_batch_are_422(alice: AsyncClient) -> None:
    """同时改 /characters/0 和 /characters/0/name：结果取决于哪条先写，
    撤销时"当时的旧值"也说不清是哪一份。"""
    pid = await run_to_storyboard(alice)
    code, body = await patch(
        alice,
        pid,
        "characters",
        [
            {"path": "/characters/0/name", "value": "陈默"},
            {"path": "/characters/0/name", "value": "老陈"},
        ],
    )
    assert code == 422, body


async def test_noop_batch_is_422(alice: AsyncClient) -> None:
    """全是"改成原值"不能静默成功：界面会显示"已保存"，
    而历史里什么都查不到。"""
    pid = await run_to_storyboard(alice)
    current = (await _characters(alice, pid))[0]["name"]
    code, body = await patch(
        alice, pid, "characters", [{"path": "/characters/0/name", "value": current}]
    )
    assert code == 422, body


async def test_empty_patch_list_is_422(alice: AsyncClient) -> None:
    r = await alice.patch(
        f"{P}/{uuid.uuid4()}/outputs/characters",
        json={"patches": []},
    )
    assert r.status_code == 422


async def test_non_editable_role_is_422(alice: AsyncClient) -> None:
    """routing 是调度决策不是内容。改路线该重跑，不是改字段。"""
    pid = await run_to_storyboard(alice)
    code, body = await patch(alice, pid, "routing", [{"path": "/route", "value": "CUSTOM"}])
    assert code == 422, body


async def test_role_without_output_is_404(alice: AsyncClient) -> None:
    pid = str((await alice.post(P, json={"title": "空项目"})).json()["id"])
    code, body = await patch(
        alice, pid, "characters", [{"path": "/characters/0/name", "value": "x"}]
    )
    assert code == 404, body


# ---------------------------------------------------------------- 租户隔离


async def test_cross_tenant_patch_is_404(alice: AsyncClient, bob: AsyncClient) -> None:
    """404 不是 403：403 会确认这个项目存在，可枚举。"""
    pid = await run_to_storyboard(alice)
    before = await _characters(alice, pid)

    code, body = await patch(
        bob, pid, "characters", [{"path": "/characters/0/name", "value": "被别人改了"}]
    )
    assert code == 404, body
    assert await _characters(alice, pid) == before


async def test_cross_tenant_revision_list_is_404(alice: AsyncClient, bob: AsyncClient) -> None:
    pid = await run_to_storyboard(alice)
    await patch(alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默"}])

    r = await bob.get(f"{P}/{pid}/revisions")
    assert r.status_code == 404, r.text
