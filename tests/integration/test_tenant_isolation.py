"""跨租户隔离。

这组用例保护的是这个系统最贵的失败模式：一个用户看到另一个用户的项目、
素材或成片。发生一次就足以让产品失去信任，且往往在很久以后才被发现。

原则：**跨租户访问一律返回 404，不返回 403。**
403 等于确认了"这个 ID 确实存在"，可以被拿来枚举他人资源。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

P = "/api/v1/projects"
A = "/api/v1/assets"


async def _make_project(client: AsyncClient, title: str = "Alice 的项目") -> str:
    r = await client.post(P, json={"title": title})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _make_asset(client: AsyncClient, project_id: str | None = None) -> str:
    r = await client.post(
        f"{A}/upload-url",
        json={
            "filename": "a.png",
            "mime_type": "image/png",
            "size_bytes": 1024,
            **({"project_id": project_id} if project_id else {}),
        },
    )
    assert r.status_code == 201, r.text
    return str(r.json()["asset"]["id"])


# ------------------------------------------------------------------ 项目


async def test_project_not_visible_across_orgs(alice: AsyncClient, bob: AsyncClient) -> None:
    pid = await _make_project(alice)

    assert (await bob.get(f"{P}/{pid}")).status_code == 404
    assert bob_ids_exclude(await bob.get(P), pid)


async def test_project_not_editable_across_orgs(alice: AsyncClient, bob: AsyncClient) -> None:
    pid = await _make_project(alice)

    assert (await bob.patch(f"{P}/{pid}", json={"title": "被篡改"})).status_code == 404
    assert (await bob.delete(f"{P}/{pid}")).status_code == 404

    # 确认 Alice 的数据没被动过
    r = await alice.get(f"{P}/{pid}")
    assert r.status_code == 200
    assert r.json()["title"] == "Alice 的项目"


async def test_cross_org_returns_404_not_403(alice: AsyncClient, bob: AsyncClient) -> None:
    """404 而非 403——403 会确认资源存在，可被用来枚举。"""
    pid = await _make_project(alice)

    seen = await bob.get(f"{P}/{pid}")
    unseen = await bob.get(f"{P}/00000000-0000-0000-0000-000000000000")

    assert seen.status_code == unseen.status_code == 404
    assert seen.json()["error"]["code"] == unseen.json()["error"]["code"]


async def test_project_list_is_scoped(alice: AsyncClient, bob: AsyncClient) -> None:
    await _make_project(alice, "A1")
    await _make_project(alice, "A2")
    await _make_project(bob, "B1")

    a_titles = {p["title"] for p in (await alice.get(P)).json()["items"]}
    b_titles = {p["title"] for p in (await bob.get(P)).json()["items"]}

    assert {"A1", "A2"} <= a_titles
    assert "B1" not in a_titles
    assert "B1" in b_titles
    assert not ({"A1", "A2"} & b_titles)


# ------------------------------------------------------------------ 资产


async def test_asset_not_visible_across_orgs(alice: AsyncClient, bob: AsyncClient) -> None:
    aid = await _make_asset(alice)

    assert (await bob.get(f"{A}/{aid}")).status_code == 404
    assert (await bob.get(f"{A}/{aid}/download-url")).status_code == 404
    assert (await bob.post(f"{A}/{aid}/complete")).status_code == 404
    assert (await bob.delete(f"{A}/{aid}")).status_code == 404


async def test_cannot_attach_asset_to_foreign_project(alice: AsyncClient, bob: AsyncClient) -> None:
    """不能把自己的素材挂到别人的项目上。"""
    alice_project = await _make_project(alice)

    r = await bob.post(
        f"{A}/upload-url",
        json={
            "filename": "x.png",
            "mime_type": "image/png",
            "size_bytes": 100,
            "project_id": alice_project,
        },
    )
    assert r.status_code == 404


async def test_storage_key_is_namespaced_by_org(
    alice: AsyncClient, bob: AsyncClient, db: object
) -> None:
    """对象键必须带 org_id 前缀——即便签名逻辑出问题也越不出本租户目录。"""
    from sqlalchemy import select

    from apps.api.modules.asset.models import Asset

    a_id = await _make_asset(alice)
    b_id = await _make_asset(bob)

    rows = {
        str(r.id): r
        for r in (await db.execute(select(Asset).where(Asset.id.in_([a_id, b_id])))).scalars()  # type: ignore[attr-defined]
    }
    a, b = rows[a_id], rows[b_id]

    assert a.storage_key.startswith(f"{a.org_id}/")
    assert b.storage_key.startswith(f"{b.org_id}/")
    assert a.org_id != b.org_id
    assert not a.storage_key.startswith(f"{b.org_id}/")


def bob_ids_exclude(resp: object, pid: str) -> bool:
    items = resp.json()["items"]  # type: ignore[attr-defined]
    return all(p["id"] != pid for p in items)
