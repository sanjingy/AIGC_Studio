"""任务状态机、幂等与发件箱。

SSE 的端到端行为在 test_realtime.py，这里只覆盖不需要真实流的部分。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.task.models import OutboxEvent, Task

pytestmark = pytest.mark.integration

T = "/api/v1/tasks"
P = "/api/v1/projects"


async def _project(client: AsyncClient) -> str:
    r = await client.post(P, json={"title": "任务测试"})
    return str(r.json()["id"])


async def _task(client: AsyncClient, pid: str, **over: object) -> dict:
    payload: dict = {"type": "mock.echo", "project_id": pid, "input": {"steps": 1}}
    payload.update(over)
    r = await client.post(T, json=payload)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def test_create_task_starts_queued(alice: AsyncClient) -> None:
    pid = await _project(alice)
    task = await _task(alice, pid)
    assert task["status"] == "queued"
    assert task["progress"] == 0
    assert task["attempt"] == 0


async def test_idempotency_key_prevents_duplicate(alice: AsyncClient) -> None:
    """网络抖动导致的重发不能建出两个任务——那等于成本翻倍。"""
    pid = await _project(alice)
    body = {"type": "mock.echo", "project_id": pid, "input": {"steps": 1}}
    # 幂等键全局唯一，写死会与上一轮测试残留的记录撞车
    headers = {"Idempotency-Key": f"dup-{uuid.uuid4().hex}"}

    first = await alice.post(T, json=body, headers=headers)
    second = await alice.post(T, json=body, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 200, "幂等命中应返回 200，与新建区分开"
    assert first.json()["id"] == second.json()["id"]


async def test_idempotency_key_is_not_shared_across_orgs(
    alice: AsyncClient, bob: AsyncClient
) -> None:
    """幂等键撞车绝不能把别人的任务返回给你。"""
    a_pid = await _project(alice)
    b_pid = await _project(bob)
    headers = {"Idempotency-Key": f"shared-{uuid.uuid4().hex}"}

    await alice.post(
        T, json={"type": "mock.echo", "project_id": a_pid, "input": {}}, headers=headers
    )
    r = await bob.post(
        T, json={"type": "mock.echo", "project_id": b_pid, "input": {}}, headers=headers
    )
    assert r.status_code == 409


async def test_unknown_task_type_rejected(alice: AsyncClient) -> None:
    pid = await _project(alice)
    r = await alice.post(T, json={"type": "not.a.type", "project_id": pid})
    assert r.status_code == 422


async def test_cancel_then_retry_is_rejected(alice: AsyncClient) -> None:
    """已取消是终态。允许从终态复活会让状态机失去意义。"""
    pid = await _project(alice)
    task = await _task(alice, pid, input={"steps": 20, "step_delay": 1})

    cancelled = await alice.post(f"{T}/{task['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    r = await alice.post(f"{T}/{task['id']}/retry")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "common.conflict"


async def test_cancel_twice_is_rejected(alice: AsyncClient) -> None:
    pid = await _project(alice)
    task = await _task(alice, pid, input={"steps": 20, "step_delay": 1})

    assert (await alice.post(f"{T}/{task['id']}/cancel")).status_code == 200
    assert (await alice.post(f"{T}/{task['id']}/cancel")).status_code == 409


async def test_tasks_scoped_to_org(alice: AsyncClient, bob: AsyncClient) -> None:
    pid = await _project(alice)
    task = await _task(alice, pid)

    assert (await bob.get(f"{T}/{task['id']}")).status_code == 404
    assert (await bob.post(f"{T}/{task['id']}/cancel")).status_code == 404
    assert (await bob.post(f"{T}/{task['id']}/retry")).status_code == 404

    ids = {t["id"] for t in (await bob.get(T)).json()["items"]}
    assert task["id"] not in ids


async def test_outbox_written_in_same_transaction(alice: AsyncClient, db: AsyncSession) -> None:
    """建任务必须同时产生 task.created 事件（ADR-019）。

    两者不同事务的话，会出现"任务建好了但前端永远收不到通知"。
    """
    pid = await _project(alice)
    task = await _task(alice, pid)

    rows = (
        await db.execute(
            select(OutboxEvent).where(OutboxEvent.project_id == pid).order_by(OutboxEvent.seq)
        )
    ).scalars()
    created = [r for r in rows if r.type == "task.created"]

    assert created, "建任务后发件箱必须有 task.created"
    assert created[0].data_json["task_id"] == task["id"]
    assert created[0].data_json["status"] == "queued"


async def test_event_carries_full_state_not_delta(alice: AsyncClient, db: AsyncSession) -> None:
    """事件带最终状态而非增量指令——SSE 重连必然重复，客户端要能幂等覆盖。"""
    pid = await _project(alice)
    await _task(alice, pid)

    row = (
        await db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.project_id == pid, OutboxEvent.type == "task.created")
            .limit(1)
        )
    ).scalar_one()

    # 完整快照的必备字段
    assert {"task_id", "status", "progress", "attempt"} <= set(row.data_json)


async def test_outbox_seq_follows_creation_order(alice: AsyncClient, db: AsyncSession) -> None:
    """先发生的事件 seq 必须更小，否则前端会看到进度倒退。

    注意不能按主键排序再断言 seq 有序——主键是 UUID，本身就无序。
    要验证的是"业务发生顺序"与"seq 顺序"一致。
    """
    pid = await _project(alice)
    task_ids = [(await _task(alice, pid))["id"] for _ in range(3)]

    rows = (
        await db.execute(
            select(OutboxEvent.seq, OutboxEvent.data_json).where(
                OutboxEvent.project_id == pid, OutboxEvent.type == "task.created"
            )
        )
    ).all()
    seq_by_task = {r.data_json["task_id"]: r.seq for r in rows}

    seqs = [seq_by_task[tid] for tid in task_ids]
    assert seqs == sorted(seqs), f"seq 顺序与建任务顺序不符：{seqs}"
    assert len(set(seqs)) == 3, "seq 不能重复"


async def test_task_requires_auth(client: AsyncClient) -> None:
    assert (await client.post(T, json={"type": "mock.echo"})).status_code == 401
    assert (await client.get(T)).status_code == 401


async def test_worker_finish_ignores_cancelled_task(alice: AsyncClient, db: AsyncSession) -> None:
    """任务执行中被取消后，Worker 的完成回调不能把它改回 succeeded。

    用户看到的"已取消"必须是最终状态，不能过两秒又变成"已完成"。
    """
    import uuid as _uuid

    from apps.api.modules.task import service as task_service

    pid = await _project(alice)
    task = await _task(alice, pid, input={"steps": 20, "step_delay": 1})
    await alice.post(f"{T}/{task['id']}/cancel")

    await task_service.finish_execution(
        db, task_id=_uuid.UUID(task["id"]), status="succeeded", output={"x": 1}
    )

    row = (await db.execute(select(Task).where(Task.id == task["id"]))).scalar_one()
    assert row.status == "cancelled", "取消后不得被改成 succeeded"
