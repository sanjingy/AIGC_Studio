"""把一张已有资产钉成角色立绘 / 场景参考图。

这条路径存在的理由：基准图不一定要生成。用户可能已经有一张满意的图
（自己画的、别的项目搬过来的、刚从本地传上来的），他要的是"就用这张"。

和 `test_image_generation.py` 的分工很清楚：那边验的是"生成一张"这条
花钱的路，这边验的是**不花钱**——所以每个用例都在断言反面：没有任务、
没有流水、余额一分不动。这些断言看着像凑数，但它们正是这条路径的全部
风险所在：只要哪天有人把它接回 `create_task`，用户就会为自己的图付费。
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from apps.api.core.db import session_scope
from apps.api.modules.billing.models import CreditAccount, CreditTransaction
from apps.api.modules.consistency import service as consistency
from apps.api.modules.task import service as task_service
from apps.api.modules.task.models import Task
from tests.conftest import advance_to_gate

pytestmark = pytest.mark.integration

P = "/api/v1/projects"
A = "/api/v1/assets"

NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名被调职的刑警。"

PNG = b"\x89PNG\r\n\x1a\n-not-really-a-png-but-bytes-are-bytes"


@pytest.fixture(autouse=True)
def _no_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """掐掉入队，理由与 `test_image_generation.py` 一字不差。

    这里其实一个出图任务都不该建——但有一个用例会先真的生成一次，
    好验证"钉过之后再生成，界面上是新的那张"。那次生成不能真的跑到
    Worker 去打万相。
    """

    async def _noop(task_id: uuid.UUID) -> None:
        del task_id

    monkeypatch.setattr(task_service, "_enqueue", _noop)


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


async def _run_to_storyboard(client: AsyncClient) -> str:
    """跑到分镜门：角色档案、场景档案都已产出。

    途中每一道门由 `advance_to_gate` 通过，这里不写死门的道数——
    ADR-037 把门从 2 道加到 4 道时，写死道数的助手全部一起变红。
    """
    pid = str((await client.post(P, json={"title": "base-image"})).json()["id"])
    await advance_to_gate(client, pid, "storyboard", user_input=NOVEL)
    return pid


async def _upload(
    client: AsyncClient,
    *,
    filename: str = "my-portrait.png",
    mime_type: str = "image/png",
    body: bytes = PNG,
    complete: bool = True,
) -> str:
    """走完整的三段式直传，返回 asset_id。

    真的把字节 PUT 到 MinIO：这条路径的入参就是"一份已经传好的资产"，
    用假的记录测等于没测过 `complete` 那一步到底放不放行。
    """
    r = await client.post(
        f"{A}/upload-url",
        json={"filename": filename, "mime_type": mime_type, "size_bytes": len(body)},
    )
    assert r.status_code == 201, r.text
    ticket = r.json()

    async with httpx.AsyncClient(timeout=30) as raw:
        put = await raw.put(ticket["upload_url"], content=body, headers={"Content-Type": mime_type})
    assert put.status_code in (200, 204), put.text

    asset_id = str(ticket["asset"]["id"])
    if complete:
        done = await client.post(f"{A}/{asset_id}/complete")
        assert done.status_code == 200, done.text

    return asset_id


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


async def _ledger_count(org_id: uuid.UUID) -> int:
    """这个租户账上一共有多少条流水。

    RESERVE 也算——它 amount 为 0，但它是"预扣发生过"的唯一证据，
    正是这里要证明**没有**发生的那件事。
    """
    async with session_scope() as db:
        return int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(CreditTransaction)
                    .join(CreditAccount, CreditAccount.id == CreditTransaction.account_id)
                    .where(CreditAccount.org_id == org_id)
                )
            ).scalar_one()
        )


async def _balance(client: AsyncClient) -> dict[str, int]:
    return dict((await client.get("/api/v1/credits/balance")).json())


# ---------------------------------------------------------------- 角色


async def test_assign_character_portrait_writes_base_asset(alice: AsyncClient) -> None:
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)
    asset_id = await _upload(alice)

    r = await alice.put(f"{P}/{pid}/images/characters/zhu_jue", json={"asset_id": asset_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subject_kind"] == "character"
    assert body["subject_ref"] == "zhu_jue"
    assert body["asset_id"] == asset_id

    async with session_scope() as db:
        characters = await consistency.list_characters(db, org_id=org_id, project_id=uuid.UUID(pid))
    zhu = next(c for c in characters if c.ref == "zhu_jue")
    assert str(zhu.base_portrait_asset_id) == asset_id, "基准立绘没真的写进档案"


async def test_assign_costs_nothing(alice: AsyncClient) -> None:
    """这条路径不调任何 Provider，所以不该有任务、不该有流水、余额不动。

    这是整个功能最重要的一条断言：用户给的是自己的图，为它收钱是纯粹的
    错误，而且是那种没人会报 bug、只会默默流失的错误。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)
    asset_id = await _upload(alice)

    tasks_before = await _task_count(pid)
    ledger_before = await _ledger_count(org_id)
    balance_before = await _balance(alice)

    r = await alice.put(f"{P}/{pid}/images/characters/zhu_jue", json={"asset_id": asset_id})
    assert r.status_code == 200, r.text
    r = await alice.put(f"{P}/{pid}/images/scenes/gate", json={"asset_id": asset_id})
    assert r.status_code == 200, r.text

    assert await _task_count(pid) == tasks_before, "钉一张已有的图不该建任务"
    assert await _ledger_count(org_id) == ledger_before, "钉一张已有的图不该产生任何流水"
    assert await _balance(alice) == balance_before, "余额和预扣都不该动"


async def test_assign_is_idempotent(alice: AsyncClient) -> None:
    """PUT 就是 PUT：同一个 asset_id 提交两次，结果和提交一次一样。

    这正是这条路径用 PUT 不用 POST 的理由——POST 到出图端点两次会扣
    两次钱，而"把基准图设成它"重复执行必须是安全的。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)
    asset_id = await _upload(alice)
    ledger_before = await _ledger_count(org_id)

    first = await alice.put(f"{P}/{pid}/images/characters/zhu_jue", json={"asset_id": asset_id})
    second = await alice.put(f"{P}/{pid}/images/characters/zhu_jue", json={"asset_id": asset_id})

    assert first.status_code == second.status_code == 200
    assert first.json()["asset_id"] == second.json()["asset_id"] == asset_id
    assert await _ledger_count(org_id) == ledger_before


async def test_reassign_replaces_and_keeps_old_asset(alice: AsyncClient) -> None:
    """换一张基准图，旧的那张资产**不删**——用户很可能想换回去。"""
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)
    first_id = await _upload(alice, filename="first.png")
    second_id = await _upload(alice, filename="second.png", body=PNG + b"-2")

    await alice.put(f"{P}/{pid}/images/characters/zhu_jue", json={"asset_id": first_id})
    r = await alice.put(f"{P}/{pid}/images/characters/zhu_jue", json={"asset_id": second_id})
    assert r.status_code == 200, r.text

    async with session_scope() as db:
        characters = await consistency.list_characters(db, org_id=org_id, project_id=uuid.UUID(pid))
        zhu = next(c for c in characters if c.ref == "zhu_jue")
        assert str(zhu.base_portrait_asset_id) == second_id
        # 换一张图不是"角色设定改了"，版本不该动——动了会让所有已生成的
        # 镜头凭空变成"过期版本"
        assert zhu.version == 1

    still_there = await alice.get(f"{A}/{first_id}")
    assert still_there.status_code == 200, "旧的基准图被删了"


async def test_unknown_character_ref_is_404(alice: AsyncClient) -> None:
    pid = await _run_to_storyboard(alice)
    asset_id = await _upload(alice)
    r = await alice.put(f"{P}/{pid}/images/characters/nobody", json={"asset_id": asset_id})
    assert r.status_code == 404


# ---------------------------------------------------------------- 场景


async def test_assign_scene_reference_writes_base_asset(alice: AsyncClient) -> None:
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)
    asset_id = await _upload(alice, filename="gate.png")

    r = await alice.put(f"{P}/{pid}/images/scenes/gate", json={"asset_id": asset_id})
    assert r.status_code == 200, r.text
    assert r.json()["subject_kind"] == "scene"

    async with session_scope() as db:
        scenes = await consistency.list_scenes(db, org_id=org_id, project_id=uuid.UUID(pid))
    gate = next(s for s in scenes if s.ref == "gate")
    assert str(gate.base_reference_asset_id) == asset_id


async def test_unknown_scene_ref_is_404(alice: AsyncClient) -> None:
    pid = await _run_to_storyboard(alice)
    asset_id = await _upload(alice)
    r = await alice.put(f"{P}/{pid}/images/scenes/nowhere", json={"asset_id": asset_id})
    assert r.status_code == 404


async def test_assign_before_stage_explains_itself(alice: AsyncClient) -> None:
    """还没跑到角色那一步就要钉基准图，给的错误必须说清楚缺哪一步。"""
    pid = str((await alice.post(P, json={"title": "empty"})).json()["id"])
    asset_id = await _upload(alice)
    r = await alice.put(f"{P}/{pid}/images/characters/zhu_jue", json={"asset_id": asset_id})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "consistency.profile.missing"


# ---------------------------------------------------------------- 资产校验


async def test_asset_from_another_org_is_404(alice: AsyncClient, bob: AsyncClient) -> None:
    """拿别人的 asset_id 去钉自己的角色，必须 404 不是 403。

    403 会确认那个 id 真的存在，等于给了一个可枚举的探测口。
    """
    pid = await _run_to_storyboard(alice)
    bobs_asset = await _upload(bob, filename="bobs.png")

    r = await alice.put(f"{P}/{pid}/images/characters/zhu_jue", json={"asset_id": bobs_asset})
    assert r.status_code == 404, r.text

    org_id = await _org(alice)
    async with session_scope() as db:
        characters = await consistency.list_characters(db, org_id=org_id, project_id=uuid.UUID(pid))
    zhu = next(c for c in characters if c.ref == "zhu_jue")
    assert zhu.base_portrait_asset_id is None, "越权的 asset 竟然写进去了"


async def test_non_image_asset_is_400(alice: AsyncClient) -> None:
    """基准图必须是图片。传一份小说 txt 上去要被明确拒绝，不是 404。

    那份资产确实存在、确实是他自己的，只是不合用——报 404 他会以为
    自己选错了文件，回去再选一遍还是同一个。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)
    txt_id = await _upload(alice, filename="novel.txt", mime_type="text/plain", body=b"hello")

    r = await alice.put(f"{P}/{pid}/images/characters/zhu_jue", json={"asset_id": txt_id})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "consistency.base_image.invalid"
    assert r.json()["error"]["detail"]["reason"] == "not_image"

    async with session_scope() as db:
        characters = await consistency.list_characters(db, org_id=org_id, project_id=uuid.UUID(pid))
    assert next(c for c in characters if c.ref == "zhu_jue").base_portrait_asset_id is None


async def test_pending_asset_is_400(alice: AsyncClient) -> None:
    """没走完 complete 的资产不能当基准图：桶里可能一个字节都没有。"""
    pid = await _run_to_storyboard(alice)
    pending_id = await _upload(alice, complete=False)

    r = await alice.put(f"{P}/{pid}/images/characters/zhu_jue", json={"asset_id": pending_id})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["detail"]["reason"] == "not_ready"


# ---------------------------------------------------------------- 列表投影


async def test_assigned_image_shows_up_in_render_list(alice: AsyncClient) -> None:
    """钉完之后，出图列表里要能看见它——否则界面上等于什么都没发生。

    它没有 task_id（没有任务），`source` 是 assigned，前端靠这两样
    决定不给它画"重试"按钮。
    """
    pid = await _run_to_storyboard(alice)
    asset_id = await _upload(alice)
    await alice.put(f"{P}/{pid}/images/characters/zhu_jue", json={"asset_id": asset_id})

    rows = (await alice.get(f"{P}/{pid}/images")).json()
    mine = [r for r in rows if r["subject_kind"] == "character" and r["subject_ref"] == "zhu_jue"]
    assert mine, "钉上的基准图没出现在出图列表里"
    assert mine[0]["source"] == "assigned"
    assert mine[0]["task_id"] is None
    assert mine[0]["asset_id"] == asset_id
    assert mine[0]["status"] == "succeeded"


async def test_generation_after_assign_is_the_newer_one(alice: AsyncClient) -> None:
    """先钉一张，再点「重新生成」，列表第一条必须是那次生成。

    前端"取第一条就是当前这一版"的约定要在两个来源合并之后仍然成立，
    否则用户点了生成会看见旧图，以为没生效又点一次——那一次是真花钱的。
    """
    pid = await _run_to_storyboard(alice)
    asset_id = await _upload(alice)
    await alice.put(f"{P}/{pid}/images/characters/zhu_jue", json={"asset_id": asset_id})

    r = await alice.post(f"{P}/{pid}/images/characters/zhu_jue")
    assert r.status_code == 201, r.text

    rows = (await alice.get(f"{P}/{pid}/images")).json()
    mine = [r for r in rows if r["subject_kind"] == "character" and r["subject_ref"] == "zhu_jue"]
    assert len(mine) == 2
    assert mine[0]["source"] == "generated", "刚生成的那次没排在钉的那张前面"
    assert mine[1]["source"] == "assigned"
