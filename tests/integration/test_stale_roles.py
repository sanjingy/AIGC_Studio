"""过期记账：上游被改过之后，下游产出还停留在旧版本。

原来这份信息只在 `POST /revise` 的那一次响应里，刷新页面就没了——
用户改完剧本、关掉页面，隔天回来看到角色和场景都在，会以为已经同步过了。
所以它必须存进 `current_state_json` 并从项目响应读得到（ADR-008）。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

P = "/api/v1/projects"

NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名侦探。"


async def _with_story(client: AsyncClient) -> str:
    pid = str((await client.post(P, json={"title": "stale"})).json()["id"])
    await client.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": NOVEL})
    return pid


async def _pending(client: AsyncClient, pid: str) -> dict | None:
    rows = (await client.get(f"{P}/{pid}/approvals")).json()
    return next((dict(r) for r in rows if r["status"] == "pending"), None)


async def _produce_all(client: AsyncClient, pid: str) -> None:
    """通过剧本门，一路跑到分镜门——五个生产阶段全部有产出。

    停在分镜门上（不通过它），后面的用例还要用它来测"打回重做"。
    """
    gate = await _pending(client, pid)
    assert gate is not None, "剧本门应该已经开了"
    await client.post(f"{P}/{pid}/approvals/{gate['id']}", json={"decision": "approved"})
    await client.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": ""})


async def _stale(client: AsyncClient, pid: str) -> list[str]:
    return list((await client.get(f"{P}/{pid}")).json()["stale_roles"])


async def _revise(client: AsyncClient, pid: str, role: str, text: str) -> dict:
    r = await client.post(f"{P}/{pid}/revise", json={"target_role": role, "instruction": text})
    assert r.status_code == 200, r.text
    return dict(r.json())


async def test_stale_roles_survive_a_reload(alice: AsyncClient) -> None:
    """改了剧本，下游三个阶段过期——而且下次读项目还能读到。"""
    pid = await _with_story(alice)
    await _produce_all(alice, pid)

    assert await _stale(alice, pid) == []

    body = await _revise(alice, pid, "screenplay", "换个结局")
    assert body["stale_roles"] == ["characters", "scenes", "storyboard"]

    # 关键的一条：不是只在那一次响应里，重新 GET 也读得到
    assert await _stale(alice, pid) == ["characters", "scenes", "storyboard"]


async def test_revising_a_stale_role_clears_its_own_mark(alice: AsyncClient) -> None:
    """同步过的那个阶段自己变新鲜，它的下游接着过期。"""
    pid = await _with_story(alice)
    await _produce_all(alice, pid)
    await _revise(alice, pid, "screenplay", "换个结局")
    assert await _stale(alice, pid) == ["characters", "scenes", "storyboard"]

    await _revise(alice, pid, "characters", "请根据最新的上游设定同步更新这部分内容")

    # characters 刚改过，是新鲜的；它的下游仍然停在旧版
    assert await _stale(alice, pid) == ["scenes", "storyboard"]


async def test_last_stage_revision_leaves_nothing_stale(alice: AsyncClient) -> None:
    pid = await _with_story(alice)
    await _produce_all(alice, pid)
    await _revise(alice, pid, "screenplay", "换个结局")

    for role in ("characters", "scenes", "storyboard"):
        await _revise(alice, pid, role, "按最新设定同步")

    assert await _stale(alice, pid) == []


async def test_advance_does_not_mark_what_it_just_produced(alice: AsyncClient) -> None:
    """正向产出永远是新鲜的。

    推进本身不该把刚跑出来的这一版标成过期——那会让每个项目一生成完
    就顶着一排"建议同步"，提示立刻失去意义。
    """
    pid = await _with_story(alice)
    assert await _stale(alice, pid) == []

    await _produce_all(alice, pid)
    assert await _stale(alice, pid) == []


async def test_advance_refreshes_a_stale_stage(alice: AsyncClient) -> None:
    """打回重做跑出的新版同样是新鲜的，记账要跟着划掉。"""
    pid = await _with_story(alice)
    await _produce_all(alice, pid)
    await _revise(alice, pid, "scenes", "把码头改成雨夜")
    assert await _stale(alice, pid) == ["storyboard"]

    # 分镜门打回 → 退回 storyboard 重做 → 重新产出
    gate = await _pending(alice, pid)
    assert gate is not None, "分镜门应该开着"
    await alice.post(
        f"{P}/{pid}/approvals/{gate['id']}",
        json={"decision": "changes_requested", "comment": "重做"},
    )
    await alice.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": ""})

    assert await _stale(alice, pid) == []


async def test_project_list_exposes_stale_roles(alice: AsyncClient) -> None:
    """列表页也要看得见，否则用户得逐个点进去才知道哪个项目待同步。"""
    pid = await _with_story(alice)
    await _produce_all(alice, pid)
    await _revise(alice, pid, "screenplay", "换个结局")

    items = (await alice.get(f"{P}?limit=50")).json()["items"]
    row = next(p for p in items if p["id"] == pid)
    assert row["stale_roles"] == ["characters", "scenes", "storyboard"]
