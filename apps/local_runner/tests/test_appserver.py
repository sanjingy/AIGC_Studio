"""app-server JSON-RPC 客户端：分帧、握手、请求关联、超时、拆进程树。

**用一个假的 app-server（本机 `python -c`）跑真子进程**，不碰 Codex、
不花任何订阅额度。假进程说的是真协议：JSONL、数字 id、`initialize` 握手。

这些边界条件抄自 Orca（MIT，见 THIRD_PARTY_NOTICES.md），
所以也照着它的边界来测。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from typing import Any

from apps.local_runner import appserver

# 一个最小的假 app-server：读 JSONL 请求，按 id 回响应，认得 initialize。
FAKE_SERVER = r"""
import json, sys, time
mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
if mode == "noise-first":
    sys.stdout.write("starting up, not json\n")
    sys.stdout.write("{ broken json\n")
    sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        if mode == "slow-handshake":
            time.sleep(30)
        sys.stdout.write(json.dumps({"id": mid, "result": {"ok": True}}) + "\n")
        sys.stdout.flush()
    elif method == "initialized":
        continue
    elif method == "echo":
        sys.stdout.write(json.dumps({"id": mid, "result": msg.get("params")}) + "\n")
        sys.stdout.flush()
    elif method == "notify-then-reply":
        sys.stdout.write(json.dumps({"method": "item/completed",
                                     "params": {"hello": "world"}}) + "\n")
        sys.stdout.write(json.dumps({"id": mid, "result": "done"}) + "\n")
        sys.stdout.flush()
    elif method == "fail":
        sys.stdout.write(json.dumps({"id": mid,
                                     "error": {"code": -32000, "message": "nope"}}) + "\n")
        sys.stdout.flush()
    elif method == "silent":
        continue
    elif method == "die":
        sys.stderr.write("fatal: exploded\n")
        sys.stderr.flush()
        sys.exit(3)
    elif method == "hang-forever":
        time.sleep(600)
"""

MISSING_SUBCOMMAND = r"""
import sys
sys.stderr.write("error: unrecognized subcommand 'app-server'\n")
sys.exit(2)
"""


def fake_argv(mode: str = "normal", *, script: str = FAKE_SERVER) -> list[str]:
    return [sys.executable, "-c", script, mode]


class HandshakeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cwd = tempfile.mkdtemp(prefix="appserver-test-")

    async def _open(self, mode: str = "normal", **kwargs: Any) -> appserver.AppServerConnection:
        return await appserver.open_connection(
            fake_argv(mode), cwd=self.cwd, env=dict(os.environ), **kwargs
        )

    async def test_handshake_and_round_trip(self) -> None:
        conn = await self._open()
        try:
            self.assertEqual(await conn.request("echo", {"a": 1}), {"a": 1})
        finally:
            await conn.close()

    async def test_non_protocol_lines_are_skipped_not_fatal(self) -> None:
        """子进程 stdout 上混入启动横幅或坏帧是常态。
        一行坏帧不该杀掉整条连接。"""
        conn = await self._open("noise-first")
        try:
            self.assertEqual(await conn.request("echo", {"ok": True}), {"ok": True})
        finally:
            await conn.close()

    async def test_missing_app_server_subcommand_is_reported_as_unsupported(self) -> None:
        with self.assertRaises(appserver.AppServerUnsupportedError):
            await appserver.open_connection(
                [sys.executable, "-c", MISSING_SUBCOMMAND],
                cwd=self.cwd,
                env=dict(os.environ),
            )

    async def test_handshake_timeout_reaps_the_child(self) -> None:
        """握手挂住时必须先把子进程收掉再抛——留一个跑着的 app-server
        在后台，既占内存又可能继续持有账号会话。"""
        started = asyncio.get_running_loop().time()
        with self.assertRaises(appserver.AppServerTimeoutError):
            await appserver.open_connection(
                fake_argv("slow-handshake"),
                cwd=self.cwd,
                env=dict(os.environ),
            )
        # 不能等满 30 秒的 sleep：握手上限是 15s，加上拆除余量。
        self.assertLess(asyncio.get_running_loop().time() - started, 28)


class RequestTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cwd = tempfile.mkdtemp(prefix="appserver-test-")
        self.conn = await appserver.open_connection(fake_argv(), cwd=self.cwd, env=dict(os.environ))

    async def asyncTearDown(self) -> None:
        await self.conn.close()

    async def test_responses_are_matched_by_id_not_by_order(self) -> None:
        """通知可以插在请求和它的响应之间；按到达顺序配对会串号。"""
        a = asyncio.create_task(self.conn.request("notify-then-reply"))
        b = asyncio.create_task(self.conn.request("echo", {"n": 2}))
        self.assertEqual(await a, "done")
        self.assertEqual(await b, {"n": 2})

    async def test_error_body_becomes_a_typed_error(self) -> None:
        with self.assertRaises(appserver.AppServerRequestError) as ctx:
            await self.conn.request("fail")
        self.assertEqual(ctx.exception.code, -32000)

    async def test_per_request_timeout_does_not_kill_the_connection(self) -> None:
        """超时是这一次调用的，不是这条连接的——连接活得比任何一次调用都长。"""
        with self.assertRaises(appserver.AppServerTimeoutError):
            await self.conn.request("silent", timeout_seconds=0.3)
        self.assertEqual(await self.conn.request("echo", {"still": "alive"}), {"still": "alive"})

    async def test_notifications_reach_the_handler(self) -> None:
        seen: list[tuple[str, dict[str, Any]]] = []
        conn = await appserver.open_connection(
            fake_argv(),
            cwd=self.cwd,
            env=dict(os.environ),
            on_notification=lambda m, p: seen.append((m, p)),
        )
        try:
            await conn.request("notify-then-reply")
            for _ in range(50):
                if seen:
                    break
                await asyncio.sleep(0.02)
        finally:
            await conn.close()
        self.assertEqual(seen[0][0], "item/completed")

    async def test_cancelling_a_request_removes_it(self) -> None:
        task = asyncio.create_task(self.conn.request("silent", timeout_seconds=30))
        await asyncio.sleep(0.1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        # 连接还能用，没有被那条被取消的请求毒掉。
        self.assertEqual(await self.conn.request("echo", {"x": 1}), {"x": 1})


class ChildDeathTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cwd = tempfile.mkdtemp(prefix="appserver-test-")

    async def test_child_death_fails_inflight_requests_instead_of_hanging(self) -> None:
        """子进程死了要把所有在途请求一次性失败掉，
        否则调用方会永远挂着，用户看到的是"生成中"直到天荒地老。"""
        conn = await appserver.open_connection(fake_argv(), cwd=self.cwd, env=dict(os.environ))
        pending = asyncio.create_task(self.conn_request(conn))
        await asyncio.sleep(0.1)
        conn.notify("die")
        with self.assertRaises(appserver.AppServerError):
            await asyncio.wait_for(pending, timeout=10)

    @staticmethod
    async def conn_request(conn: appserver.AppServerConnection) -> object:
        return await conn.request("silent", timeout_seconds=30)

    async def test_requests_after_death_fail_immediately(self) -> None:
        conn = await appserver.open_connection(fake_argv(), cwd=self.cwd, env=dict(os.environ))
        conn.notify("die")
        for _ in range(100):
            if conn.returncode is not None:
                break
            await asyncio.sleep(0.02)
        with self.assertRaises(appserver.AppServerError):
            await conn.request("echo", {"a": 1}, timeout_seconds=5)

    async def test_close_terminates_a_hung_child(self) -> None:
        """对端卡死时 close 必须能拆掉它，而且要等它真的死。"""
        conn = await appserver.open_connection(fake_argv(), cwd=self.cwd, env=dict(os.environ))
        conn.notify("hang-forever")
        await asyncio.sleep(0.2)
        await asyncio.wait_for(conn.close(), timeout=30)
        self.assertIsNotNone(conn.returncode)

    async def test_close_is_idempotent(self) -> None:
        conn = await appserver.open_connection(fake_argv(), cwd=self.cwd, env=dict(os.environ))
        await conn.close()
        await conn.close()


class ParsingTest(unittest.TestCase):
    def test_bad_lines_return_none(self) -> None:
        self.assertIsNone(appserver.parse_json_line(""))
        self.assertIsNone(appserver.parse_json_line("   "))
        self.assertIsNone(appserver.parse_json_line("{ broken"))
        self.assertIsNone(appserver.parse_json_line("[1,2,3]"), "数组不是协议帧")
        self.assertIsNone(appserver.parse_json_line('"a string"'))

    def test_good_line_parses(self) -> None:
        self.assertEqual(appserver.parse_json_line('{"id":1}'), {"id": 1})

    def test_unsupported_signal_needs_both_markers(self) -> None:
        """只有"argv 解析失败且点名 app-server"才证明 RPC 面不存在；
        别的 stderr 一律当瞬时，否则一次抖动会把本地能力永久标成不可用。"""
        self.assertTrue(
            appserver.stderr_indicates_missing_app_server(
                "error: unrecognized subcommand 'app-server'"
            )
        )
        self.assertFalse(appserver.stderr_indicates_missing_app_server("connection reset"))
        self.assertFalse(
            appserver.stderr_indicates_missing_app_server("app-server: temporary failure")
        )

    def test_argv_uses_stdio_only(self) -> None:
        """不开 unix/ws 监听面：那会把一个能操作本机的 RPC 端口暴露出去。"""
        argv = appserver.build_app_server_argv("/x/codex")
        self.assertEqual(argv[:4], ["/x/codex", "app-server", "--listen", "stdio://"])
        self.assertNotIn("ws://", " ".join(argv))


if __name__ == "__main__":
    unittest.main()
