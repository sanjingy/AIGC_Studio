"""实时通道：票据、中继、重放。

不测完整 SSE 长连接——ASGITransport 的流式读取行为与真实 HTTP 有差异，
在这里测只会得到关于测试工具的结论。SSE 长连接的端到端行为靠真实 HTTP
手工验证，这里覆盖它依赖的各个部件。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from apps.api.modules.realtime import stream
from apps.api.modules.realtime.relay import relay_once, sse_frame
from apps.api.modules.realtime.stream import parse_id

pytestmark = pytest.mark.integration

P = "/api/v1/projects"
T = "/api/v1/tasks"


async def _project(client: AsyncClient) -> str:
    return str((await client.post(P, json={"title": "实时"})).json()["id"])


# ------------------------------------------------------------------ 票据


async def test_ticket_is_single_use(alice: AsyncClient) -> None:
    """票据用掉即焚——URL 会进 access log，泄露了也只能用一次。"""
    pid = await _project(alice)
    ticket = (await alice.post(f"{P}/{pid}/events/ticket")).json()["ticket"]

    from apps.api.core.redis import get_redis

    r = get_redis()
    key = f"sse:ticket:{ticket}"
    assert await r.getdel(key) is not None
    assert await r.getdel(key) is None


async def test_ticket_requires_project_ownership(alice: AsyncClient, bob: AsyncClient) -> None:
    pid = await _project(alice)
    assert (await bob.post(f"{P}/{pid}/events/ticket")).status_code == 404


async def test_ticket_requires_auth(client: AsyncClient) -> None:
    r = await client.post(f"{P}/00000000-0000-0000-0000-000000000000/events/ticket")
    assert r.status_code == 401


async def test_sse_rejects_bad_ticket(alice: AsyncClient) -> None:
    pid = await _project(alice)
    r = await alice.get(f"{P}/{pid}/events?ticket=not-a-real-ticket")
    assert r.status_code == 401


# ------------------------------------------------------------------ 中继


async def test_relay_moves_outbox_to_stream(alice: AsyncClient) -> None:
    """发件箱 → Redis Stream。中继坏了等于所有前端永远转圈。

    断言"事件最终到了流里"而不是"本次调用搬了几条"——
    Worker 里的常驻中继会与本用例抢跑，它先搬完的话
    relay_once() 返回 0，但结果同样是对的。
    """
    import asyncio

    pid = await _project(alice)
    await alice.post(T, json={"type": "mock.echo", "project_id": pid, "input": {}})

    for _ in range(20):
        await relay_once()
        entries = await stream.read_since(project_id=pid, last_id="0", count=50)
        if any(f["type"] == "task.created" for _id, f in entries):
            return
        await asyncio.sleep(0.1)

    raise AssertionError("task.created 没有出现在事件流里")


async def test_event_is_never_published_twice(alice: AsyncClient) -> None:
    """同一条发件箱记录不能投递两次，否则前端会收到重复事件。

    Worker 里的常驻中继会与本用例并发跑，所以不能靠"投递前后条数相等"来断言——
    那会与并发产生的事件抢跑。改为对特定任务的 task.created 计数，
    这个数字与谁投递、投递几轮都无关。
    """
    pid = await _project(alice)
    r = await alice.post(T, json={"type": "mock.echo", "project_id": pid, "input": {}})
    task_id = r.json()["id"]

    # 多跑几轮，模拟中继重复扫描
    for _ in range(3):
        await relay_once()

    entries = await stream.read_since(project_id=pid, last_id="0", count=200)
    created = [
        f for _id, f in entries if f["type"] == "task.created" and task_id in f.get("data", "")
    ]
    assert len(created) == 1, f"task.created 出现了 {len(created)} 次"


# ------------------------------------------------------------------ 重放游标


async def test_read_since_excludes_the_cursor_itself(alice: AsyncClient) -> None:
    """重放必须从游标之后开始，含游标本身会导致客户端收到重复。"""
    pid = await _project(alice)
    await alice.post(T, json={"type": "mock.echo", "project_id": pid, "input": {}})
    await relay_once()

    all_entries = await stream.read_since(project_id=pid, last_id="0", count=50)
    assert len(all_entries) >= 1

    first_id = all_entries[0][0]
    rest = await stream.read_since(project_id=pid, last_id=first_id, count=50)
    assert all(eid != first_id for eid, _ in rest)
    assert len(rest) == len(all_entries) - 1


async def test_fresh_cursor_allows_replay(alice: AsyncClient) -> None:
    pid = await _project(alice)
    await alice.post(T, json={"type": "mock.echo", "project_id": pid, "input": {}})
    await relay_once()

    entries = await stream.read_since(project_id=pid, last_id="0", count=10)
    assert await stream.cursor_is_fresh(project_id=pid, entry_id=entries[0][0])


async def test_ancient_cursor_requires_full_sync(alice: AsyncClient) -> None:
    """游标早于保留窗口 → 不能假装无缝续上，必须让客户端全量拉取。"""
    pid = await _project(alice)
    await alice.post(T, json={"type": "mock.echo", "project_id": pid, "input": {}})
    await relay_once()

    assert not await stream.cursor_is_fresh(project_id=pid, entry_id="1-0")


def test_stream_id_comparison_handles_digit_length() -> None:
    """Stream ID 不能按字符串比大小：位数不同时 '9-0' 会大于 '10-0'。"""
    assert parse_id("9-0") < parse_id("10-0")
    assert parse_id("1786902863952-0") < parse_id("1786902863952-1")
    assert parse_id("") == (0, 0)
    assert parse_id("garbage") == (0, 0)


def test_sse_frame_format() -> None:
    frame = sse_frame("123-0", {"type": "task.progress", "data": '{"a":1}'})
    assert frame == 'id: 123-0\nevent: task.progress\ndata: {"a":1}\n\n'
