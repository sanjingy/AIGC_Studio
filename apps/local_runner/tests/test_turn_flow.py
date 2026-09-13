"""一轮对话的形状：起线程、发起一轮、拿产物、打断。

这些用例喂的是**固定的协议帧**，一次模型调用都不发生，也不花任何额度。
它们盯的是三件历史上真的写错过、而且错了不会有任何报错的事：

1. `turn/interrupt` 的必填参数是 `{threadId, turnId}` 两个（官方 schema 的
   `required`）。旧实现只传 threadId —— 打断被对端拒掉，于是"用户取消了"
   变成"界面上取消了、桌面还在跑"，烧的是用户自己的订阅额度。
2. 文本产物是 `item/completed` 里的 `agentMessage`。把工具事件、推理摘要
   当成产出，拿到的会是"我先看一下需求"这种句子。
3. `error` 通知的形状是 `{error: {...}}`，不是 `{message: ...}`。
   读错字段的结果是错误说明永远是 "unknown"。
"""

from __future__ import annotations

import asyncio
import base64
import unittest
from typing import Any

from apps.local_runner import imagegen

PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
)


class FakeConnection:
    """记下每一次 request，按方法名给一个固定回答。"""

    def __init__(self, *, turn_id: str | None = "turn-1", delay: float = 0.0) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.timeouts: dict[str, float] = {}
        self._turn_id = turn_id
        self._delay = delay

    async def request(self, method: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        self.calls.append((method, params or {}))
        if "timeout_seconds" in kw:
            self.timeouts[method] = float(kw["timeout_seconds"])
        if self._delay and method == "thread/start":
            await asyncio.sleep(self._delay)
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            return {"turn": {"id": self._turn_id}} if self._turn_id else {}
        return {}

    def params_of(self, method: str) -> dict[str, Any]:
        for name, params in self.calls:
            if name == method:
                return params
        raise AssertionError(f"没有调用过 {method}")

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]


def _item_completed(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return "item/completed", {"item": item, "threadId": "thread-1", "turnId": "turn-1"}


class ThreadStartTest(unittest.IsolatedAsyncioTestCase):
    async def test_thread_is_readonly_ephemeral_and_never_asks_for_approval(self) -> None:
        """四个参数都是安全边界，不是风格选择。

        `approvalPolicy: never` 尤其重要：非交互下没人能回答审批请求，
        不写死它，一次审批就会把这一轮挂到超时为止。
        """
        conn = FakeConnection()
        await imagegen.start_thread(conn, cwd="/tmp/x")  # type: ignore[arg-type]
        params = conn.params_of("thread/start")
        self.assertEqual(params["sandbox"], "read-only")
        self.assertEqual(params["approvalPolicy"], "never")
        self.assertTrue(params["ephemeral"])
        self.assertEqual(params["cwd"], "/tmp/x")
        # 不传 model 时不许出现任何模型 id。
        self.assertNotIn("model", params)


class InterruptTest(unittest.IsolatedAsyncioTestCase):
    async def test_interrupt_carries_both_ids(self) -> None:
        conn = FakeConnection()
        thread_id = await imagegen.start_thread(conn, cwd="/tmp/x")  # type: ignore[arg-type]
        turn_id = await imagegen.start_turn(
            conn,  # type: ignore[arg-type]
            thread_id=thread_id,
            prompt="画一张",
            timeout_seconds=5,
        )
        await imagegen.interrupt(conn, thread_id=thread_id, turn_id=turn_id)  # type: ignore[arg-type]
        params = conn.params_of("turn/interrupt")
        self.assertEqual(params, {"threadId": "thread-1", "turnId": "turn-1"})

    async def test_no_turn_id_means_no_interrupt_call(self) -> None:
        """拿不到 turnId 时不要发一个必定被拒的请求——它只会在日志里
        制造一条假的"已打断"。"""
        conn = FakeConnection(turn_id=None)
        await imagegen.interrupt(conn, thread_id="t", turn_id=None)  # type: ignore[arg-type]
        self.assertNotIn("turn/interrupt", conn.methods())

    async def test_failure_interrupts_the_turn(self) -> None:
        """这一轮失败/超时/被取消，都要打断——否则模型在后台继续跑，
        继续烧订阅额度，而结果没有人会收。"""
        conn = FakeConnection()
        watcher = imagegen.TurnWatcher(allowed_roots=[])
        watcher.feed("turn/completed", {})  # 没出图
        with self.assertRaises(imagegen.NoImageProducedError):
            await imagegen.generate_image(
                conn,  # type: ignore[arg-type]
                watcher,
                prompt="画一张",
                cwd="/tmp/x",
                timeout_seconds=5,
            )
        self.assertIn("turn/interrupt", conn.methods())


class TextTurnTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_final_agent_message_is_returned(self) -> None:
        watcher = imagegen.TurnWatcher(allowed_roots=[])
        watcher.feed(*_item_completed({"type": "reasoning", "id": "r1", "summary": "先想想"}))
        watcher.feed(
            *_item_completed(
                {"type": "commandExecution", "id": "c1", "aggregatedOutput": "工具输出，不是答案"}
            )
        )
        watcher.feed(*_item_completed({"type": "agentMessage", "id": "a1", "text": "中间说明"}))
        watcher.feed(
            *_item_completed({"type": "agentMessage", "id": "a2", "text": '{"route":"NOVEL"}'})
        )
        watcher.feed("turn/completed", {})
        text = await watcher.wait_for_text(timeout_seconds=5)
        self.assertEqual(text, '{"route":"NOVEL"}')

    async def test_error_notification_message_is_read_from_the_right_field(self) -> None:
        watcher = imagegen.TurnWatcher(allowed_roots=[])
        watcher.feed(
            "error",
            {"error": {"message": "model unavailable"}, "threadId": "t", "willRetry": False},
        )
        with self.assertRaises(imagegen.TurnFailedError) as ctx:
            await watcher.wait_for_text(timeout_seconds=5)
        self.assertIn("model unavailable", str(ctx.exception))

    async def test_turn_without_any_message_is_empty_not_a_hang(self) -> None:
        watcher = imagegen.TurnWatcher(allowed_roots=[])
        watcher.feed("turn/completed", {})
        self.assertEqual(await watcher.wait_for_text(timeout_seconds=5), "")


class BudgetTest(unittest.IsolatedAsyncioTestCase):
    """整条请求共用**一份**时间预算，先花掉的从后面扣。

    服务端发下来的 `timeout_seconds` 已经扣掉了排队时间（绝对截止时间）。
    起线程、发起一轮、等产物再各给一份完整的值，加起来必然超过服务端的
    截止时间——那时服务端已经判超时并清掉了这条请求，桌面却还在画，
    烧的是用户自己的订阅额度，而结果没有人会收。
    """

    async def test_each_step_is_capped_by_the_total(self) -> None:
        conn = FakeConnection()
        watcher = imagegen.TurnWatcher(allowed_roots=[])
        watcher.feed(
            *_item_completed(
                {
                    "type": "imageGeneration",
                    "id": "i1",
                    "status": "completed",
                    "result": base64.b64encode(PNG).decode("ascii"),
                }
            )
        )
        await imagegen.generate_image(
            conn,  # type: ignore[arg-type]
            watcher,
            prompt="画一张",
            cwd="/tmp/x",
            timeout_seconds=8,
        )
        assert conn.timeouts["thread/start"] <= 8
        assert conn.timeouts["turn/start"] <= 8

    async def test_time_already_spent_is_deducted_from_the_next_step(self) -> None:
        conn = FakeConnection(delay=1.2)
        watcher = imagegen.TurnWatcher(allowed_roots=[])
        watcher.feed("turn/completed", {})
        with self.assertRaises(imagegen.NoImageProducedError):
            await imagegen.generate_image(
                conn,  # type: ignore[arg-type]
                watcher,
                prompt="画一张",
                cwd="/tmp/x",
                timeout_seconds=6,
            )
        # 起线程花掉了 1.2 秒，后面那一步只剩不到 5 秒。
        assert conn.timeouts["turn/start"] < 5.0


class CancelTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_check_breaks_the_wait(self) -> None:
        """服务端说"这条没人等了"之后必须立刻停，不能等到超时。"""
        watcher = imagegen.TurnWatcher(allowed_roots=[])
        cancelled = False

        def should_cancel() -> bool:
            return cancelled

        task = asyncio.create_task(
            watcher.wait_for_image(timeout_seconds=30, should_cancel=should_cancel)
        )
        await asyncio.sleep(0.05)
        cancelled = True
        with self.assertRaises(imagegen.TurnCancelledError):
            await asyncio.wait_for(task, timeout=5)

    async def test_image_still_arrives_when_not_cancelled(self) -> None:
        watcher = imagegen.TurnWatcher(allowed_roots=[])
        watcher.feed(
            *_item_completed(
                {
                    "type": "imageGeneration",
                    "id": "i1",
                    "status": "completed",
                    "result": base64.b64encode(PNG).decode("ascii"),
                    "revisedPrompt": "被模型改写过的提示词",
                }
            )
        )
        outcome = await watcher.wait_for_image(timeout_seconds=5, should_cancel=lambda: False)
        self.assertEqual(outcome.data, PNG)
        self.assertEqual(outcome.mime_type, "image/png")
        self.assertEqual(outcome.revised_prompt, "被模型改写过的提示词")


if __name__ == "__main__":
    unittest.main()
