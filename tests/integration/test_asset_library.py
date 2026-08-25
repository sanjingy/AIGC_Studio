"""用户资产库：配额与聚合查询。

两件事在这里被守住：

1. **配额是账号级、跨项目的。** 按项目算等于"多建几个项目就能无限存"。
2. **超配额在签发直传 URL 之前就拒绝。** 等传完了才说存不下，
   用户已经把文件传完、平台已经付过上游的钱了。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import new_client

pytestmark = pytest.mark.integration

A = "/api/v1/assets"
P = "/api/v1/projects"

MIB = 1024 * 1024
QUOTA_BYTES = 1024 * MIB  # 迁移 a1f3c07b52d4 灌的种子值
# 单文件上限就是 512 MiB，所以两张票正好把配额顶满，第三张必被拒。
HALF_QUOTA = 512 * MIB


async def _ticket(client: AsyncClient, size: int, project_id: str | None = None) -> dict:
    payload: dict[str, object] = {
        "filename": "big.mp4",
        "mime_type": "video/mp4",
        "size_bytes": size,
    }
    if project_id:
        payload["project_id"] = project_id
    return (await client.post(f"{A}/upload-url", json=payload)).json()


async def _project(client: AsyncClient, title: str) -> str:
    r = await client.post(P, json={"title": title})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _me(client: AsyncClient) -> dict:
    return (await client.get("/api/v1/auth/me")).json()


# ------------------------------------------------------------------ 配额计算


async def test_quota_rule_is_seeded(alice: AsyncClient) -> None:
    """配额值必须真的在 pricing_rules 里。

    读不到规则时代码是**放行**的（缺一条限制的配置不该让上传链路瘫痪），
    所以种子数据丢失会静默地把配额关掉。这条用例是那个 fail-open 的兜底。
    """
    r = await alice.get(f"{A}/usage")
    assert r.status_code == 200, r.text
    assert r.json()["quota_bytes"] == QUOTA_BYTES


async def test_usage_starts_empty(alice: AsyncClient) -> None:
    body = (await alice.get(f"{A}/usage")).json()
    assert body["used_bytes"] == 0
    assert body["percent_used"] == 0
    assert body["free_bytes"] == QUOTA_BYTES


async def test_usage_aggregates_across_projects(alice: AsyncClient) -> None:
    """跨项目汇总，不是单项目。"""
    p1 = await _project(alice, "项目一")
    p2 = await _project(alice, "项目二")

    await _ticket(alice, 10 * MIB, p1)
    await _ticket(alice, 30 * MIB, p2)
    await _ticket(alice, 5 * MIB)  # 不挂任何项目的素材也要算进去

    body = (await alice.get(f"{A}/usage")).json()
    assert body["used_bytes"] == 45 * MIB
    assert body["free_bytes"] == QUOTA_BYTES - 45 * MIB


async def test_usage_is_per_user_not_per_org(alice: AsyncClient, bob: AsyncClient) -> None:
    await _ticket(alice, 20 * MIB)
    assert (await bob.get(f"{A}/usage")).json()["used_bytes"] == 0


async def test_deleted_assets_free_up_space(alice: AsyncClient) -> None:
    ticket = await _ticket(alice, 40 * MIB)
    assert (await alice.get(f"{A}/usage")).json()["used_bytes"] == 40 * MIB

    assert (await alice.delete(f"{A}/{ticket['asset']['id']}")).status_code == 204
    assert (await alice.get(f"{A}/usage")).json()["used_bytes"] == 0


# ------------------------------------------------------------------ 超配额拦截


async def test_over_quota_upload_rejected_without_side_effects(alice: AsyncClient) -> None:
    """第三张票必须被拒，且不能留下任何痕迹。

    "拒绝但已经建了记录"比不拒绝更糟——用量会被一堆永远传不上来的
    幽灵记录顶满，用户删都不知道该删什么。
    """
    for name in ("a.mp4", "b.mp4"):
        r = await alice.post(
            f"{A}/upload-url",
            json={"filename": name, "mime_type": "video/mp4", "size_bytes": HALF_QUOTA},
        )
        assert r.status_code == 201, r.text

    before = (await alice.get(f"{A}/usage")).json()
    assert before["used_bytes"] == QUOTA_BYTES
    assert before["percent_used"] == 100

    r = await alice.post(
        f"{A}/upload-url",
        json={"filename": "c.mp4", "mime_type": "video/mp4", "size_bytes": 1},
    )
    assert r.status_code == 413, r.text
    err = r.json()["error"]
    assert err["code"] == "asset.quota.exceeded"
    # 报错要说清楚"用了多少、上限多少"，只说"满了"用户不知道该删多少
    assert err["detail"]["quota_bytes"] == QUOTA_BYTES
    assert err["detail"]["used_bytes"] == QUOTA_BYTES
    assert err["user_message"]

    # 副作用检查：用量没变，也没有多出一条记录
    assert (await alice.get(f"{A}/usage")).json()["used_bytes"] == QUOTA_BYTES
    assert not [a for a in (await alice.get(A)).json()["items"] if a["filename"] == "c.mp4"]


async def test_pending_upload_reserves_space(alice: AsyncClient) -> None:
    """没 complete 的上传也占配额。

    不占的话，用户可以连签 100 张票同时开传，等 complete 时字节已经
    在桶里了——那时再拒绝，磁盘已经被写爆。
    """
    await _ticket(alice, HALF_QUOTA)
    body = (await alice.get(f"{A}/usage")).json()
    assert body["used_bytes"] == HALF_QUOTA
    assert body["percent_used"] == 50


# ------------------------------------------------------------------ 资产库查询


async def test_library_only_returns_own_assets(alice: AsyncClient, bob: AsyncClient) -> None:
    a_ticket = await _ticket(alice, 1024)
    b_ticket = await _ticket(bob, 1024)

    ids = {a["id"] for a in (await bob.get(f"{A}/library")).json()["assets"]}
    assert a_ticket["asset"]["id"] not in ids
    assert b_ticket["asset"]["id"] not in ids  # pending 的都不列


async def test_cross_tenant_asset_returns_404_not_403(alice: AsyncClient, bob: AsyncClient) -> None:
    ticket = await _ticket(alice, 1024)
    aid = ticket["asset"]["id"]

    # 403 会确认这个 ID 存在，可以被拿来枚举
    assert (await bob.get(f"{A}/{aid}")).status_code == 404
    assert (await bob.get(f"{A}/{aid}/download-url")).status_code == 404
    assert (await bob.delete(f"{A}/{aid}")).status_code == 404


async def test_library_requires_auth(client: AsyncClient) -> None:
    assert (await client.get(f"{A}/library")).status_code == 401
    assert (await client.get(f"{A}/usage")).status_code == 401


# ------------------------------------------------------------------ 角色 / 场景档案


async def _seed_profile_run(*, org_id: str, project_id: str, agent_id: str, output: dict) -> None:
    """直接塞一条成功的 agent_run。

    走真实编排要打上游、要花钱，且这条用例验的是"聚合按谁过滤"，
    不是"Agent 产出对不对"——后者由 eval 用例负责。
    """
    from apps.api.core.db import get_session_factory
    from apps.api.modules.agent.models import AgentRun

    async with get_session_factory()() as s:
        s.add(
            AgentRun(
                org_id=uuid.UUID(org_id),
                project_id=uuid.UUID(project_id),
                agent_id=agent_id,
                role="visual",
                status="succeeded",
                input_json={},
                output_json=output,
            )
        )
        await s.commit()


async def test_profiles_aggregate_across_projects_by_user(alice: AsyncClient) -> None:
    me = await _me(alice)
    p1 = await _project(alice, "档案项目一")
    p2 = await _project(alice, "档案项目二")

    await _seed_profile_run(
        org_id=me["org_id"],
        project_id=p1,
        agent_id="visual.character.v1",
        output={"characters": [{"ref": "C1", "name": "阿哲"}]},
    )
    await _seed_profile_run(
        org_id=me["org_id"],
        project_id=p2,
        agent_id="visual.scene.v1",
        output={"scenes": [{"ref": "S1", "name": "旧仓库"}]},
    )

    profiles = (await alice.get(f"{A}/library")).json()["profiles"]
    by_project = {(p["project_id"], p["kind"]): p for p in profiles}

    assert (p1, "characters") in by_project
    assert (p2, "scenes") in by_project
    assert by_project[(p1, "characters")]["project_title"] == "档案项目一"
    assert by_project[(p1, "characters")]["output"]["characters"][0]["name"] == "阿哲"


async def test_profiles_filtered_by_user_not_project(alice: AsyncClient) -> None:
    """同一个租户里别人的项目产出，不该出现在我的资产库里。

    这是按 owner_user_id 过滤而不是只按 org_id 过滤的意义所在——
    团队版上线后，org 里会有很多个人。
    """
    other = await new_client("同租户的另一个人")
    try:
        me = await _me(alice)
        other_me = await _me(other)
        foreign_project = await _project(other, "别人的项目")
        await _seed_profile_run(
            org_id=other_me["org_id"],
            project_id=foreign_project,
            agent_id="visual.character.v1",
            output={"characters": [{"ref": "X", "name": "不该出现"}]},
        )

        mine = await _project(alice, "我的项目")
        await _seed_profile_run(
            org_id=me["org_id"],
            project_id=mine,
            agent_id="visual.character.v1",
            output={"characters": [{"ref": "C1", "name": "该出现"}]},
        )

        profiles = (await alice.get(f"{A}/library")).json()["profiles"]
        project_ids = {p["project_id"] for p in profiles}
        assert mine in project_ids
        assert foreign_project not in project_ids
    finally:
        await other.aclose()


async def test_profiles_keep_only_latest_revision(alice: AsyncClient) -> None:
    """同一个项目改过三版角色，资产库只显示当前生效的那版。"""
    me = await _me(alice)
    pid = await _project(alice, "改了三版的项目")

    for name in ("第一版", "第二版", "第三版"):
        await _seed_profile_run(
            org_id=me["org_id"],
            project_id=pid,
            agent_id="visual.character.v1",
            output={"characters": [{"ref": "C1", "name": name}]},
        )

    entries = [
        p for p in (await alice.get(f"{A}/library")).json()["profiles"] if p["project_id"] == pid
    ]
    assert len(entries) == 1
    assert entries[0]["output"]["characters"][0]["name"] == "第三版"
