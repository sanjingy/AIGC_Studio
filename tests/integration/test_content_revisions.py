"""变更历史与按批撤销。

`content_revisions` 不是日志的美化版：`POST /revisions/{batch_id}/undo`
直接把这些行反过来重放，所以 `old_value` / `new_value` 是**可执行的数据**。
写坏它等于写坏撤销，所以这里逐条验它们是不是真的能原样退回去。

最要紧的一组用例是**冲突**：同一批只能在"当前值仍等于当时写下的新值"
时才安全可撤。否则退回去就是把后来那次改动无声地抹掉——用户会看到一个
自己从没做过的值，且找不到是谁干的。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.integration.test_content_patch import P, patch, run_to_storyboard

pytestmark = pytest.mark.integration


async def _history(client: AsyncClient, pid: str, **params: str | int) -> list[dict]:
    r = await client.get(f"{P}/{pid}/revisions", params=params)
    assert r.status_code == 200, r.text
    return list(r.json()["items"])


async def _undo(
    client: AsyncClient, pid: str, batch_id: str, reason: str | None = None
) -> tuple[int, dict]:
    r = await client.post(
        f"{P}/{pid}/revisions/{batch_id}/undo",
        json={"reason": reason} if reason else {},
    )
    return r.status_code, dict(r.json())


async def _name(client: AsyncClient, pid: str, index: int = 0) -> str:
    """当前的角色名。借一次无害的 PATCH 拿返回值太贵，直接读历史端点不够，
    所以走产出本身——这里复用 patch 的返回值不成立，只能读库。"""
    from tests.integration.test_content_patch import _characters

    return str((await _characters(client, pid))[index]["name"])


# ---------------------------------------------------------------- 历史


async def test_history_is_grouped_by_batch_newest_first(alice: AsyncClient) -> None:
    pid = await run_to_storyboard(alice)
    await patch(alice, pid, "characters", [{"path": "/characters/0/name", "value": "一"}], "第一次")
    await patch(
        alice,
        pid,
        "characters",
        [
            {"path": "/characters/0/name", "value": "二"},
            {"path": "/characters/1/name", "value": "乙二"},
        ],
        "第二次",
    )

    items = await _history(alice, pid)
    assert [i["reason"] for i in items] == ["第二次", "第一次"]
    assert len(items[0]["changes"]) == 2
    assert len(items[1]["changes"]) == 1


async def test_history_records_who_what_and_from_to(alice: AsyncClient) -> None:
    me = (await alice.get("/api/v1/auth/me")).json()
    pid = await run_to_storyboard(alice)
    before = await _name(alice, pid)

    await patch(
        alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默"}], "定稿用这个名"
    )

    batch = (await _history(alice, pid))[0]
    assert batch["role"] == "characters"
    assert batch["source"] == "user_edit"
    assert batch["actor_user_id"] == me["id"]
    assert batch["reason"] == "定稿用这个名"
    assert batch["undone_by_batch_id"] is None

    change = batch["changes"][0]
    assert change["field_path"] == "/characters/0/name"
    assert change["old_value"] == before
    assert change["new_value"] == "陈默"


async def test_history_can_filter_by_role(alice: AsyncClient) -> None:
    pid = await run_to_storyboard(alice)
    await patch(alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默"}])
    await patch(
        alice,
        pid,
        "scenes",
        [{"path": "/scenes/0/lighting_states/0/description", "value": "顶灯惨白"}],
    )

    assert len(await _history(alice, pid)) == 2
    only = await _history(alice, pid, role="scenes")
    assert len(only) == 1
    assert only[0]["role"] == "scenes"


async def test_history_pages_by_batch_not_by_row(alice: AsyncClient) -> None:
    """按行截断会把一批劈成两页，用户看到一条"改了 2 处"而实际改了 5 处。"""
    pid = await run_to_storyboard(alice)
    for i in range(3):
        await patch(
            alice,
            pid,
            "characters",
            [
                {"path": "/characters/0/name", "value": f"甲{i}"},
                {"path": "/characters/1/name", "value": f"乙{i}"},
            ],
        )

    r = await alice.get(f"{P}/{pid}/revisions", params={"limit": 2})
    body = r.json()
    assert len(body["items"]) == 2
    assert all(len(i["changes"]) == 2 for i in body["items"]), "批被劈开了"
    assert body["next_cursor"]

    rest = await alice.get(
        f"{P}/{pid}/revisions", params={"limit": 2, "cursor": body["next_cursor"]}
    )
    assert len(rest.json()["items"]) == 1


# ---------------------------------------------------------------- 撤销


async def test_undo_restores_every_field_in_the_batch(alice: AsyncClient) -> None:
    pid = await run_to_storyboard(alice)
    from tests.integration.test_content_patch import _characters

    before = await _characters(alice, pid)

    _, body = await patch(
        alice,
        pid,
        "characters",
        [
            {"path": "/characters/0/name", "value": "陈默"},
            {"path": "/characters/1/hair", "value": "银白长发"},
        ],
    )
    batch_id = body["batch_id"]

    code, undone = await _undo(alice, pid, batch_id, reason="改错了")
    assert code == 200, undone
    assert undone["changed"] == 2

    assert await _characters(alice, pid) == before


async def test_undo_is_itself_auditable(alice: AsyncClient) -> None:
    """撤销是反向重放，不是删记录：它自己也进历史，谁撤的、撤的哪一批都查得到。"""
    pid = await run_to_storyboard(alice)
    _, body = await patch(
        alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默"}]
    )
    batch_id = body["batch_id"]

    code, undone = await _undo(alice, pid, batch_id, reason="改错了")
    assert code == 200

    items = await _history(alice, pid)
    assert len(items) == 2, "撤销本身也应该是一条历史"

    undo_batch, original = items[0], items[1]
    assert undo_batch["source"] == "undo"
    assert undo_batch["undoes_batch_id"] == batch_id
    assert undo_batch["reason"] == "改错了"
    assert undo_batch["batch_id"] == undone["batch_id"]

    # 被撤销的那一批带上标记，前端据此把"撤销"按钮置灰——
    # 不给这个字段，用户只能点下去吃一个 409
    assert original["undone_by_batch_id"] == undo_batch["batch_id"]


async def test_undo_can_be_undone(alice: AsyncClient) -> None:
    """撤销一次撤销就是重做。共用同一条写路径，不需要第二套代码。"""
    pid = await run_to_storyboard(alice)
    _, first = await patch(
        alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默"}]
    )

    code, undo = await _undo(alice, pid, first["batch_id"])
    assert code == 200
    assert await _name(alice, pid) != "陈默"

    code, redo = await _undo(alice, pid, undo["batch_id"])
    assert code == 200, redo
    assert await _name(alice, pid) == "陈默"


async def test_undoing_twice_is_409(alice: AsyncClient) -> None:
    pid = await run_to_storyboard(alice)
    _, body = await patch(
        alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默"}]
    )

    assert (await _undo(alice, pid, body["batch_id"]))[0] == 200
    code, err = await _undo(alice, pid, body["batch_id"])
    assert code == 409, err
    assert err["error"]["detail"]["undone_by_batch_id"]


async def test_undo_refuses_to_clobber_a_later_edit(alice: AsyncClient) -> None:
    """这一条是整个撤销设计的核心。

    改了 A，又改了 A，再撤销第一次——如果照旧值硬写回去，第二次改动就被
    无声抹掉了。用户会看到一个自己从没做过的值。所以整批 409，
    并把冲突字段告诉前端。
    """
    pid = await run_to_storyboard(alice)
    _, first = await patch(
        alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默"}]
    )
    await patch(alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默之"}])

    code, err = await _undo(alice, pid, first["batch_id"])
    assert code == 409, err
    assert err["error"]["detail"]["conflicts"] == ["/characters/0/name"]
    # 一个字都不该动
    assert await _name(alice, pid) == "陈默之"
    assert len(await _history(alice, pid)) == 2, "失败的撤销不该留下记录"


async def test_undo_is_all_or_nothing(alice: AsyncClient) -> None:
    """一批里只有一个字段被后来的改动碰过，整批也不撤。

    部分撤销出来的是一个既不是改前也不是改后的第三种状态，没有人要过它。
    """
    pid = await run_to_storyboard(alice)
    _, first = await patch(
        alice,
        pid,
        "characters",
        [
            {"path": "/characters/0/name", "value": "陈默"},
            {"path": "/characters/1/name", "value": "白馆长"},
        ],
    )
    await patch(alice, pid, "characters", [{"path": "/characters/1/name", "value": "白玉京"}])

    code, err = await _undo(alice, pid, first["batch_id"])
    assert code == 409, err
    assert await _name(alice, pid, 0) == "陈默", "没被碰过的那个字段也不该退回去"


async def test_undo_resyncs_the_consistency_projection(alice: AsyncClient) -> None:
    """撤销走的是同一条写路径，所以投影同步这一步不会被绕过。"""
    from apps.api.core.db import session_scope
    from apps.api.modules.consistency import service as consistency

    org_id = uuid.UUID((await alice.get("/api/v1/auth/me")).json()["org_id"])
    pid = await run_to_storyboard(alice)

    async with session_scope() as db:
        original = next(
            c
            for c in await consistency.list_characters(db, org_id=org_id, project_id=uuid.UUID(pid))
            if c.ref == "zhu_jue"
        ).appearance_json["hair"]

    _, body = await patch(
        alice, pid, "characters", [{"path": "/characters/0/hair", "value": "栗棕色短发"}]
    )
    assert (await _undo(alice, pid, body["batch_id"]))[0] == 200

    async with session_scope() as db:
        profiles = await consistency.list_characters(db, org_id=org_id, project_id=uuid.UUID(pid))
    assert next(c for c in profiles if c.ref == "zhu_jue").appearance_json["hair"] == original


async def test_undo_marks_downstream_stale_too(alice: AsyncClient) -> None:
    """撤销也是一次内容改动，下游同样要重新同步。"""
    pid = await run_to_storyboard(alice)
    _, body = await patch(
        alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默"}]
    )
    code, undone = await _undo(alice, pid, body["batch_id"])
    assert code == 200
    assert undone["stale_roles"] == ["scenes", "storyboard"]


# ---------------------------------------------------------------- 拒绝与隔离


async def test_unknown_batch_is_404(alice: AsyncClient) -> None:
    pid = await run_to_storyboard(alice)
    code, _ = await _undo(alice, pid, str(uuid.uuid4()))
    assert code == 404


async def test_cross_tenant_undo_is_404(alice: AsyncClient, bob: AsyncClient) -> None:
    """404 不是 403：403 会确认这个 batch_id 在别的租户下存在。"""
    pid = await run_to_storyboard(alice)
    _, body = await patch(
        alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默"}]
    )

    code, _ = await _undo(bob, pid, body["batch_id"])
    assert code == 404
    assert await _name(alice, pid) == "陈默"


async def test_undo_costs_nothing(alice: AsyncClient) -> None:
    from tests.integration.test_content_patch import _counts, _org

    org_id = await _org(alice)
    pid = await run_to_storyboard(alice)
    _, body = await patch(
        alice, pid, "characters", [{"path": "/characters/0/name", "value": "陈默"}]
    )

    before = await _counts(org_id, pid)
    assert (await _undo(alice, pid, body["batch_id"]))[0] == 200
    assert await _counts(org_id, pid) == before
