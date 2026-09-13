"""`codex app-server` 的 stdio JSON-RPC 客户端。

**这是本地生成能力的唯一执行面。** 不抓 TUI 文本、不解析屏幕、不猜路径——
`codex app-server` 是 Codex 官方的协议接口，schema 由官方命令自己生成
（`codex app-server generate-json-schema`），版本随 CLI 一起演进。

协议形状（取自本机 codex-cli 0.153.4 生成的 schema，非文档转述）：

* 帧是 **JSONL**，一行一个对象。
* 请求：``{"method": ..., "id": <int>, "params": {...}}``，响应按 **数字 id** 配对。
  **不是 JSON-RPC 2.0 的信封**——没有 ``jsonrpc`` 字段。
* 通知：``{"method": ..., "params": {...}}``，没有 id。
* 握手：``initialize`` 请求 → 收到响应后再发 ``initialized`` 通知。

设计参考 Orca（MIT），逐条差异见 `THIRD_PARTY_NOTICES.md`。抄的是边界条件，
其中三条是这类客户端最容易写错的：

1. **JSONL 解析失败要跳过，不能抛。** 子进程 stdout 上混入非协议行是常态
   （启动横幅、warning）。一行坏帧不该杀掉整条连接。
2. **超时是逐请求的，不是逐连接的。** 连接活得比任何一次调用都长，
   只有单次调用才谈得上截止时间。
3. **子进程死了要把所有在途请求一次性失败掉**，否则调用方会永远挂着——
   而且要区分"传输坏了"与"进程真的退出了"，前者立刻不可用，
   后者才触发 `on_exit`。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AppServerConnection",
    "AppServerError",
    "AppServerExitError",
    "AppServerRequestError",
    "AppServerTimeoutError",
    "AppServerUnsupportedError",
    "open_connection",
    "stderr_indicates_missing_app_server",
]

#: 握手上限。参考 Orca 的 HANDSHAKE_TIMEOUT_MS。
HANDSHAKE_TIMEOUT_SECONDS = 15.0
#: 单次请求默认上限。生图那一轮会显式传更长的值。
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
#: stderr 只留尾巴，用于本地分类；**永远不回传服务端**（里面有本机路径与账号）。
STDERR_TAIL_MAX_BYTES = 8192
#: 单帧上限。生图的 base64 会很大，给得宽，但不能无上限。
MAX_FRAME_BYTES = 64 * 1024 * 1024
#: 优雅退出与强制拆除之间的等待。
GRACEFUL_EXIT_SECONDS = 1.5
FORCED_EXIT_SECONDS = 1.0


class AppServerError(Exception):
    """本模块所有错误的基类。"""


class AppServerExitError(AppServerError):
    """子进程已经没了。`stderr_tail` 只在本地用于分类。"""

    def __init__(self, message: str, *, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.stderr_tail = stderr_tail


class AppServerTimeoutError(AppServerError):
    """单次请求超时。连接本身可能还活着。"""


class AppServerRequestError(AppServerError):
    """对端返回了 JSON-RPC 错误体。"""

    def __init__(self, method: str, code: int | None, message: str) -> None:
        super().__init__(f"{method} failed: {message}")
        self.method = method
        self.code = code


class AppServerUnsupportedError(AppServerError):
    """这个 Codex 版本没有 app-server 这个子命令。"""


def stderr_indicates_missing_app_server(stderr_tail: str) -> bool:
    """只有"argv 解析失败且点名 app-server"才证明 RPC 面不存在。

    别的 stderr 一律当瞬时错误——把它们当成"不支持"会让一次网络抖动
    永久地把本地能力标成不可用。判据抄自 Orca 的
    `codex-app-server-capability-signal.ts`。
    """
    lowered = stderr_tail.lower()
    if "app-server" not in lowered:
        return False
    return any(
        marker in lowered
        for marker in ("unrecognized subcommand", "unexpected argument", "invalid subcommand")
    )


def parse_json_line(line: str) -> dict[str, Any] | None:
    """解析一行协议帧。**不是对象、或解析不了，都返回 None。**

    返回 None 而不是抛：见模块文档第 1 条。
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


@dataclass
class _Pending:
    method: str
    future: asyncio.Future[Any]


NotificationHandler = Callable[[str, dict[str, Any]], None]
ServerRequestHandler = Callable[[str, Any, dict[str, Any]], Awaitable[Any] | Any]


@dataclass
class AppServerConnection:
    """一条开着的 app-server 连接。用 :func:`open_connection` 创建。"""

    _process: asyncio.subprocess.Process
    _on_notification: NotificationHandler | None = None
    _on_server_request: ServerRequestHandler | None = None
    _pending: dict[int, _Pending] = field(default_factory=dict)
    _next_id: int = 1
    _stderr_tail: str = ""
    _terminal_error: BaseException | None = None
    _closing: bool = False
    _reader_task: asyncio.Task[None] | None = None
    _stderr_task: asyncio.Task[None] | None = None

    # ---------------------------------------------------------------- 读侧

    async def _pump_stdout(self) -> None:
        stdout = self._process.stdout
        assert stdout is not None
        try:
            while True:
                raw = await stdout.readline()
                if not raw:
                    break
                if len(raw) > MAX_FRAME_BYTES:
                    # 超长帧丢弃但不杀连接：下一帧仍可能是好的。
                    continue
                record = parse_json_line(raw.decode("utf-8", errors="replace"))
                if record is None:
                    continue
                self._dispatch(record)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - 传输层意外
            self._fail_all(AppServerExitError(f"transport failed: {exc!r}"))
        finally:
            # 读到 EOF = 子进程的 stdout 关了，等于连接结束。
            self._fail_all(
                AppServerExitError("codex app-server exited", stderr_tail=self._stderr_tail)
            )

    async def _pump_stderr(self) -> None:
        stderr = self._process.stderr
        if stderr is None:
            return
        try:
            while True:
                chunk = await stderr.read(4096)
                if not chunk:
                    break
                text = self._stderr_tail + chunk.decode("utf-8", errors="replace")
                self._stderr_tail = text[-STDERR_TAIL_MAX_BYTES:]
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            pass

    def _dispatch(self, record: dict[str, Any]) -> None:
        record_id = record.get("id")
        method = record.get("method")

        if method is None and isinstance(record_id, int):
            # 响应：按数字 id 配对。
            pending = self._pending.pop(record_id, None)
            if pending is None or pending.future.done():
                return
            error = record.get("error")
            if isinstance(error, dict):
                pending.future.set_exception(
                    AppServerRequestError(
                        pending.method,
                        error.get("code") if isinstance(error.get("code"), int) else None,
                        str(error.get("message", "unknown error")),
                    )
                )
            else:
                pending.future.set_result(record.get("result"))
            return

        if isinstance(method, str) and record_id is not None:
            # 服务端反向请求（审批等）。没有 handler 就明确拒绝，不静默丢弃——
            # 丢弃会让对端永远等着。
            self._handle_server_request(method, record_id, record.get("params") or {})
            return

        if isinstance(method, str):
            params = record.get("params")
            if self._on_notification is not None:
                self._on_notification(method, params if isinstance(params, dict) else {})

    def _handle_server_request(self, method: str, request_id: Any, params: dict[str, Any]) -> None:
        handler = self._on_server_request
        if handler is None:
            self._write(
                {
                    "id": request_id,
                    "error": {"code": -32601, "message": f"unsupported server request {method}"},
                }
            )
            return

        async def _run() -> None:
            try:
                outcome = handler(method, request_id, params)
                if asyncio.iscoroutine(outcome):
                    outcome = await outcome
                self._write({"id": request_id, "result": outcome})
            except Exception as exc:  # pragma: no cover - handler 自己的问题
                self._write({"id": request_id, "error": {"code": -32603, "message": repr(exc)}})

        asyncio.get_running_loop().create_task(_run())

    def _fail_all(self, error: BaseException) -> None:
        """子进程死了/传输坏了：所有在途请求一次性失败。

        只记第一个终态原因——一次死亡会从好几个监听点传回来，
        第一个才是有信息量的那个。
        """
        if self._terminal_error is None:
            self._terminal_error = error
        pending, self._pending = self._pending, {}
        for entry in pending.values():
            if not entry.future.done():
                entry.future.set_exception(self._terminal_error)

    # ---------------------------------------------------------------- 写侧

    def _write(self, payload: dict[str, Any]) -> None:
        stdin = self._process.stdin
        if stdin is None or stdin.is_closing():
            raise AppServerExitError("codex app-server stdin is closed")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        stdin.write(line.encode("utf-8"))

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """发一个通知。发不出去就算了——下一次请求会暴露子进程已死。"""
        if self._terminal_error is not None or self._closing:
            return
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        with contextlib.suppress(AppServerError, OSError):
            self._write(payload)

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> Any:
        """发一个请求并等结果。**超时是这一次调用的，不是这条连接的。**"""
        if self._closing:
            raise AppServerExitError(f"connection is closed ({method})")
        if self._terminal_error is not None:
            raise self._terminal_error

        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = _Pending(method=method, future=future)

        payload: dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            payload["params"] = params
        try:
            self._write(payload)
        except Exception:
            self._pending.pop(request_id, None)
            raise

        try:
            return await asyncio.wait_for(future, timeout=timeout_seconds)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise AppServerTimeoutError(
                f"codex app-server {method} exceeded {timeout_seconds}s"
            ) from exc
        except asyncio.CancelledError:
            # 调用方取消（用户取消任务）：把这条请求摘掉，别留一个没人收的 future。
            self._pending.pop(request_id, None)
            raise

    # ---------------------------------------------------------------- 生命周期

    async def close(self) -> None:
        """优雅关，关不掉就拆进程树。**幂等。**"""
        if self._closing:
            return
        self._closing = True
        self._fail_all(AppServerExitError("connection closed by caller"))

        stdin = self._process.stdin
        if stdin is not None and not stdin.is_closing():
            with contextlib.suppress(OSError, AppServerError):
                stdin.close()

        try:
            await asyncio.wait_for(self._process.wait(), timeout=GRACEFUL_EXIT_SECONDS)
        except (TimeoutError, asyncio.CancelledError):
            await terminate_process_tree(self._process)

        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    @property
    def stderr_tail(self) -> str:
        return self._stderr_tail

    @property
    def returncode(self) -> int | None:
        return self._process.returncode


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """杀进程树，并**等它真的死**。

    Windows 上 `Process.kill()` 只杀直接子进程；`codex app-server` 会拉起别的
    子进程，只杀父进程会留下孤儿继续跑、继续占订阅额度。POSIX 上用进程组。
    设计参考 Orca 的 `codex-app-server-process-teardown.ts` /
    `codex-app-server-posix-supervisor.ts`——它们比这份更完整（会证明进程组已空），
    这里取的是"分平台 + 等待确认"这两条内核。
    """
    if process.returncode is not None:
        return

    if os.name == "nt":
        # 不拼接任何来自网络的字符串，只用 pid；shell=False。
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=30,
            )
    else:
        import signal

        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)

    try:
        await asyncio.wait_for(process.wait(), timeout=FORCED_EXIT_SECONDS + 14)
    except (TimeoutError, asyncio.CancelledError):  # pragma: no cover - 极端情况
        with contextlib.suppress(ProcessLookupError, OSError):
            process.kill()


async def open_connection(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    on_notification: NotificationHandler | None = None,
    on_server_request: ServerRequestHandler | None = None,
    client_name: str = "aigc_studio_local_runner",
    client_version: str = "0.1.0",
    handshake_timeout_seconds: float = HANDSHAKE_TIMEOUT_SECONDS,
) -> AppServerConnection:
    """起 `codex app-server`，完成握手，返回一条开着的连接。

    握手失败时**先把子进程收掉再抛**——留一个跑着的 app-server 在后台，
    既占内存又可能继续持有账号会话。

    `handshake_timeout_seconds` 由调用方从**本次请求的剩余预算**里切一块给它
    （见 `executor.py`）。握手用一份独立的 15 秒，会让"握手 + 起线程 + 生成"
    加起来超过服务端给的截止时间——那时服务端已经判超时并清掉了这条请求，
    而桌面还在画，烧的是用户自己的订阅额度。
    """
    kwargs: dict[str, Any] = {}
    if os.name != "nt":
        # 自成进程组，超时时能整组杀掉。
        kwargs["start_new_session"] = True

    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        limit=MAX_FRAME_BYTES,
        **kwargs,
    )

    connection = AppServerConnection(
        _process=process,
        _on_notification=on_notification,
        _on_server_request=on_server_request,
    )
    loop = asyncio.get_running_loop()
    connection._stderr_task = loop.create_task(connection._pump_stderr())
    connection._reader_task = loop.create_task(connection._pump_stdout())

    try:
        await connection.request(
            "initialize",
            {
                "clientInfo": {
                    "name": client_name,
                    "title": "AIGC Studio local runner",
                    "version": client_version,
                },
                "capabilities": {
                    "experimentalApi": True,
                    "requestAttestation": False,
                    "mcpServerOpenaiFormElicitation": False,
                    "extensions": {},
                },
            },
            timeout_seconds=max(1.0, handshake_timeout_seconds),
        )
    except BaseException:
        tail = connection.stderr_tail
        await connection.close()
        if stderr_indicates_missing_app_server(tail):
            raise AppServerUnsupportedError(
                "这个 Codex 版本没有 app-server 子命令，请升级官方 Codex CLI"
            ) from None
        raise
    connection.notify("initialized")
    return connection


def build_app_server_argv(codex_binary: str, *, extra: list[str] | None = None) -> list[str]:
    """组装 argv。**只有 stdio**，不开 unix/ws 监听面。

    监听面（`--listen ws://...`）会把一个能操作本机的 RPC 端口暴露出去；
    我们是本机父子进程关系，stdio 就够，也不给别人留入口。
    """
    return [codex_binary, "app-server", "--listen", "stdio://", *(extra or [])]
