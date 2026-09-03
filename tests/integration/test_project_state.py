"""`GET /projects/{id}/state`：编排状态的读接口。

这条接口补的是一个**结构性缺口**，不是一个新功能：`current_state_json`
是 ADR-008 里"状态的唯一权威"，但在此之前它没有任何 GET 接口——阶段和五份
阶段产出只在 `advance` / 审核决议那一次响应里出现过，刷新就没了。

后果有两条，都在前端：
1. 界面只能从 `agent_runs` 反推当前阶段；
2. ADR-029 的字段级 Patch 写进 `current_state_json`，而界面读 `agent_runs`
   ——保存成功、刷新回旧值，等于那条写路径接不上。

所以这里测的重点不是"能返回 200"，而是**它返回的确实是编排器写下的那一份**，
以及跨租户读不到。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.project import service as project_service

pytestmark = pytest.mark.integration


async def _new_project(client: AsyncClient, title: str = "状态读取") -> dict[str, Any]:
    resp = await client.post("/api/v1/projects", json={"title": title})
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


def _url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/state"


async def _write_state(db: AsyncSession, project: dict[str, Any], state: dict[str, Any]) -> None:
    """直接写库，不跑 advance——advance 会真的调模型。

    这里要验的是读路径，生产链路本身在 `test_agent_orchestration.py` 里。
    `update_current_state` 有意不 commit（字段级编辑要和记账同事务），
    所以测试自己提交。
    """
    await project_service.update_current_state(
        db,
        org_id=uuid.UUID(project["org_id"]),
        project_id=uuid.UUID(project["id"]),
        state=state,
    )
    await db.commit()


async def test_new_project_starts_at_routing(alice: AsyncClient) -> None:
    """刚建的项目 `current_state_json` 是空的，阶段必须是 routing。

    这一条钉的是**默认值由后端给**：`current_stage` 在读不到 `stage` 键时
    返回 `"routing"`。前端不该自己兜这个底——两边各兜一次，改阶段图时
    必然只改一处。
    """
    project = await _new_project(alice)

    resp = await alice.get(_url(project["id"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["project_id"] == project["id"]
    assert body["stage"] == "routing"
    assert body["current_state_json"] == {}
    assert body["stale_roles"] == []
    assert body["updated_at"]


async def test_state_returns_what_orchestrator_wrote(alice: AsyncClient, db: AsyncSession) -> None:
    """编排器写进 `current_state_json` 的东西，这条接口要原样读得回来。

    直接改库而不是跑一遍 advance：advance 会真的调模型。这里要验证的是
    读路径，不是生产链路本身——那条在 `test_agent_orchestration.py` 里。
    """
    project = await _new_project(alice)
    written = {
        "stage": "storyboard",
        "source": "小说原文若干",
        "plot_index": {"logline": "一句话"},
        "screenplay": {"title": "剧本"},
        "characters": {"characters": [{"ref": "c1", "name": "阿明"}]},
        "scenes": {"scenes": [{"ref": "s1", "name": "旧巷"}]},
        "stale_roles": ["characters"],
    }
    await _write_state(db, project, written)

    resp = await alice.get(_url(project["id"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["stage"] == "storyboard"
    # 整份透出，不裁剪：裁剪等于在这条接口里再定义一次"哪些键算产出"
    assert body["current_state_json"] == written
    assert body["stale_roles"] == ["characters"]


async def test_legacy_stage_name_is_translated(alice: AsyncClient, db: AsyncSession) -> None:
    """存量项目的旧阶段名要翻成现行枚举，不能原样抛出去。

    库里 2026-08-18 之前的项目 `stage` 还是 `story` / `visual`，这两个值在
    现行 `_NEXT` 里根本不存在。原样返回等于把一个前端解释不了的字符串
    交出去——它会落到"未知阶段"上，而这个项目其实是可以继续跑的。
    """
    project = await _new_project(alice)
    await _write_state(db, project, {"stage": "story"})

    body = (await alice.get(_url(project["id"]))).json()
    assert body["stage"] == "plot_index"
    # 翻译只发生在返回值上，库里那份不动——改库是迁移的事，不是读接口的事
    assert body["current_state_json"]["stage"] == "story"


async def test_stale_roles_drops_roles_without_output(alice: AsyncClient, db: AsyncSession) -> None:
    """过期记账里那些其实没有产出的阶段不该出现。

    `stale_roles` 是记账不是阶段图：某个阶段被标过期、之后整块产出又被
    重写掉，记账会留下一个指向不存在产出的名字。透出它会让界面上出现
    一条点不开的"上游已变"。
    """
    project = await _new_project(alice)
    # screenplay 被标了过期，但 state 里根本没有 screenplay 这一块
    await _write_state(
        db, project, {"stage": "characters", "plot_index": {}, "stale_roles": ["screenplay"]}
    )

    body = (await alice.get(_url(project["id"]))).json()
    assert body["stale_roles"] == []


async def test_cross_tenant_returns_404(alice: AsyncClient, bob: AsyncClient) -> None:
    """别人的项目读不到，且是 404 不是 403。

    403 会确认"这个 id 存在"，项目 id 就成了可枚举的。这条和
    `test_tenant_isolation.py` 里其余端点的约定是同一条。
    """
    project = await _new_project(alice, "Alice 的项目")

    resp = await bob.get(_url(project["id"]))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "common.not_found"


async def test_requires_auth(client: AsyncClient) -> None:
    """没登录读不到。`current_state_json` 里有用户上传的小说原文。"""
    resp = await client.get(_url(str(uuid.uuid4())))
    assert resp.status_code == 401
