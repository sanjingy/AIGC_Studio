"""Worker 那条出图路径的端到端：Gateway → 下载 → 转存 → 落库。

**这条路在此之前没有任何自动化测试覆盖。**
`test_image_generation.py` 用 `_no_enqueue` 把入队掐掉，只断言到"任务建出来了、
提示词拼对了"为止——再往后的下载与落库全靠人工验收。原因也很实在：
一旦让 `image.generate` 真的执行，就会拿平台的真 Key 去打万相，每跑一遍
测试套件都在花钱。

有了无 Key 回退（FR-CONS-011）之后这条路才测得了：`ENV=test` 下 Gateway
一律解析到 Mock，而 Mock 返回的是**真能 HTTP GET 到的 URL**，所以
`_download` / `register_generated` 一步都不会被跳过。

**入队仍然要掐掉**：worker 容器跑在 `ENV=local`，它不认测试环境的 Mock 开关，
真让它捞到这个任务就会去打真上游。这里改成在测试进程里直接调
`execute_task`——同一个函数、同一条分发，只是不经过 Redis 队列。
"""

from __future__ import annotations

import io
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from apps.api.core.db import session_scope
from apps.api.modules.asset.models import Asset
from apps.api.modules.gateway import mock_image
from apps.api.modules.task import service as task_service
from apps.api.modules.task.models import Task
from worker.jobs.execute import execute_task

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _no_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """掐掉入队，改由用例自己同步执行。

    留着入队的话，跑在 `ENV=local` 的 worker 容器会先一步捞走这个任务，
    然后拿真 Key 去打万相——测试套件又开始花钱，而且断言还会因为
    "任务已经被别人跑完了"变得时红时绿。
    """

    async def _noop(task_id: uuid.UUID) -> None:
        del task_id

    monkeypatch.setattr(task_service, "_enqueue", _noop)


async def _create_image_task(client: AsyncClient, prompt: str, **extra: Any) -> str:
    r = await client.post(
        "/api/v1/tasks",
        json={"type": "image.generate", "input": {"prompt": prompt, "n": 1, **extra}},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _run(task_id: str) -> str:
    return await execute_task({}, task_id)


async def _task_row(task_id: str) -> Task:
    async with session_scope() as db:
        return (await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))).scalar_one()


async def _assets_of(org_id: uuid.UUID) -> list[Asset]:
    async with session_scope() as db:
        return list(
            (
                await db.execute(
                    select(Asset).where(Asset.org_id == org_id).order_by(Asset.created_at)
                )
            )
            .scalars()
            .all()
        )


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


# ---------------------------------------------------------------- 全程


async def test_image_task_lands_a_real_asset_without_any_key(alice: AsyncClient) -> None:
    """没有 Provider Key，出图任务照样跑到 succeeded，资产库里真的多一行。

    断言一路铺到字节：只断言"任务成功了"的话，一个直接返回空 URL 列表的
    Mock 也能过，而那正好把这个 Mock 的意义（覆盖下载与落库）架空。
    """
    org_id = await _org(alice)
    before = len(await _assets_of(org_id))

    task_id = await _create_image_task(alice, "主角站在铁门外，夜色，雨")
    assert await _run(task_id) == "succeeded"

    row = await _task_row(task_id)
    assert row.status == "succeeded"
    output = dict(row.output_json or {})
    assert output["model_id"] == mock_image.MODEL_ID, "没有 Key 时必须解析到 Mock"
    assert len(output["asset_ids"]) == 1

    assets = await _assets_of(org_id)
    assert len(assets) == before + 1, "资产库里没多出这一行"

    asset = next(a for a in assets if str(a.id) == output["asset_ids"][0])
    assert asset.status == "ready"
    assert asset.mime_type == "image/png"
    assert asset.size_bytes and asset.size_bytes > 0, "落库了但字节数是 0"
    assert asset.metadata_json["source"] == "generated"
    assert asset.metadata_json["model_id"] == mock_image.MODEL_ID


async def test_the_bytes_in_storage_are_a_real_png(alice: AsyncClient) -> None:
    """对象存储里躺着的必须是一张真 PNG，不是一个空文件。

    `_download` 只检查 HTTP 状态码和大小上限，不看内容；转存链路上任何一环
    把字节弄丢，表现都只是"图打不开"，而任务状态一切正常。
    """
    from PIL import Image

    from apps.api.modules.asset import storage

    org_id = await _org(alice)
    task_id = await _create_image_task(alice, "铁门特写，锈迹")
    assert await _run(task_id) == "succeeded"

    asset = (await _assets_of(org_id))[-1]
    info = await storage.head_object(key=asset.storage_key)
    assert info is not None, "库里有记录，桶里没对象"
    assert info.size_bytes == asset.size_bytes

    url = await storage.presign_get(key=asset.storage_key, internal=True)
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
    assert resp.status_code == 200
    image = Image.open(io.BytesIO(resp.content))
    assert image.format == "PNG"
    assert image.size == (1024, 1024)


async def test_two_shots_get_two_different_images(alice: AsyncClient) -> None:
    """不同提示词必须落成不同的图。

    十个镜头出十张一样的图，看不出流程是不是真的按镜头走了一遍——
    这正是占位图存在的理由，所以要在真实链路上验一次，而不是只验渲染函数。
    """
    org_id = await _org(alice)

    for prompt in ("第 1 镜的画面内容", "第 2 镜的画面内容"):
        task_id = await _create_image_task(alice, prompt)
        assert await _run(task_id) == "succeeded"

    assets = (await _assets_of(org_id))[-2:]
    assert len({a.checksum for a in assets}) == 2, "两个镜头出了同一张图"
    assert len({a.storage_key for a in assets}) == 2


async def test_same_prompt_is_reproducible(alice: AsyncClient) -> None:
    """同一个提示词跑两次得到同样的字节。

    不确定的 Mock 会让测试时红时绿，比没有 Mock 更糟。
    """
    org_id = await _org(alice)

    for _ in range(2):
        task_id = await _create_image_task(alice, "完全一样的提示词", seed=42)
        assert await _run(task_id) == "succeeded"

    a, b = (await _assets_of(org_id))[-2:]
    assert a.checksum == b.checksum, "同一个提示词出了两张不同的图"
    assert a.storage_key != b.storage_key, "两次生成仍然是两份独立资产"


# ---------------------------------------------------------------- 计费


async def test_mock_run_settles_like_any_other_image_task(alice: AsyncClient) -> None:
    """Mock 跑完照样按普通出图结算，计费链路上没有 `if mock` 这样的特例。

    这是刻意的：预扣与结算都发生在 Gateway 解析之前/之后的任务层，
    它们看的是任务请求的模型（`pricing._DEFAULT_MODEL`），不是 Gateway
    最终解析到谁——真实路径上 failover 换了模型也是这个行为。为了让
    Mock 免费而在 billing 里开口子，等于在计费上多一条只有测试环境才走的
    分支，那比"无 Key 环境里 Credits 照扣"危险得多。
    """
    before = (await alice.get("/api/v1/credits/balance")).json()

    task_id = await _create_image_task(alice, "一张会被计费的图")
    row = await _task_row(task_id)
    assert row.reserved_cost > 0, "出图任务必须预扣"
    reserved = (await alice.get("/api/v1/credits/balance")).json()
    assert reserved["reserved"] == before["reserved"] + row.reserved_cost

    assert await _run(task_id) == "succeeded"

    after = (await alice.get("/api/v1/credits/balance")).json()
    assert after["reserved"] == before["reserved"], "预扣没释放"
    assert after["balance"] == before["balance"] - row.reserved_cost
