"""Redis 邮箱本身的语义。用真 Redis，超时都压到一两秒。

这里验的是"钱已经花掉了"那一侧的正确性：

* 超时之后不能还留着一条请求让连接器下次捞出来再跑一遍（那是白烧一次
  订阅额度）；
* 结果的一次性——过期、重放、租约不符都不能覆盖任何东西；
* **绝对截止时间**：在队列里躺过的时间要从超时里扣掉，不能一出队就重新
  发一份完整的三分钟；
* **原子性**：入队与深度检查、出队与占租约、清理的两步，各自不能被插入。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest

from apps.api.modules.local_runtime import transport

pytestmark = pytest.mark.integration

PROVIDER = "codex"


@pytest.fixture(autouse=True)
async def _clean() -> AsyncIterator[None]:
    async def _wipe() -> None:
        r = transport._r()
        try:
            await r.delete(
                transport.queue_key(PROVIDER, "text"),
                transport.queue_key(PROVIDER, "image"),
                transport.heartbeat_key(PROVIDER),
            )
        finally:
            await transport._release(r)

    await _wipe()
    yield
    await _wipe()


def _text_job() -> transport.TextJob:
    return transport.TextJob(
        system="系统", user="用户", schema_name="StoryOutline", max_output_tokens=256
    )


async def _enqueue(request_id: uuid.UUID, *, timeout_seconds: int = 30) -> bool:
    return await transport.enqueue(
        provider=PROVIDER,
        kind="text",
        request_id=request_id,
        timeout_seconds=timeout_seconds,
        text=_text_job(),
    )


class TestHeartbeat:
    async def test_absent_until_touched_then_expires_on_its_own(self) -> None:
        assert await transport.runner_connected(PROVIDER) is False
        await transport.touch_heartbeat(PROVIDER, kinds=["text"])
        assert await transport.runner_connected(PROVIDER) is True

        r = transport._r()
        try:
            ttl = await r.ttl(transport.heartbeat_key(PROVIDER))
        finally:
            await transport._release(r)
        # 心跳必须自己过期：连接器崩了没人来删这个 key。
        assert 0 < int(ttl) <= transport.HEARTBEAT_TTL_SECONDS

    async def test_capabilities_are_what_the_runner_reported(self) -> None:
        """能力是连接器自报的实况，不是服务端猜的。

        界面上那句"本机可以出图"直接来自这里——写死在服务端就成了一个
        点下去必然失败的假入口。
        """
        await transport.touch_heartbeat(PROVIDER, kinds=["image", "text"], version="codex-cli 0.1")
        state = await transport.runner_state(PROVIDER)
        assert state is not None
        assert set(state.kinds) == {"text", "image"}
        assert state.version == "codex-cli 0.1"

        await transport.touch_heartbeat(PROVIDER, kinds=["text"])
        state = await transport.runner_state(PROVIDER)
        assert state is not None
        assert set(state.kinds) == {"text"}


class TestQueueSeparation:
    async def test_a_text_only_runner_never_gets_an_image_request(self) -> None:
        """文本和图片分两条队列。混在一起的话，只会跑文本的那台连接器
        会捞走一条出图请求，然后回一个 unsupported —— 用户白等一轮。"""
        image_id = uuid.uuid4()
        assert await transport.enqueue(
            provider=PROVIDER,
            kind="image",
            request_id=image_id,
            timeout_seconds=30,
            image=transport.ImageJob(prompt="画", negative_prompt="", size="1024*1024", n=1),
        )
        assert await transport.take(provider=PROVIDER, kind="text") is None
        leased = await transport.take(provider=PROVIDER, kind="image")
        assert leased is not None
        assert leased.kind == "image"
        assert leased.image is not None
        assert leased.image.prompt == "画"
        assert leased.text is None


class TestDeadline:
    async def test_time_spent_queued_is_deducted(self) -> None:
        """在队列里躺过的时间要从超时里扣掉。

        第一版是"出队时给一份全新的 timeout"——一条躺了两分钟的请求被
        取走时又拿到完整三分钟，而等待方早就超时了，桌面却还在跑。
        """
        request_id = uuid.uuid4()
        await _enqueue(request_id, timeout_seconds=10)
        await asyncio.sleep(2)
        leased = await transport.take(provider=PROVIDER, kind="text")
        assert leased is not None
        assert leased.timeout_seconds <= 9

    async def test_an_expired_request_is_never_handed_out(self) -> None:
        """剩余时间不够就直接丢弃，不交给连接器——派出去也跑不完，
        只是白烧一次订阅额度。"""
        request_id = uuid.uuid4()
        await _enqueue(request_id, timeout_seconds=6)
        await asyncio.sleep(2.5)
        # 剩下 3 秒多，低于 _MIN_REMAINING_MS（5 秒）
        assert await transport.take(provider=PROVIDER, kind="text") is None


class TestAtomicity:
    async def test_queue_depth_is_enforced_even_under_concurrency(self) -> None:
        """深度检查与入队是一个动作。分成两步的话，同时进来的十几条
        全都会看到"还没满"。"""
        results = await asyncio.gather(
            *[_enqueue(uuid.uuid4()) for _ in range(transport.MAX_QUEUE_DEPTH + 6)]
        )
        assert sum(1 for ok in results if ok) == transport.MAX_QUEUE_DEPTH
        assert await transport.queue_depth(PROVIDER, "text") == transport.MAX_QUEUE_DEPTH

    async def test_two_runners_cannot_get_the_same_request(self) -> None:
        """同一条请求被两台连接器同时拿走 = 同一次生成跑两遍，
        订阅额度扣两次。"""
        for _ in range(5):
            await _enqueue(uuid.uuid4())
        leased = await asyncio.gather(
            *[transport.take(provider=PROVIDER, kind="text") for _ in range(5)]
        )
        ids = [item.request_id for item in leased if item is not None]
        assert len(ids) == len(set(ids))

    async def test_cleanup_removes_the_queue_entry_and_all_keys(self) -> None:
        request_id = uuid.uuid4()
        await _enqueue(request_id)
        assert await transport.queue_depth(PROVIDER, "text") == 1

        await transport.cleanup(provider=PROVIDER, kind="text", request_id=request_id)
        assert await transport.queue_depth(PROVIDER, "text") == 0
        assert await transport.take(provider=PROVIDER, kind="text") is None

        r = transport._r()
        try:
            for key in (
                transport.request_key(request_id),
                transport.lease_key(request_id),
                transport.reply_key(request_id),
            ):
                assert await r.exists(key) == 0
        finally:
            await transport._release(r)


class TestOneShotCompletion:
    async def test_happy_path(self) -> None:
        request_id = uuid.uuid4()
        await _enqueue(request_id)
        leased = await transport.take(provider=PROVIDER, kind="text")
        assert leased is not None

        outcome = await transport.complete(
            request_id=request_id,
            lease_token=leased.lease_token,
            reply=transport.Reply(text="产出", model_id="local-cli.codex"),
        )
        assert outcome == transport.COMPLETE_OK

        reply = await transport.await_reply(request_id=request_id, timeout_seconds=2)
        assert reply is not None
        assert reply.text == "产出"

    async def test_a_second_completion_is_refused(self) -> None:
        request_id = uuid.uuid4()
        await _enqueue(request_id)
        leased = await transport.take(provider=PROVIDER, kind="text")
        assert leased is not None
        assert (
            await transport.complete(
                request_id=request_id,
                lease_token=leased.lease_token,
                reply=transport.Reply(text="第一次"),
            )
            == transport.COMPLETE_OK
        )
        assert (
            await transport.complete(
                request_id=request_id,
                lease_token=leased.lease_token,
                reply=transport.Reply(text="第二次"),
            )
            == transport.COMPLETE_NOT_OUTSTANDING
        )

    async def test_a_wrong_lease_changes_nothing(self) -> None:
        request_id = uuid.uuid4()
        await _enqueue(request_id)
        leased = await transport.take(provider=PROVIDER, kind="text")
        assert leased is not None
        assert (
            await transport.complete(
                request_id=request_id,
                lease_token="y" * 64,
                reply=transport.Reply(text="冒名顶替"),
            )
            == transport.COMPLETE_LEASE_MISMATCH
        )
        # 真正的租约仍然有效。
        assert (
            await transport.complete(
                request_id=request_id,
                lease_token=leased.lease_token,
                reply=transport.Reply(text="正主"),
            )
            == transport.COMPLETE_OK
        )

    async def test_completing_after_cleanup_is_refused(self) -> None:
        """清理之后回来的结果不能复活一条已经没人等的请求。"""
        request_id = uuid.uuid4()
        await _enqueue(request_id)
        leased = await transport.take(provider=PROVIDER, kind="text")
        assert leased is not None
        await transport.cleanup(provider=PROVIDER, kind="text", request_id=request_id)
        assert (
            await transport.complete(
                request_id=request_id,
                lease_token=leased.lease_token,
                reply=transport.Reply(text="迟到"),
            )
            == transport.COMPLETE_NOT_OUTSTANDING
        )


class TestOutstanding:
    async def test_outstanding_flips_to_false_after_cleanup(self) -> None:
        """连接器靠它知道"用户取消了"。租约一没就该立刻停手，
        不然桌面还要把这一轮跑满，烧的是用户自己的订阅额度。"""
        request_id = uuid.uuid4()
        await _enqueue(request_id)
        leased = await transport.take(provider=PROVIDER, kind="text")
        assert leased is not None
        assert await transport.is_outstanding(request_id) is True

        await transport.cleanup(provider=PROVIDER, kind="text", request_id=request_id)
        assert await transport.is_outstanding(request_id) is False
