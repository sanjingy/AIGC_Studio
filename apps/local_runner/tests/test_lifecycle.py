"""连接器的生命周期：取消、Ctrl-C、回传超时、账号与配置隔离。

这个文件补的是复核报告 P2-6 点名的那块空白——`executor.py` 与 `cli.py`
合计八百多行，此前只有 `build_image_prompt` 一个函数被测过，而报告里
两个连接器侧 bug（P0-1 取消锁存、P2-7 Ctrl-C 空转）都落在这块空白里。

**这里一条真实的 CLI 都不起**，所以跑它不花任何额度：执行器换成假的，
`config/read` / `account/read` 的回包换成官方形状的字面量。
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

from apps.local_runner.cli import Outstanding, Worker
from apps.local_runner.client import BridgeClient
from apps.local_runner.executor import (
    AccountCheck,
    CancelCheck,
    CodexExecutor,
    ImageResult,
    ProviderShadowedError,
    TextResult,
    assert_first_party_provider,
    build_thread_config_overrides,
    classify_codex_account,
    classify_codex_login_text,
)
from apps.local_runner.imagegen import (
    ImageOutcome,
    TurnCancelledError,
    TurnFailedError,
    assert_effective_provider,
)
from apps.local_runner.resolve import Executable


@contextlib.contextmanager
def _real_codex_executor() -> Iterator[CodexExecutor]:
    """造一个**真的** `CodexExecutor`，只把"要起进程"的那几步换成假的。

    换掉的只有构造期的四次外部探测（解析可执行文件、`--version`、
    `account/read`、`features list`）——它们都要起真进程。
    `_begin` / `_end` / `cancel` / `_cancel_check` / `run_text` / `run_image`
    **全是生产代码本身**，一行都没有被子类顶掉。

    这一点是这组用例存在的理由：上一版这里是一个重写了 `run_text`、
    自己调 `_begin` / `_end` 的子类，于是"生产入口有没有正确开合取消标志"
    根本没被覆盖——生产的 `run_text` 整个删掉 `_begin` / `_end`，
    那一版用例照样全绿。

    **一条真实 CLI 都不起，一个 turn 都不发，不消耗任何额度。**
    """
    exe = Executable(provider="codex", kind="native", path="C:/fake/codex.exe", argv_prefix=[])
    with (
        mock.patch("apps.local_runner.executor.resolve", return_value=exe),
        mock.patch("apps.local_runner.executor._probe_version", return_value="codex-cli fake"),
        mock.patch(
            "apps.local_runner.executor.probe_codex_account", return_value=AccountCheck(True)
        ),
        mock.patch("apps.local_runner.executor._probe_image_feature", return_value=True),
    ):
        yield CodexExecutor()


class _Gate:
    """一次假 turn 的两个闸门：它开跑了、可以放它走了。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()


class _ScriptedTurns:
    """顶掉 `_turn_text` / `_turn_image` 这两个**底层**方法，其余全走生产路径。

    顶在这一层而不是顶掉 `run_text` / `run_image`：底下是"起 app-server、
    握手、发 turn"，那是唯一非跑真进程不可的部分；而取消语义
    （每请求一份标志、晚到的 cancel 落到谁身上、结束后清不清）全在它上面。

    每次调用取一个 `_Gate`：第 n 次调用用第 n 个闸门，测试因此能精确地让
    第一条卡在途中、第二条直接跑完。
    """

    def __init__(self, executor: CodexExecutor) -> None:
        self._executor = executor
        self._gates: list[_Gate] = []
        self.calls = 0
        #: 每次调用进来时 `executor._current` 是什么。生产入口漏了 `_begin`
        #: 的话这里会是 None——用例据此判死，而不是等下游症状。
        self.current_seen: list[threading.Event | None] = []

    def gate(self, index: int) -> _Gate:
        while len(self._gates) <= index:
            self._gates.append(_Gate())
        return self._gates[index]

    #: 假 turn 的兜底上限。取消没能打到在途请求时（=生产入口坏了），
    #: 这一轮要**失败**，不能挂住整个测试进程——挂住的测试没法判红。
    STUCK_SECONDS = 5.0

    async def _turn(self, should_cancel: CancelCheck) -> None:
        gate = self.gate(self.calls)
        self.calls += 1
        self.current_seen.append(self._executor._current)
        gate.started.set()
        deadline = time.monotonic() + self.STUCK_SECONDS
        # 真实执行面就是这样反复问 `should_cancel()` 的（见
        # `imagegen.generate_text` 的等待循环），所以这里也这样问。
        while not gate.release.is_set():
            if should_cancel():
                raise TurnCancelledError("cancelled mid-flight")
            if time.monotonic() > deadline:
                raise TurnFailedError("scripted turn was never cancelled or released")
            await asyncio.sleep(0.005)
        if should_cancel():
            raise TurnCancelledError("cancelled mid-flight")

    async def text(self, *, prompt: str, timeout_seconds: int, should_cancel: CancelCheck) -> str:
        del prompt, timeout_seconds
        await self._turn(should_cancel)
        return "ok"

    async def image(
        self, *, prompt: str, timeout_seconds: int, should_cancel: CancelCheck
    ) -> ImageOutcome:
        del prompt, timeout_seconds
        await self._turn(should_cancel)
        return ImageOutcome(
            data=b"\x89PNG\r\n\x1a\n",
            mime_type="image/png",
            revised_prompt=None,
            saved_path=Path("C:/fake/out.png"),
            transparent_background=None,
            item_id="item-1",
        )


class CancelIsolationTest(unittest.TestCase):
    """P0-1：取消一次之后，**下一条请求仍然要能跑**——走生产入口证明。

    这条用例在修复之前是红的：那时取消标志是执行器级的一个
    `threading.Event`，`cancel()` 设了之后没有任何地方清，整个进程从此
    对所有后续请求都在第一次检查时抛 `TurnCancelledError`。

    `run_text` 和 `run_image` **两条入口都要覆盖**：它们各自写了一遍
    `_begin` / `try` / `finally: _end`，是两份可以各自写错的代码。
    """

    # -------------------------------------------------------------- 文本

    def test_an_in_flight_text_request_is_cancelled_and_the_next_one_still_runs(self) -> None:
        with _real_codex_executor() as codex:
            turns = _ScriptedTurns(codex)
            codex._turn_text = turns.text  # type: ignore[method-assign]
            turns.gate(1).release.set()  # 第二条不卡，直接跑完

            results: list[TextResult] = []
            thread = threading.Thread(
                target=lambda: results.append(
                    codex.run_text(system="s", user="u", timeout_seconds=30)
                ),
                daemon=True,
            )
            thread.start()
            self.assertTrue(turns.gate(0).started.wait(5), "第一条请求没有真的开跑")
            # 确实在途：生产入口已经把这一条的标志登记成"当前这条"。
            self.assertIsNotNone(codex._current, "run_text 没有调 _begin，取消打不到在途请求上")

            codex.cancel()
            thread.join(5)
            self.assertFalse(thread.is_alive())

            self.assertEqual(results[0].error_code, "cancelled")
            self.assertIsNone(
                codex._current, "run_text 的 finally 没有调 _end，晚到的 cancel 会落到下一条身上"
            )

            second = codex.run_text(system="s", user="u", timeout_seconds=30)
            self.assertIsNone(second.error_code, "取消过一条之后下一条必须还能跑")
            self.assertEqual(second.text, "ok")
            self.assertEqual(turns.calls, 2)
            self.assertNotIn(None, turns.current_seen, "每次进 turn 时都该有一份自己的取消标志")
            self.assertIsNot(turns.current_seen[0], turns.current_seen[1], "取消标志必须每请求一份")

    def test_a_cancel_with_nothing_in_flight_does_not_poison_the_next_text_request(self) -> None:
        with _real_codex_executor() as codex:
            turns = _ScriptedTurns(codex)
            codex._turn_text = turns.text  # type: ignore[method-assign]
            turns.gate(0).release.set()
            turns.gate(1).release.set()

            codex.cancel()  # 心跳线程说"这条没人等了"——但此刻没有在途请求
            first = codex.run_text(system="s", user="u", timeout_seconds=30)
            self.assertIsNone(
                first.error_code, "没有在途请求时收到的 cancel() 不该落到下一条请求身上"
            )
            second = codex.run_text(system="s", user="u", timeout_seconds=30)
            self.assertIsNone(second.error_code)
            self.assertEqual(turns.calls, 2)

    # -------------------------------------------------------------- 出图

    def test_an_in_flight_image_request_is_cancelled_and_the_next_one_still_runs(self) -> None:
        with _real_codex_executor() as codex:
            turns = _ScriptedTurns(codex)
            codex._turn_image = turns.image  # type: ignore[method-assign]
            turns.gate(1).release.set()

            results: list[ImageResult] = []
            thread = threading.Thread(
                target=lambda: results.append(
                    codex.run_image(
                        prompt="p", negative_prompt="", size="1024*1024", timeout_seconds=30
                    )
                ),
                daemon=True,
            )
            thread.start()
            self.assertTrue(turns.gate(0).started.wait(5), "第一条出图请求没有真的开跑")
            self.assertIsNotNone(codex._current, "run_image 没有调 _begin")

            codex.cancel()
            thread.join(5)
            self.assertFalse(thread.is_alive())

            self.assertEqual(results[0].error_code, "cancelled")
            self.assertIsNone(codex._current, "run_image 的 finally 没有调 _end")

            second = codex.run_image(
                prompt="p", negative_prompt="", size="1024*1024", timeout_seconds=30
            )
            self.assertIsNone(second.error_code, "取消过一条出图之后下一条必须还能跑")
            self.assertEqual(second.mime_type, "image/png")
            self.assertEqual(turns.calls, 2)
            self.assertNotIn(None, turns.current_seen)
            self.assertIsNot(turns.current_seen[0], turns.current_seen[1])

    def test_cancelling_text_does_not_poison_a_following_image_request(self) -> None:
        """两条入口共用同一份 `_current`，所以要证明它们之间也不互相污染。"""
        with _real_codex_executor() as codex:
            text_turns = _ScriptedTurns(codex)
            image_turns = _ScriptedTurns(codex)
            codex._turn_text = text_turns.text  # type: ignore[method-assign]
            codex._turn_image = image_turns.image  # type: ignore[method-assign]
            image_turns.gate(0).release.set()

            results: list[TextResult] = []
            thread = threading.Thread(
                target=lambda: results.append(
                    codex.run_text(system="s", user="u", timeout_seconds=30)
                ),
                daemon=True,
            )
            thread.start()
            self.assertTrue(text_turns.gate(0).started.wait(5))
            codex.cancel()
            thread.join(5)
            self.assertEqual(results[0].error_code, "cancelled")

            outcome = codex.run_image(
                prompt="p", negative_prompt="", size="1024*1024", timeout_seconds=30
            )
            self.assertIsNone(outcome.error_code)

    # -------------------------------------------------------------- 晚到的 cancel

    def test_a_late_cancel_only_sets_an_abandoned_flag(self) -> None:
        """晚到的 cancel()（上一条的心跳）不该污染已经开始的下一条。"""
        with _real_codex_executor() as codex:
            turns = _ScriptedTurns(codex)
            codex._turn_text = turns.text  # type: ignore[method-assign]
            turns.gate(0).release.set()

            stale = codex._begin()
            codex._end(stale)
            codex.cancel()  # 指向 stale 的那次 cancel，此刻 _current 已是 None
            self.assertFalse(stale.is_set())

            result = codex.run_text(system="s", user="u", timeout_seconds=30)
            self.assertIsNone(result.error_code)
            self.assertIsNot(turns.current_seen[0], stale)

    def test_an_external_should_cancel_still_reaches_the_turn(self) -> None:
        """调用方（`cli.py` 用服务端心跳实现的那个）给的取消判据不能被吞掉。"""
        with _real_codex_executor() as codex:
            turns = _ScriptedTurns(codex)
            codex._turn_text = turns.text  # type: ignore[method-assign]

            result = codex.run_text(
                system="s", user="u", timeout_seconds=30, should_cancel=lambda: True
            )
            self.assertEqual(result.error_code, "cancelled")
            self.assertIsNone(codex._current)


# ---------------------------------------------------------------- Ctrl-C


class _RecordingClient:
    """只记回传，不发任何 HTTP。"""

    def __init__(self) -> None:
        self.posted: list[dict[str, Any]] = []
        self.base = "http://127.0.0.1:3100"

    def post_result(self, request_id: str, **fields: Any) -> None:
        self.posted.append({"request_id": request_id, **fields})

    def heartbeat(self, *args: Any, **kwargs: Any) -> bool:
        return True


class _ExplodingExecutor:
    provider = "codex"

    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.cancelled = 0

    def capabilities(self) -> tuple[str, ...]:
        return ("text", "image")

    def unavailable_reason(self) -> str | None:
        return None

    def version(self) -> str | None:
        return "fake"

    def run_text(self, **kwargs: Any) -> TextResult:
        raise self._error

    def run_image(self, **kwargs: Any) -> ImageResult:
        raise self._error

    def cancel(self) -> None:
        self.cancelled += 1


class KeyboardInterruptTest(unittest.TestCase):
    """P2-7：Ctrl-C 落在跑活当中时，服务端要**立刻**收到一次 `cancelled`。

    修复前这条是红的：`_handle` 的 finally 先把 `_outstanding` 清成 None，
    等 `main()` 的 `except KeyboardInterrupt` 调 `cancel_outstanding()` 时
    它第一行就 return，用户要等满整个超时（默认 420 秒）才看到失败。
    """

    def _worker(self, error: BaseException) -> tuple[Worker, _RecordingClient, Any]:
        client = _RecordingClient()
        executor = _ExplodingExecutor(error)
        worker = Worker(client, executor=executor, poll_seconds=0.01)  # type: ignore[arg-type]
        return worker, client, executor

    def test_ctrl_c_reports_cancelled_before_it_propagates(self) -> None:
        worker, client, executor = self._worker(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            worker._handle(
                {
                    "request_id": "req-1",
                    "lease_token": "lease-1",
                    "kind": "text",
                    "timeout_seconds": 60,
                    "text": {"system": "s", "user": "u"},
                }
            )
        self.assertEqual(len(client.posted), 1)
        self.assertEqual(client.posted[0]["error_code"], "cancelled")
        self.assertEqual(client.posted[0]["request_id"], "req-1")
        self.assertGreaterEqual(executor.cancelled, 1, "要先让本机停手再回报")

    def test_the_second_gate_still_covers_ctrl_c_between_requests(self) -> None:
        """Ctrl-C 落在两条请求**之间**时，兜底那条路仍要能收掉在途的那条。"""
        worker, client, _executor = self._worker(KeyboardInterrupt())
        worker._outstanding = Outstanding("req-2", "lease-2", deadline=0.0)
        worker.cancel_outstanding()
        self.assertEqual([p["error_code"] for p in client.posted], ["cancelled"])

    def test_nothing_is_posted_twice_for_one_interrupt(self) -> None:
        """`_handle` 回报之后 outstanding 已清空，兜底那条不该再发一次。"""
        worker, client, _executor = self._worker(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            worker._handle(
                {
                    "request_id": "req-3",
                    "lease_token": "lease-3",
                    "kind": "image",
                    "timeout_seconds": 60,
                    "image": {"prompt": "p"},
                }
            )
        worker.cancel_outstanding()
        self.assertEqual(len(client.posted), 1)


# ---------------------------------------------------------------- 回传超时


class ResultTimeoutTest(unittest.TestCase):
    """P3-12：回传超时按体积放大，但不超过这一轮剩下的绝对预算。"""

    def test_small_bodies_keep_the_floor(self) -> None:
        self.assertEqual(BridgeClient.result_timeout(0), 30)
        self.assertEqual(BridgeClient.result_timeout(1_000), 30)

    def test_a_multi_megabyte_image_gets_more_than_thirty_seconds(self) -> None:
        # 本次冒烟那张真图：2,471,345 字节 PNG → base64 约 3.3 MB。
        base64_bytes = 2_471_345 * 4 // 3
        self.assertGreater(
            BridgeClient.result_timeout(base64_bytes),
            30,
            "固定 30 秒会让已经画出来的图白丢——额度花了、图有了、没人收",
        )

    def test_it_is_capped(self) -> None:
        self.assertEqual(BridgeClient.result_timeout(10**9), 600)

    def test_the_remaining_budget_wins_when_it_is_smaller(self) -> None:
        base64_bytes = 4_000_000
        self.assertEqual(BridgeClient.result_timeout(base64_bytes, budget_left_seconds=12.7), 12)

    def test_an_exhausted_budget_still_gets_one_second(self) -> None:
        for left in (0.0, -5.0):
            self.assertEqual(BridgeClient.result_timeout(1_000, budget_left_seconds=left), 1)


# ---------------------------------------------------------------- 账号身份


class CodexAccountTest(unittest.TestCase):
    """`account/read` 的结构化判据。形状取自官方 schema 的 `Account`。"""

    def test_a_chatgpt_subscription_is_usable(self) -> None:
        check = classify_codex_account({"type": "chatgpt", "email": "x", "planType": "plus"})
        self.assertTrue(check.usable)
        self.assertIsNone(check.reason)

    def test_an_api_key_account_is_refused(self) -> None:
        check = classify_codex_account({"type": "apiKey"})
        self.assertFalse(check.usable)
        assert check.reason is not None
        self.assertIn("API Key", check.reason)

    def test_a_third_party_account_is_refused(self) -> None:
        check = classify_codex_account({"type": "amazonBedrock"})
        self.assertFalse(check.usable)

    def test_an_unreadable_answer_is_refused_rather_than_assumed_good(self) -> None:
        for payload in (None, {}, "chatgpt", {"type": ""}):
            self.assertFalse(classify_codex_account(payload).usable)

    def test_the_text_fallback_only_backs_up_the_structured_one(self) -> None:
        self.assertTrue(classify_codex_login_text(0, "Logged in using ChatGPT").usable)
        self.assertFalse(classify_codex_login_text(0, "Not logged in").usable)
        self.assertFalse(classify_codex_login_text(1, "Logged in using ChatGPT").usable)
        self.assertFalse(classify_codex_login_text(0, "Logged in using an API key").usable)
        self.assertFalse(classify_codex_login_text(0, "已登录").usable)


class ProviderShadowingTest(unittest.TestCase):
    """`modelProvider` 只钉住名字，不保证它还是官方那条通道。"""

    def test_a_clean_config_passes(self) -> None:
        assert_first_party_provider({"model_providers": {}}, provider="openai")
        assert_first_party_provider({}, provider="openai")

    def test_an_env_key_on_the_builtin_name_is_refused(self) -> None:
        with self.assertRaises(ProviderShadowedError):
            assert_first_party_provider(
                {"model_providers": {"openai": {"env_key": "OPENAI_API_KEY"}}},
                provider="openai",
            )

    def test_a_custom_base_url_on_the_builtin_name_is_refused(self) -> None:
        with self.assertRaises(ProviderShadowedError):
            assert_first_party_provider(
                {"model_providers": {"openai": {"base_url": "https://proxy.example/v1"}}},
                provider="openai",
            )

    def test_a_global_openai_base_url_is_refused(self) -> None:
        with self.assertRaises(ProviderShadowedError):
            assert_first_party_provider(
                {"openai_base_url": "https://proxy.example/v1"}, provider="openai"
            )

    def test_another_providers_env_key_is_none_of_our_business(self) -> None:
        # 用户配了别的 provider 是他的自由；我们只管**这一条线程用的那个**。
        assert_first_party_provider(
            {"model_providers": {"my-proxy": {"env_key": "K"}}}, provider="openai"
        )

    def test_the_thread_start_echo_is_checked(self) -> None:
        # `ThreadStartResponse` 的 required 里就有 modelProvider，官方回显。
        assert_effective_provider({"modelProvider": "openai"}, expected="openai")
        with self.assertRaises(TurnFailedError):
            assert_effective_provider({"modelProvider": "my-proxy"}, expected="openai")
        # 老版本不回显这个字段时不判死——不能因为读不到就把功能关掉。
        assert_effective_provider({}, expected="openai")
        assert_effective_provider({"modelProvider": "whatever"}, expected=None)


class ThreadConfigOverrideTest(unittest.TestCase):
    """逐线程收窄权限的覆盖到底长什么样。

    形状不是猜的：2026-09-07 在本机 codex-cli 0.153.4 上用官方查询方法
    实测过（`config/read` + 带 `threadId` 的 `mcpServerStatus/list`，
    全程没有发过任何 turn，未消耗额度）：`mcp_servers: {}` 是**合并**语义，
    两台服务器仍然 `connected`；按名给一个带 `enabled: false` 的最小定义，
    对应那台才会变成 `disabled`。
    """

    CONFIG: ClassVar[dict[str, Any]] = {
        "mcp_servers": {
            "node_repl": {
                "command": "C:/x/node_repl.exe",
                "args": ["--serve"],
                "env": {"SECRET": "s"},
                "tool_timeout_sec": "",
            }
        },
        "notify": ["C:/x/codex-computer-use.exe", "turn-ended"],
        "hooks": {"PreToolUse": [{"handler": "x"}], "state": {"a": 1}},
    }

    def test_every_server_is_disabled_by_name_not_by_an_empty_object(self) -> None:
        overrides = build_thread_config_overrides(self.CONFIG)
        self.assertEqual(
            overrides["mcp_servers"],
            {"node_repl": {"command": "C:/x/node_repl.exe", "args": [], "enabled": False}},
        )
        self.assertNotEqual(overrides["mcp_servers"], {}, "空对象是合并语义，实测不清空")

    def test_the_server_definition_stays_minimal(self) -> None:
        # 把 config/read 读回来的定义原样塞回去会被配置加载器拒掉
        # （`tool_timeout_sec` 读回来是 ""，写进去要 f64），
        # 而且我们也不该把用户的 env 再抄一遍。
        servers = build_thread_config_overrides(self.CONFIG)["mcp_servers"]
        assert isinstance(servers, dict)
        definition = servers["node_repl"]
        assert isinstance(definition, dict)
        self.assertEqual(set(definition), {"command", "args", "enabled"})

    def test_notify_is_cleared(self) -> None:
        self.assertEqual(build_thread_config_overrides(self.CONFIG)["notify"], [])

    def test_hooks_are_cleared_per_event_but_state_is_left_alone(self) -> None:
        # `hooks.state` 的值是 map 不是 list，一起当事件清空会让 thread/start
        # 直接报 `invalid type: sequence, expected a map`（实测）。
        hooks = build_thread_config_overrides(self.CONFIG)["hooks"]
        self.assertEqual(hooks, {"PreToolUse": []})

    def test_a_config_without_hooks_does_not_grow_an_empty_key(self) -> None:
        overrides = build_thread_config_overrides({"mcp_servers": {}})
        self.assertNotIn("hooks", overrides)
        self.assertEqual(overrides, {"mcp_servers": {}, "notify": []})

    def test_a_server_without_a_command_still_gets_disabled(self) -> None:
        # url 传输的服务器没有 command。定义仍要能被解析，`enabled` 才是
        # 真正起作用的那一位。
        overrides = build_thread_config_overrides({"mcp_servers": {"remote": {"url": "https://x"}}})
        self.assertEqual(
            overrides["mcp_servers"],
            {"remote": {"command": "", "args": [], "enabled": False}},
        )


if __name__ == "__main__":
    unittest.main()
