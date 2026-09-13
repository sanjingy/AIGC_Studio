"""「本机 Codex 出图」这条来源的闭环。

覆盖的是这条路径上真正会出事的七件事，每一件失败了都不会有任何报错：

1. 来源在**建任务时**钉进 `input_json`，重试也不会漂移；
2. 选了本机就**绝不**调用 Gateway（那是付费的，用户以为在用订阅额度）；
3. 本机不可用时**在建任务之前**就拒绝——不建任务、不预扣、不留失败记录；
4. 执行失败按既有资金规则释放预扣，余额一分不少；
5. 等待期间 org / project 归属不混：别人的租户拿同一个项目 id 也进不来；
6. 落库的必须是**真的图片字节**，路径、URL、一段文字一律不收；
7. 图片进的是同一个项目资产库（同一条 `register_generated`、同一套配额）。
"""

from __future__ import annotations

import base64
import io
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import func, select

from apps.api.core.config import Settings
from apps.api.core.db import session_scope
from apps.api.core.errors import ERRORS, AppError, Disposition
from apps.api.modules.asset.models import Asset
from apps.api.modules.billing.models import CreditAccount, CreditTransaction
from apps.api.modules.consistency import render
from apps.api.modules.local_runtime import service as lr_service
from apps.api.modules.local_runtime import transport
from apps.api.modules.task import service as task_service
from apps.api.modules.task.models import Task
from tests.conftest import advance_to_gate
from worker.jobs import generation
from worker.jobs.execute import execute_task

pytestmark = pytest.mark.integration

P = "/api/v1/projects"
NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名被调职的刑警。"

#: 一张**真的** PNG（Pillow 生成）。入库前服务端会真解一遍：
#: 魔数只有八个字节，"PNG 头 + 一坨随机数据"能轻松骗过它。


def _png(width: int = 64, height: int = 64) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (12, 34, 56)).save(buf, format="PNG")
    return buf.getvalue()


PNG = _png()

#: 头对内容坏。魔数挡不住，Pillow 能。
CORRUPT_PNG = PNG[:40] + bytes(200)


@pytest.fixture(autouse=True)
def _no_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """掐掉入队，改由用例自己同步执行。

    留着入队的话，跑在 `ENV=local` 的 worker 容器会先一步捞走任务，
    而它那边既没有本机连接器也不认测试的 Mock 开关。
    """

    async def _noop(task_id: uuid.UUID) -> None:
        del task_id

    monkeypatch.setattr(task_service, "_enqueue", _noop)


@pytest.fixture
def enable_local(monkeypatch: pytest.MonkeyPatch):
    def _enable(org_id: uuid.UUID, project_ids: list[uuid.UUID]) -> None:
        settings = Settings(  # type: ignore[arg-type]
            env="test",
            local_cli_enabled=True,
            local_cli_org_id=org_id,
            local_cli_project_ids=project_ids,
            local_cli_provider="codex",
            local_cli_token="bridge-" + "k" * 40,
            local_cli_timeout_seconds=30,
            local_cli_image_timeout_seconds=60,
        )
        monkeypatch.setattr(lr_service, "get_settings", lambda: settings)

    return _enable


@pytest.fixture
def no_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 Gateway 变成地雷。

    这是整个文件里最重要的一个夹具：选了本机来源之后，任何一次
    `gateway.generate_image` 都是"悄悄替用户花了一次钱"。让它直接炸，
    比事后看日志可靠。
    """

    async def _boom(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("选了本机来源，绝不允许调用付费 Gateway")

    monkeypatch.setattr(generation.gateway, "generate_image", _boom)


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


async def _run_to_storyboard(client: AsyncClient, title: str = "local-image") -> str:
    pid = str((await client.post(P, json={"title": title})).json()["id"])
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


async def _ledger_count(org_id: uuid.UUID) -> int:
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


async def _task_row(task_id: str) -> Task:
    async with session_scope() as db:
        return (await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))).scalar_one()


async def _assets_of(org_id: uuid.UUID) -> list[Asset]:
    async with session_scope() as db:
        return list((await db.execute(select(Asset).where(Asset.org_id == org_id))).scalars().all())


# ---------------------------------------------------------------- 不可用时不建任务


async def test_local_source_is_refused_before_any_money_moves(
    alice: AsyncClient, enable_local, no_gateway: None
) -> None:
    """本机没连上时：409，且**不建任务、不预扣**。

    建了再失败也"能用"，但那会留下一条本来就不该存在的失败任务，
    还要走一遍预扣—退回。用户看到的应该是"你还没启动连接器"，
    不是"生成失败了"。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice)
    enable_local(org_id, [uuid.UUID(pid)])

    r = transport._r()
    try:
        await r.delete(transport.heartbeat_key("codex"))
    finally:
        await transport._release(r)

    before_tasks, before_ledger = await _task_count(pid), await _ledger_count(org_id)
    before_balance = await _balance(alice)

    resp = await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "local"})
    assert resp.status_code == 409, resp.text
    body = resp.json()["error"]
    assert body["code"] == "local_runtime.offline"
    # 文案必须能告诉用户去干什么——他是唯一能修这件事的人。
    assert "连接器" in body["user_message"]

    assert await _task_count(pid) == before_tasks
    assert await _ledger_count(org_id) == before_ledger
    assert await _balance(alice) == before_balance


async def test_a_project_outside_the_pilot_cannot_pick_local(
    alice: AsyncClient, enable_local, no_gateway: None
) -> None:
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, title="不在白名单")
    # 白名单里放一个别的项目 id
    enable_local(org_id, [uuid.uuid4()])
    await transport.touch_heartbeat("codex", kinds=["image"])

    resp = await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "local"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "local_runtime.not_configured"
    assert await _task_count(pid) == 0


async def test_an_unknown_source_is_a_validation_error(alice: AsyncClient) -> None:
    pid = await _run_to_storyboard(alice, title="来源乱填")
    resp = await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "自己编的"})
    assert resp.status_code == 422
    assert await _task_count(pid) == 0


# ---------------------------------------------------------------- 来源钉死在任务上


async def test_source_is_pinned_into_the_task_and_survives_retry(
    alice: AsyncClient, enable_local, no_gateway: None
) -> None:
    """来源写进 `input_json`，重试**不会**漂移。

    漂移的后果是不对称的：本来说好用订阅额度的一次重试，悄悄变成了
    一次付费调用。所以它必须是任务自己的属性，而不是"重试那一刻本机在不在线"。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, title="来源钉死")
    enable_local(org_id, [uuid.UUID(pid)])
    await transport.touch_heartbeat("codex", kinds=["image"])

    resp = await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "local"})
    assert resp.status_code == 201, resp.text
    task_id = resp.json()["id"]

    row = await _task_row(task_id)
    assert row.input_json["image_source"] == "local"

    # 让它失败，然后重试。
    async with session_scope() as db:
        await task_service.begin_execution(db, task_id=uuid.UUID(task_id))
    async with session_scope() as db:
        await task_service.finish_execution(
            db,
            task_id=uuid.UUID(task_id),
            status="failed",
            error_code="local_runtime.timeout",
        )
    retried = await alice.post(f"/api/v1/tasks/{task_id}/retry")
    assert retried.status_code == 200, retried.text

    row = await _task_row(task_id)
    assert row.input_json["image_source"] == "local", "重试之后来源变了 = 悄悄改花了谁的钱"


async def test_the_default_source_is_still_the_platform(alice: AsyncClient) -> None:
    """不传 body 的老前端必须还是走平台那条，一个字都没变。"""
    pid = await _run_to_storyboard(alice, title="默认来源")
    resp = await alice.post(f"{P}/{pid}/images/characters/zhu_jue")
    assert resp.status_code == 201, resp.text
    row = await _task_row(resp.json()["id"])
    assert row.input_json["image_source"] == "api"


async def test_renders_list_exposes_who_drew_it(
    alice: AsyncClient, enable_local, no_gateway: None
) -> None:
    """界面要能分辨"这张图花了谁的钱"。"""
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, title="来源可见")
    enable_local(org_id, [uuid.UUID(pid)])
    await transport.touch_heartbeat("codex", kinds=["image"])

    await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "local"})
    rows = (await alice.get(f"{P}/{pid}/images")).json()
    assert rows
    assert rows[0]["image_source"] == "local"
    assert rows[0]["source"] == "generated"


# ---------------------------------------------------------------- 执行：绝不回落


async def test_worker_never_calls_the_gateway_when_local_fails(
    alice: AsyncClient, enable_local, no_gateway: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本机失败就是失败。回落到付费 API 等于拿用户的钱补一次他以为免费的调用。"""
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, title="失败不回落")
    enable_local(org_id, [uuid.UUID(pid)])
    await transport.touch_heartbeat("codex", kinds=["image"])

    async def _fail(**_kw: Any) -> Any:
        raise AppError("local_runtime.no_image", message="模型这一轮没画图")

    monkeypatch.setattr(generation.local_runtime, "complete_image", _fail)

    resp = await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "local"})
    task_id = resp.json()["id"]
    outcome = await execute_task({}, task_id)

    assert outcome == "failed"
    row = await _task_row(task_id)
    assert row.error_code == "local_runtime.no_image"
    # 没有任何资产被创建——失败就是失败，不会"顺手用平台补一张"。
    assert not await _assets_of(org_id)


async def test_a_failed_local_run_releases_the_reservation(
    alice: AsyncClient, enable_local, no_gateway: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败按既有资金规则释放预扣，余额一分不少。

    这条路径没有产生任何**平台侧**上游成本（烧的是用户自己的订阅额度），
    所以错误目录里全部登记为 RELEASE。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, title="失败要退钱")
    enable_local(org_id, [uuid.UUID(pid)])
    await transport.touch_heartbeat("codex", kinds=["image"])

    async def _fail(**_kw: Any) -> Any:
        raise AppError("local_runtime.timeout", message="超时")

    monkeypatch.setattr(generation.local_runtime, "complete_image", _fail)

    before = await _balance(alice)
    resp = await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "local"})
    task_id = resp.json()["id"]
    # 预扣发生了：这条路径**没有**被做成免费（折算规则还没定，见 15 号文档）。
    reserved = await _balance(alice)
    assert reserved["reserved"] > before["reserved"]

    assert await execute_task({}, task_id) == "failed"

    after = await _balance(alice)
    assert after == before, "失败之后预扣必须原样退回"


async def test_local_error_codes_never_failover_and_always_release() -> None:
    """错误目录本身就是资金规则的一部分（21_ErrorTaxonomy §2）。

    这两条属性写错了不会有任何测试变红，但一条会让用户被悄悄改调付费
    接口，另一条会让他为一次没产出的生成付钱。
    """
    local_codes = [code for code in ERRORS if code.startswith("local_runtime.")]
    assert local_codes, "错误码没登记？"
    for code in local_codes:
        spec = ERRORS[code]
        assert spec.failover is False, f"{code} 不许 failover 到付费通道"
        assert spec.disposition is Disposition.RELEASE, f"{code} 必须退回预扣"
        # `apiFetch` 只显示 user_message，而这条链路能修的人是用户自己。
        assert spec.user_message and spec.user_message != code


# ---------------------------------------------------------------- 执行：真的入库


async def test_a_successful_local_run_lands_in_the_project_asset_library(
    alice: AsyncClient, enable_local, no_gateway: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成功这条：图片字节 → 同一个 `register_generated` → 项目资产库。

    走的是与 API 出图**同一条**落地路径，所以配额闸门、MIME 白名单、
    对象存储键的形状全都自动一致——绕过去等于绕过全部这些校验。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, title="真的入库")
    enable_local(org_id, [uuid.UUID(pid)])
    await transport.touch_heartbeat("codex", kinds=["image"])

    seen: dict[str, Any] = {}

    async def _ok(**kwargs: Any) -> lr_service.LocalImage:
        seen.update(kwargs)
        return lr_service.LocalImage(
            data=PNG,
            mime_type="image/png",
            model_id="local-cli.codex",
            revised_prompt="模型改写后的提示词",
        )

    monkeypatch.setattr(generation.local_runtime, "complete_image", _ok)

    resp = await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "local"})
    task_id = resp.json()["id"]
    assert await execute_task({}, task_id) == "succeeded"

    # 提示词是**服务端合成**的那一份，不是前端传的（前端根本传不了）。
    assert seen["org_id"] == org_id
    assert seen["project_id"] == uuid.UUID(pid)
    assert seen["prompt"]

    row = await _task_row(task_id)
    asset_ids = row.output_json["asset_ids"]
    assert len(asset_ids) == 1
    assert row.output_json["image_source"] == "local"

    assets = await _assets_of(org_id)
    assert len(assets) == 1
    asset = assets[0]
    assert asset.project_id == uuid.UUID(pid)
    assert asset.mime_type == "image/png"
    assert asset.size_bytes == len(PNG)
    # Codex 会改写提示词，改写后的那份必须留档——与万相那条同一个字段名。
    assert asset.metadata_json["actual_prompt"] == "模型改写后的提示词"
    assert asset.metadata_json["image_source"] == "local"
    assert asset.metadata_json["model_id"] == "local-cli.codex"

    # 它在项目资产库里真的看得到（这才是"进资产库"的定义）。
    listed = (await alice.get(f"/api/v1/assets?project_id={pid}")).json()
    assert str(asset.id) in str(listed)

    # 出图记录也认得它。
    renders = (await alice.get(f"{P}/{pid}/images")).json()
    assert renders[0]["asset_id"] == str(asset.id)
    assert renders[0]["image_source"] == "local"


async def test_a_task_without_a_project_cannot_use_the_local_source(
    alice: AsyncClient, enable_local, no_gateway: None
) -> None:
    """白名单是按 (org, project) 配的。从 `POST /tasks` 直接建的出图任务
    没有项目，不该有机会走到本机来源上。

    以前这条路是"建出来、跑一遍、在 Worker 里失败"。preflight 提到
    `task_service` 之后它在**建任务之前**就被拒了——任务没建、钱没动
    （P2-4）。这两句断言就是那个差别。
    """
    org_id = await _org(alice)
    enable_local(org_id, [uuid.uuid4()])
    await transport.touch_heartbeat("codex", kinds=["image"])
    before_ledger = await _ledger_count(org_id)

    r = await alice.post(
        "/api/v1/tasks",
        json={"type": "image.generate", "input": {"prompt": "画一张", "image_source": "local"}},
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "local_runtime.not_configured"
    assert await _ledger_count(org_id) == before_ledger


# ------------------------------------------------- P2-4：preflight 是不变式


async def test_retrying_a_local_task_while_the_runner_is_offline_moves_no_money(
    alice: AsyncClient, enable_local, no_gateway: None
) -> None:
    """连接器下线之后，重试**不该**重新预扣一笔去跑一条注定失败的任务。

    这是复核报告 P2-4 点名的绕过路径之一：`retry_task` 沿用原
    `input_json`（来源确实钉住了，这点是对的），但此前它不做任何 preflight。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, title="离线重试")
    enable_local(org_id, [uuid.UUID(pid)])
    await transport.touch_heartbeat("codex", kinds=["image"])

    resp = await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "local"})
    assert resp.status_code == 201, resp.text
    task_id = resp.json()["id"]

    async with session_scope() as db:
        await task_service.begin_execution(db, task_id=uuid.UUID(task_id))
    async with session_scope() as db:
        await task_service.finish_execution(
            db, task_id=uuid.UUID(task_id), status="failed", error_code="local_runtime.timeout"
        )

    # 用户在这期间把连接器关了。
    r = transport._r()
    try:
        await r.delete(transport.heartbeat_key("codex"))
    finally:
        await transport._release(r)

    before_ledger, before_balance = await _ledger_count(org_id), await _balance(alice)

    retried = await alice.post(f"/api/v1/tasks/{task_id}/retry")
    assert retried.status_code == 409, retried.text
    assert retried.json()["error"]["code"] == "local_runtime.offline"

    assert await _ledger_count(org_id) == before_ledger, "被拒的重试不该在账本上留痕"
    assert await _balance(alice) == before_balance
    row = await _task_row(task_id)
    assert row.status == "failed", "重试被拒，任务不该被翻回 queued"


async def test_retry_still_works_when_the_runner_is_back(
    alice: AsyncClient, enable_local, no_gateway: None
) -> None:
    """连接器在的时候，重试照旧——这道闸不能把正常路径也堵掉。"""
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, title="在线重试")
    enable_local(org_id, [uuid.UUID(pid)])
    await transport.touch_heartbeat("codex", kinds=["image"])

    task_id = (
        await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "local"})
    ).json()["id"]
    async with session_scope() as db:
        await task_service.begin_execution(db, task_id=uuid.UUID(task_id))
    async with session_scope() as db:
        await task_service.finish_execution(
            db, task_id=uuid.UUID(task_id), status="failed", error_code="local_runtime.timeout"
        )

    await transport.touch_heartbeat("codex", kinds=["image"])
    assert (await alice.post(f"/api/v1/tasks/{task_id}/retry")).status_code == 200


async def test_an_api_task_is_never_blocked_by_the_local_preflight(
    alice: AsyncClient, enable_local
) -> None:
    """平台那条来源与连接器在不在线毫无关系，别把它也拦下来。"""
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, title="平台不受影响")
    enable_local(org_id, [uuid.UUID(pid)])
    r = transport._r()
    try:
        await r.delete(transport.heartbeat_key("codex"))
    finally:
        await transport._release(r)

    resp = await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "api"})
    assert resp.status_code == 201, resp.text


# ------------------------------------------------- P3-13：本机一次只出一张


async def test_a_local_task_asking_for_more_than_one_image_is_refused_before_it_exists(
    alice: AsyncClient, enable_local, no_gateway: None
) -> None:
    """`pricing._shape` 按 `n` 计份数，而本机这条永远只出一张。

    不挡的话是**按 n 张预扣、只登记一个资产**，而且不报任何错。
    界面进不来（`render.py` 三处都写死 `"n": 1`），但 `POST /tasks` 可以。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, title="本机只出一张")
    enable_local(org_id, [uuid.UUID(pid)])
    await transport.touch_heartbeat("codex", kinds=["image"])
    before_ledger = await _ledger_count(org_id)
    before_tasks = await _task_count(pid)

    r = await alice.post(
        "/api/v1/tasks",
        json={
            "type": "image.generate",
            "project_id": pid,
            "input": {"prompt": "画四张", "image_source": "local", "n": 4},
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "provider.params.invalid"
    assert await _task_count(pid) == before_tasks
    assert await _ledger_count(org_id) == before_ledger


async def test_the_worker_also_refuses_more_than_one(
    alice: AsyncClient, enable_local, no_gateway: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第二道闸：就算有人绕过建任务，Worker 也不静默少给。"""
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, title="worker 也挡")
    enable_local(org_id, [uuid.UUID(pid)])
    await transport.touch_heartbeat("codex", kinds=["image"])

    async def _never(**_kw: Any) -> Any:
        raise AssertionError("n != 1 时根本不该走到连接器")

    monkeypatch.setattr(generation.local_runtime, "complete_image", _never)

    with pytest.raises(AppError) as exc:
        await generation._generate_local(
            {"image_source": "local", "n": 3, "project_id": pid},
            org_id=org_id,
            project_id=uuid.UUID(pid),
            owner_user_id=None,
            prompt="画三张",
            task_id=None,
        )
    assert exc.value.code == "provider.params.invalid"


# ------------------------------------------------- P1-3：成功要收钱，文案要对得上


async def test_a_successful_local_run_settles_the_reservation_at_full_price(
    alice: AsyncClient, enable_local, no_gateway: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成功的本机出图**照原价结算**，与 API 出图同价。

    这条是资金口径的锚：界面上那三处文案和 `local_runtime.usage_limit`
    都必须与它一致。写成"本机不扣 Credits"会与这里的断言直接矛盾——
    `pricing._shape` 里 `image_source` 一个字都不参与定价。
    """
    org_id = await _org(alice)
    pid = await _run_to_storyboard(alice, title="成功要收钱")
    enable_local(org_id, [uuid.UUID(pid)])
    await transport.touch_heartbeat("codex", kinds=["image"])

    async def _ok(**_kw: Any) -> lr_service.LocalImage:
        return lr_service.LocalImage(
            data=PNG, mime_type="image/png", model_id="local-cli.codex", revised_prompt=None
        )

    monkeypatch.setattr(generation.local_runtime, "complete_image", _ok)

    before = await _balance(alice)
    task_id = (
        await alice.post(f"{P}/{pid}/images/characters/zhu_jue", json={"source": "local"})
    ).json()["id"]
    assert await execute_task({}, task_id) == "succeeded"

    row = await _task_row(task_id)
    assert row.reserved_cost > 0, "本机这条**不是**免费路径"
    assert row.actual_cost == row.reserved_cost, "成功按预扣全额结算，与 API 出图同价"

    after = await _balance(alice)
    assert after["balance"] == before["balance"] - row.actual_cost
    assert after["reserved"] == before["reserved"]


def test_the_usage_limit_copy_does_not_claim_the_path_is_free() -> None:
    """`usage_limit` 的文案曾经写着"这条路径不消耗平台 Credits"。

    那句话在"失败时"这个狭义上没错，但用户读到的是整条路径免费——
    与上面那条成功用例的断言相反。文案回退到旧说法时这条要红。
    """
    message = ERRORS["local_runtime.usage_limit"].user_message
    assert "本次失败不扣平台 Credits" in message
    assert "这条路径不消耗平台 Credits" not in message


# ---------------------------------------------------------------- 图片字节校验


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\Users\alice\.codex\auth.json",
        "https://evil.example/whatever.png",
        "这不是图片",
    ],
)
def test_decode_image_refuses_anything_that_is_not_an_image(raw: str) -> None:
    """路径、URL、一段文字都不是图片。

    `savedPath` 那个字段的教训：对端给的路径可以指向任何地方，而这份字节
    马上要被写进对象存储、再被浏览器当图片加载。判据只能是**字节魔数**。
    """
    with pytest.raises(AppError) as exc:
        lr_service.decode_image(base64.b64encode(raw.encode()).decode("ascii"))
    assert exc.value.code == "local_runtime.result_invalid"


def test_decode_image_refuses_a_valid_header_with_a_broken_body() -> None:
    """魔数只有八个字节。"PNG 头 + 一坨随机数据"能骗过它，骗不过真解码。

    这一条是"必须真解一遍"的全部理由：不解的话，这坨东西会被写进对象存储，
    然后在用户的浏览器里显示成一个碎图标——而任务是"成功"的。
    """
    with pytest.raises(AppError) as exc:
        lr_service.decode_image(base64.b64encode(CORRUPT_PNG).decode("ascii"))
    assert exc.value.code == "local_runtime.result_invalid"


def test_decode_image_refuses_a_decompression_bomb() -> None:
    """字节数小不代表解开来小。

    一张纯色的 30000×30000 PNG 压缩后只有几百字节，解开是 9 亿像素、
    几个 GB 的内存。所以尺寸必须在 `load()` **之前**检查——
    等它开始解就已经晚了。
    """
    import struct
    import zlib

    def _chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    def _huge_png(width: int, height: int) -> bytes:
        """一个**声称**自己有这么大的 PNG。文件只有两百来字节。

        像素数据是假的（永远不会被解到），但 IDAT / IEND 必须在——
        少了它们 Pillow 连头都不肯读，那样这个用例验的就变成"文件坏了"，
        而不是"尺寸太大"。
        """
        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(16)))
            + _chunk(b"IEND", b"")
        )

    # 8000×8000 = 6400 万像素：低于 Pillow 自己的炸弹阈值（约 1.78 亿），
    # 所以**必须由我们这一道**挡下来。它正是那段"Pillow 不管、我们要管"的区间。
    with pytest.raises(AppError) as ours:
        lr_service.decode_image(base64.b64encode(_huge_png(8000, 8000)).decode("ascii"))
    assert ours.value.code == "local_runtime.result_invalid"
    assert "too large" in ours.value.message

    # 30000×30000 = 9 亿像素：Pillow 自己在 open 的时候就炸了，
    # 我们把它归到同一个错误码上（对用户来说都是"这不是一张能用的图"）。
    with pytest.raises(AppError) as theirs:
        lr_service.decode_image(base64.b64encode(_huge_png(30000, 30000)).decode("ascii"))
    assert theirs.value.code == "local_runtime.result_invalid"


def test_decode_image_refuses_a_few_pixel_image() -> None:
    """几像素的"图"不是产物，是一次失败的生成或者一段垃圾。"""
    with pytest.raises(AppError) as exc:
        lr_service.decode_image(base64.b64encode(_png(4, 4)).decode("ascii"))
    assert exc.value.code == "local_runtime.result_invalid"
    assert "too small" in exc.value.message


def test_decode_image_accepts_a_real_png() -> None:
    data, mime = lr_service.decode_image(base64.b64encode(PNG).decode("ascii"))
    assert data == PNG
    assert mime == "image/png"


def test_decode_image_accepts_a_real_webp_and_jpeg() -> None:
    """三种格式都要真能解出来——落进资产库的东西不该有惊喜。"""
    for fmt, mime in (("WEBP", "image/webp"), ("JPEG", "image/jpeg")):
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, format=fmt)
        _data, sniffed = lr_service.decode_image(base64.b64encode(buf.getvalue()).decode("ascii"))
        assert sniffed == mime


def test_render_module_keeps_the_two_sources_apart() -> None:
    """两个常量在 `render` 与 `worker.jobs.generation` 里各写了一份
    （跨模块只为两个字符串建依赖不划算）。它们必须相等——
    不等的后果是本机任务被当成平台任务，直接去打付费接口。"""
    assert render.IMAGE_SOURCE_API == generation.SOURCE_API
    assert render.IMAGE_SOURCE_LOCAL == generation.SOURCE_LOCAL
