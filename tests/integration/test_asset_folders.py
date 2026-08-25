"""资产库文件夹：用户自建的分类。

四件事在这里被守住：

1. **"全部"视图不变。** 加了文件夹之后，不带 folder_id 的资产库仍然是
   原来那份（按项目分组的档案 + 分页的资产），只是每条多带一个 folder_id 标签。
2. **删文件夹不删东西。** 用户点的是"删除文件夹"，不是"删除这些资产"。
3. **归类要重新验证归属。** 文件夹只决定"在哪个抽屉里"，不能成为越权的入口。
4. **跨租户/跨用户一律 404，不是 403。** 403 等于确认这个 ID 存在。
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import AsyncClient

from tests.conftest import new_client

pytestmark = pytest.mark.integration

A = "/api/v1/assets"
P = "/api/v1/projects"


async def _me(client: AsyncClient) -> dict:
    return (await client.get("/api/v1/auth/me")).json()


async def _project(client: AsyncClient, title: str) -> str:
    r = await client.post(P, json={"title": title})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _folder(client: AsyncClient, name: str) -> dict:
    r = await client.post(f"{A}/folders", json={"name": name})
    assert r.status_code == 201, r.text
    return dict(r.json())


async def _ready_asset(client: AsyncClient, filename: str = "a.png") -> str:
    """走完整的三段式直传，拿到一个 ready 的资产。

    资产库只列 ready 的（pending 只是预留了配额，文件还在路上），
    所以这里不能图省事只签票——那样列表里根本看不到它。
    """
    body = filename.encode() + b"-bytes"
    r = await client.post(
        f"{A}/upload-url",
        json={"filename": filename, "mime_type": "image/png", "size_bytes": len(body)},
    )
    assert r.status_code == 201, r.text
    ticket = r.json()
    async with httpx.AsyncClient(timeout=30) as raw:
        put = await raw.put(
            ticket["upload_url"], content=body, headers={"Content-Type": "image/png"}
        )
    assert put.status_code in (200, 204), put.text
    done = await client.post(f"{A}/{ticket['asset']['id']}/complete")
    assert done.status_code == 200, done.text
    return str(ticket["asset"]["id"])


async def _seed_profile_run(*, org_id: str, project_id: str, agent_id: str, output: dict) -> str:
    """直接塞一条成功的 agent_run，返回 run_id。

    走真实编排要打上游、要花钱，而这里验的是归类逻辑，不是 Agent 产出。
    """
    from apps.api.core.db import get_session_factory
    from apps.api.modules.agent.models import AgentRun

    async with get_session_factory()() as s:
        row = AgentRun(
            org_id=uuid.UUID(org_id),
            project_id=uuid.UUID(project_id),
            agent_id=agent_id,
            role="visual",
            status="succeeded",
            input_json={},
            output_json=output,
        )
        s.add(row)
        await s.commit()
        return str(row.id)


async def _classify(
    client: AsyncClient, *, item_type: str, item_id: str, folder_id: str | None
) -> int:
    r = await client.put(
        f"{A}/folder-items",
        json={"item_type": item_type, "item_id": item_id, "folder_id": folder_id},
    )
    return r.status_code


# ------------------------------------------------------------------ 增删改


async def test_create_and_list_folders(alice: AsyncClient) -> None:
    created = await _folder(alice, "参考图")
    assert created["name"] == "参考图"
    assert created["item_count"] == 0

    body = (await alice.get(f"{A}/library")).json()
    names = [f["name"] for f in body["folders"]]
    assert names == ["参考图"]


async def test_duplicate_name_rejected(alice: AsyncClient) -> None:
    await _folder(alice, "配角")
    r = await alice.post(f"{A}/folders", json={"name": "配角"})
    assert r.status_code == 409, r.text


async def test_name_whitespace_is_folded(alice: AsyncClient) -> None:
    """ " 参考  图 " 和 "参考 图" 是同一个名字，不该建出两个看起来一样的文件夹。"""
    await _folder(alice, "参考 图")
    r = await alice.post(f"{A}/folders", json={"name": "  参考   图  "})
    assert r.status_code == 409, r.text


async def test_rename_folder(alice: AsyncClient) -> None:
    folder = await _folder(alice, "旧名字")
    r = await alice.patch(f"{A}/folders/{folder['id']}", json={"name": "新名字"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "新名字"

    names = [f["name"] for f in (await alice.get(f"{A}/library")).json()["folders"]]
    assert names == ["新名字"]


async def test_deleted_name_can_be_reused(alice: AsyncClient) -> None:
    """软删的文件夹不该挡住同名新建——唯一索引带了 deleted_at IS NULL。"""
    folder = await _folder(alice, "临时")
    assert (await alice.delete(f"{A}/folders/{folder['id']}")).status_code == 204
    again = await alice.post(f"{A}/folders", json={"name": "临时"})
    assert again.status_code == 201, again.text


# ------------------------------------------------------------------ 归类


async def test_asset_moves_into_folder_and_shows_in_folder_view(alice: AsyncClient) -> None:
    folder = await _folder(alice, "参考图")
    inside = await _ready_asset(alice, "inside.png")
    outside = await _ready_asset(alice, "outside.png")

    assert await _classify(alice, item_type="asset", item_id=inside, folder_id=folder["id"]) == 204

    # 全部视图：两个都在，归了类的那个带 folder_id 标签
    all_view = (await alice.get(f"{A}/library")).json()
    by_id = {a["id"]: a for a in all_view["assets"]}
    assert by_id[inside]["folder_id"] == folder["id"]
    assert by_id[outside]["folder_id"] is None
    assert [f["item_count"] for f in all_view["folders"]] == [1]

    # 文件夹视图：只有归进去的那个
    folder_view = (await alice.get(f"{A}/library?folder_id={folder['id']}")).json()
    assert [a["id"] for a in folder_view["assets"]] == [inside]


async def test_moving_between_folders_does_not_duplicate(alice: AsyncClient) -> None:
    first = await _folder(alice, "甲")
    second = await _folder(alice, "乙")
    asset = await _ready_asset(alice)

    await _classify(alice, item_type="asset", item_id=asset, folder_id=first["id"])
    await _classify(alice, item_type="asset", item_id=asset, folder_id=second["id"])

    counts = {
        f["name"]: f["item_count"] for f in (await alice.get(f"{A}/library")).json()["folders"]
    }
    assert counts == {"甲": 0, "乙": 1}


async def test_unclassify_puts_item_back(alice: AsyncClient) -> None:
    folder = await _folder(alice, "参考图")
    asset = await _ready_asset(alice)
    await _classify(alice, item_type="asset", item_id=asset, folder_id=folder["id"])

    assert await _classify(alice, item_type="asset", item_id=asset, folder_id=None) == 204

    body = (await alice.get(f"{A}/library")).json()
    assert body["assets"][0]["folder_id"] is None
    assert body["folders"][0]["item_count"] == 0


async def test_profile_can_be_filed(alice: AsyncClient) -> None:
    """角色档案存在 agent_runs 里，同样能归类——归类关系是独立的一张表，
    不需要往别的模块的表上加列。"""
    me = await _me(alice)
    project = await _project(alice, "有档案的项目")
    run_id = await _seed_profile_run(
        org_id=me["org_id"],
        project_id=project,
        agent_id="visual.character.v1",
        output={"characters": [{"ref": "c1", "name": "阿哲"}]},
    )
    folder = await _folder(alice, "主角设定")

    assert (
        await _classify(alice, item_type="profile", item_id=run_id, folder_id=folder["id"]) == 204
    )

    view = (await alice.get(f"{A}/library?folder_id={folder['id']}")).json()
    assert [p["run_id"] for p in view["profiles"]] == [run_id]
    assert view["profiles"][0]["output"]["characters"][0]["name"] == "阿哲"


async def test_filed_older_revision_stays_visible_in_its_folder(alice: AsyncClient) -> None:
    """归类的可能正是某个旧版本。文件夹视图必须照实显示它，
    否则用户会发现"我明明放进去的东西不见了"。"""
    me = await _me(alice)
    project = await _project(alice, "改过两版")
    old_run = await _seed_profile_run(
        org_id=me["org_id"],
        project_id=project,
        agent_id="visual.character.v1",
        output={"characters": [{"ref": "c1", "name": "第一版"}]},
    )
    await _seed_profile_run(
        org_id=me["org_id"],
        project_id=project,
        agent_id="visual.character.v1",
        output={"characters": [{"ref": "c1", "name": "第二版"}]},
    )
    folder = await _folder(alice, "存档")
    await _classify(alice, item_type="profile", item_id=old_run, folder_id=folder["id"])

    # 全部视图只给最新一版（原有行为，不变）
    latest = [
        p
        for p in (await alice.get(f"{A}/library")).json()["profiles"]
        if p["project_id"] == project
    ]
    assert len(latest) == 1
    assert latest[0]["output"]["characters"][0]["name"] == "第二版"

    # 文件夹视图给的是当初归类的那一版
    filed = (await alice.get(f"{A}/library?folder_id={folder['id']}")).json()["profiles"]
    assert [p["output"]["characters"][0]["name"] for p in filed] == ["第一版"]


async def test_unknown_item_type_rejected(alice: AsyncClient) -> None:
    folder = await _folder(alice, "参考图")
    code = await _classify(
        alice, item_type="workflow", item_id=str(uuid.uuid4()), folder_id=folder["id"]
    )
    assert code == 422


async def test_non_profile_run_cannot_be_filed(alice: AsyncClient) -> None:
    """只有角色/场景档案能进资产库，别的 Agent 产出（比如路由决策）不行。"""
    me = await _me(alice)
    project = await _project(alice, "路由产出")
    run_id = await _seed_profile_run(
        org_id=me["org_id"],
        project_id=project,
        agent_id="router.default.v1",
        output={"route": "NOVEL_TO_ANIME"},
    )
    folder = await _folder(alice, "杂项")
    assert (
        await _classify(alice, item_type="profile", item_id=run_id, folder_id=folder["id"]) == 404
    )


# ------------------------------------------------------------------ 删除


async def test_deleting_folder_keeps_the_assets(alice: AsyncClient) -> None:
    folder = await _folder(alice, "待删")
    asset = await _ready_asset(alice)
    await _classify(alice, item_type="asset", item_id=asset, folder_id=folder["id"])

    assert (await alice.delete(f"{A}/folders/{folder['id']}")).status_code == 204

    body = (await alice.get(f"{A}/library")).json()
    assert body["folders"] == []
    # 资产还在，只是回到未分类
    assert [a["id"] for a in body["assets"]] == [asset]
    assert body["assets"][0]["folder_id"] is None
    assert (await alice.get(f"{A}/{asset}")).status_code == 200


# ------------------------------------------------------------------ 隔离


async def test_cross_tenant_folder_returns_404(alice: AsyncClient, bob: AsyncClient) -> None:
    folder = await _folder(alice, "alice 的文件夹")

    assert (
        await bob.patch(f"{A}/folders/{folder['id']}", json={"name": "抢过来"})
    ).status_code == 404
    assert (await bob.delete(f"{A}/folders/{folder['id']}")).status_code == 404
    # 别人的文件夹也不出现在自己的资产库里
    assert (await bob.get(f"{A}/library")).json()["folders"] == []


async def test_cannot_file_someone_elses_asset(alice: AsyncClient) -> None:
    """同租户的另一个人也不行：资产库是按人算的。"""
    other = await new_client("同租户的另一个人")
    try:
        foreign_asset = await _ready_asset(other, "别人的.png")
        folder = await _folder(alice, "参考图")
        code = await _classify(
            alice, item_type="asset", item_id=foreign_asset, folder_id=folder["id"]
        )
        assert code == 404
    finally:
        await other.aclose()


async def test_cannot_file_into_someone_elses_folder(alice: AsyncClient, bob: AsyncClient) -> None:
    folder = await _folder(alice, "alice 的文件夹")
    asset = await _ready_asset(bob, "bob.png")
    assert await _classify(bob, item_type="asset", item_id=asset, folder_id=folder["id"]) == 404


async def test_folders_require_auth(client: AsyncClient) -> None:
    assert (await client.get(f"{A}/folders")).status_code == 401
    assert (await client.post(f"{A}/folders", json={"name": "x"})).status_code == 401
