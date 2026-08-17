"""任务链路与账务的衔接。

存在的理由是一个真实漏掉的 bug：`begin_execution` 会把 attempt 自增，
而预扣是在创建时记的，结算时用自增后的编号去找预扣——找不到就静默跳过。
表现是**任务成功了、钱一直挂在 reserved 里没扣走**，不报任何错。

单独测 billing 抓不到它（那里 attempt 是对齐的），单独测 task 也抓不到
（那里不看余额）。只有把两者串起来跑才暴露。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.db import session_scope
from apps.api.modules.billing import service as billing
from apps.api.modules.task import service as task_service

pytestmark = pytest.mark.integration

T = "/api/v1/tasks"
P = "/api/v1/projects"
C = "/api/v1/credits"


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


async def _project(client: AsyncClient) -> str:
    return str((await client.post(P, json={"title": "billing-lifecycle"})).json()["id"])


async def _balance(client: AsyncClient) -> dict[str, int]:
    return dict((await client.get(f"{C}/balance")).json())


async def _wait_terminal(client: AsyncClient, task_id: str, timeout: float = 20) -> dict:
    """等任务跑到终态。Worker 是独立进程，只能轮询。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        row = (await client.get(f"{T}/{task_id}")).json()
        if row["status"] in ("succeeded", "failed", "cancelled"):
            return dict(row)
        await asyncio.sleep(0.25)
    raise AssertionError(f"任务 {task_id} 在 {timeout}s 内没有到终态")


async def test_successful_task_settles_the_reservation(alice: AsyncClient) -> None:
    """成功的任务必须真的扣到钱，不能把预扣永远挂着。"""
    before = await _balance(alice)
    pid = await _project(alice)

    created = (
        await alice.post(
            T,
            json={
                "type": "mock.echo",
                "project_id": pid,
                "input": {"steps": 1, "step_delay": 0.05},
            },
        )
    ).json()

    # 建完就该有预扣
    mid = await _balance(alice)
    assert mid["reserved"] == created["estimated_cost"] > 0
    assert mid["total"] == before["total"], "预扣阶段总资产不变"

    done = await _wait_terminal(alice, created["id"])
    assert done["status"] == "succeeded"

    after = await _balance(alice)
    assert after["reserved"] == 0, "结算后不能还有挂着的预扣"
    assert after["total"] == before["total"] - created["estimated_cost"], "必须真的扣到钱"
    assert after["balance"] == after["total"]


async def test_failed_task_releases_the_reservation(alice: AsyncClient) -> None:
    """失败且责任不在用户时，预扣必须原样退回。"""
    before = await _balance(alice)
    pid = await _project(alice)

    created = (
        await alice.post(
            T,
            json={
                "type": "mock.fail",
                "project_id": pid,
                "input": {"error_code": "provider.unavailable"},
            },
        )
    ).json()

    done = await _wait_terminal(alice, created["id"])
    assert done["status"] == "failed"
    assert done["actual_cost"] == 0

    after = await _balance(alice)
    assert after["reserved"] == 0
    assert after["total"] == before["total"], "上游故障不该让用户买单"


async def test_cancel_releases_the_reservation(alice: AsyncClient) -> None:
    before = await _balance(alice)
    pid = await _project(alice)

    # 步数少但每步慢：估价压在体验额度内，同时留足取消的时间窗
    r = await alice.post(
        T,
        json={
            "type": "mock.echo",
            "project_id": pid,
            "input": {"steps": 3, "step_delay": 5},
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert (await alice.post(f"{T}/{created['id']}/cancel")).status_code == 200

    after = await _balance(alice)
    assert after["reserved"] == 0, "取消后不能还挂着预扣"
    assert after["total"] == before["total"], "取消不收钱"


async def test_insufficient_balance_blocks_task_creation(
    alice: AsyncClient, db: AsyncSession
) -> None:
    """余额不足时任务必须建不出来，且不能留一个 queued 任务给 Worker 捞。"""
    org = await _org(alice)
    from apps.api.modules.billing import repository as repo

    acc = await repo.lock_account(db, org_id=org)
    if acc.balance:
        await repo.append(
            db,
            account=acc,
            tx_type="ADJUST",
            amount=-acc.balance,
            reserved_delta=0,
            idempotency_key=f"drain:{uuid.uuid4()}",
        )
    await db.commit()

    pid = await _project(alice)
    r = await alice.post(T, json={"type": "mock.echo", "project_id": pid, "input": {}})

    assert r.status_code == 402
    assert r.json()["error"]["code"] == "billing.credit.insufficient"

    # 关键：不能留下 queued 任务
    rows = (await alice.get(f"{T}?project_id={pid}")).json()["items"]
    assert all(t["status"] != "queued" for t in rows), "预扣失败的任务不能留在队列里"


async def test_ledger_balances_after_real_task_run(alice: AsyncClient) -> None:
    """跑完真实任务后账仍要平。"""
    pid = await _project(alice)
    for _ in range(3):
        created = (
            await alice.post(
                T,
                json={
                    "type": "mock.echo",
                    "project_id": pid,
                    "input": {"steps": 1, "step_delay": 0.05},
                },
            )
        ).json()
        await _wait_terminal(alice, created["id"])

    org = await _org(alice)
    async with session_scope() as s:
        ok, ledger, actual = await billing.audit(s, org_id=org)
    assert ok, f"账不平：流水 {ledger} vs 账户 {actual}"


async def test_reserve_and_settle_use_the_same_attempt(alice: AsyncClient) -> None:
    """预扣与结算的 attempt 编号必须一致。

    这正是漏掉的那个 off-by-one：不一致时结算静默跳过，
    任务显示成功但钱没扣，且不产生任何错误日志。
    """
    from apps.api.modules.task.models import Task

    pid = await _project(alice)
    created = (
        await alice.post(
            T,
            json={
                "type": "mock.echo",
                "project_id": pid,
                "input": {"steps": 1, "step_delay": 0.05},
            },
        )
    ).json()
    done = await _wait_terminal(alice, created["id"])

    async with session_scope() as s:
        row = await s.get(Task, uuid.UUID(created["id"]))
    assert row is not None
    # 首次执行后 attempt 应为 1，而预扣正是按这个编号记的
    assert row.attempt == 1
    assert task_service._next_run_attempt(row) == 2, "下一次重试应按 2 号预扣"

    assert done["actual_cost"] > 0, "结算真的发生了"
