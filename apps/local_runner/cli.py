"""桌面连接器的命令行。

```
set AIGC_LOCAL_RUNNER_TOKEN=<桥接令牌>
python -m apps.local_runner --server http://127.0.0.1:3100 --provider codex
python -m apps.local_runner --doctor
```

令牌**只从环境变量读**。写在 argv 里会进 `ps` / 任务管理器的命令行列、
进 PowerShell 的 `Get-History`、进崩溃转储，而这些地方没有一个是可控的。

一个进程 = 一个 provider = 一次一条请求。要同时跑"文本走 Claude、
出图走 Codex"就起两个进程——两个进程各自只管好自己那台 CLI，
比一个进程里塞两套子进程管理简单得多，也更好排障。
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import os
import sys
import threading
import time
from dataclasses import dataclass

from apps.local_runner import executor as executors
from apps.local_runner.client import (
    BridgeClient,
    RedirectRefusedError,
    TransportError,
    UrlPolicyError,
    normalize_server_url,
)
from apps.local_runner.executor import Executor, ImageResult, TextResult
from apps.local_runner.resolve import NotInstalledError

TOKEN_ENV = "AIGC_LOCAL_RUNNER_TOKEN"

PROVIDERS = ("codex", "claude")

#: 空闲时的轮询间隔。服务端是短轮询，等待发生在这一侧。
DEFAULT_POLL_SECONDS = 1.0

#: 忙碌时的心跳间隔。必须明显小于服务端的心跳有效期（20 秒）。
BUSY_HEARTBEAT_SECONDS = 5.0


@dataclass
class Outstanding:
    request_id: str
    lease_token: str
    #: 这一轮的绝对截止时间（`time.monotonic()` 刻度）。回传结果时用它算
    #: "还能等多久"——服务端发下来的是**剩余**时间，等模型已经花掉一大截。
    deadline: float = 0.0


def _as_int(value: object, *, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _log(message: str) -> None:
    print(f"[local-runner] {message}", flush=True)


def _read_token() -> str:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise SystemExit(f"没有 {TOKEN_ENV}。先设环境变量再启动，不要把令牌写进命令行。")
    if len(token) < 32:
        raise SystemExit(f"{TOKEN_ENV} 太短（至少 32 个字符），与服务端的要求对不上。")
    return token


def doctor() -> int:
    """列出本机能力与账号身份。**不打印任何凭据、令牌、账号或路径。**

    「登录没登录、是不是订阅」这里**查**，走的是官方的免费子命令
    （`codex login status` / `claude auth status --json`）——它们不发模型
    请求、不花一分额度，也不需要我们去读 `auth.json`（那才是越界）。

    以前这里写着"不推断登录状态"，理由是"查它要么越界要么花钱"。那个前提
    是错的：两个 CLI 都给了免费的官方查询口。而不查的代价很实——
    用户的 `~/.claude/settings.json` 里放一个 `env.ANTHROPIC_BASE_URL`
    就能把这条"用我的订阅额度"的路径变成按量付费，父进程环境干干净净。

    能力那一行是**真探出来的**：Codex 的出图要它自己在 `features list` 里
    报 `image_generation true` 才算数。
    """
    print("AIGC Studio · 本机连接器体检")
    print(f"  python      : {sys.version.split()[0]} ({sys.platform})")
    print(f"  桥接令牌     : {'已设置' if os.environ.get(TOKEN_ENV) else '未设置'}（不回显内容）")
    print("  gemini      : 本轮未验证、未接入，界面上没有它的入口")
    print("  视频 / 语音  : 不支持（本机这两个 CLI 都没有订阅制的视频或语音生成入口）")

    exit_code = 0
    for provider in PROVIDERS:
        try:
            executor = executors.build_executor(provider)
        except NotInstalledError as exc:
            print(f"  {provider:<11}: 不可用 —— {exc}")
            exit_code = 1
            continue
        caps = "、".join(executor.capabilities())
        print(f"  {provider:<11}: 版本 = {executor.version() or '（未回报）'}")
        print(f"  {'':<11}  能力 = {caps or '（无）'}")
        # 账号身份是**查出来的**（免费的官方子命令），不是"我们清了环境变量
        # 所以应该没问题"。查不过就一种能力都不报，理由印在这里。
        reason = executor.unavailable_reason()
        if reason:
            print(f"  {'':<11}  账号 = 不可用：{reason}")
            exit_code = 1
        else:
            print(f"  {'':<11}  账号 = 订阅登录已确认（未发起任何模型请求）")
    return exit_code


class Worker:
    """取单 → 跑 → 回结果。一次只跑一个。"""

    def __init__(
        self,
        client: BridgeClient,
        *,
        executor: Executor,
        poll_seconds: float,
    ) -> None:
        self._client = client
        self._executor = executor
        self._provider = executor.provider
        self._kinds = list(executor.capabilities())
        self._version = executor.version()
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._heartbeat_stop = threading.Event()
        self._outstanding: Outstanding | None = None
        # 服务端说"这条已经没人等了"时置位。执行器每隔半秒问它一次。
        self._abandoned = threading.Event()

    def stop(self) -> None:
        self._stop.set()
        self._executor.cancel()

    # ---------------------------------------------------------------- 主循环

    def serve(self) -> int:
        _log(f"provider = {self._provider}，能力 = {'、'.join(self._kinds) or '（无）'}")
        _log(f"版本 = {self._version or '（未回报）'}")
        _log(f"服务端 = {self._client.base}")
        _log("按 Ctrl-C 停止。")
        while not self._stop.is_set():
            try:
                payload = self._client.poll(
                    self._provider, kinds=self._kinds, version=self._version
                )
            except RedirectRefusedError as exc:
                _log(f"停止：{exc}")
                return 2
            except TransportError as exc:
                if exc.status in (401, 403):
                    _log(f"停止：服务端拒绝了桥接令牌（{exc.status}）")
                    return 2
                if exc.status == 404:
                    _log("停止：服务端没有开启本机运行时（404）")
                    return 2
                _log(f"取单失败，稍后重试：{exc}")
                self._stop.wait(min(10.0, self._poll_seconds * 5))
                continue

            if payload is None:
                self._stop.wait(self._poll_seconds)
                continue

            self._handle(payload)
        return 0

    # ---------------------------------------------------------------- 单条请求

    def _handle(self, payload: dict[str, object]) -> None:
        request_id = str(payload.get("request_id", ""))
        lease_token = str(payload.get("lease_token", ""))
        if not request_id or not lease_token:
            _log("服务端返回的请求缺字段，跳过")
            return
        kind = str(payload.get("kind", "text"))
        # 服务端发来的是 JSON，静态类型只知道它是 object；先转成 str 再转 int，
        # 收到什么奇怪的东西都不会炸出一个看不懂的 TypeError。
        timeout_seconds = _as_int(payload.get("timeout_seconds"), default=180)
        self._outstanding = Outstanding(
            request_id, lease_token, deadline=time.monotonic() + timeout_seconds
        )
        self._abandoned.clear()

        heartbeat_stop = threading.Event()
        self._heartbeat_stop = heartbeat_stop
        heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat.start()

        started = time.monotonic()
        try:
            if kind == "image":
                self._run_image(payload, timeout_seconds=timeout_seconds)
            else:
                self._run_text(payload, timeout_seconds=timeout_seconds)
        except KeyboardInterrupt:
            # **必须在这里回报，不能留给 main() 的 except。**
            # 下面的 finally 会把 `_outstanding` 清成 None，而
            # `cancel_outstanding()` 第一行就是"没有 outstanding 就返回"——
            # 以前那条路上它永远是空转，用户得等满整个超时（默认 420 秒）
            # 才看到失败。
            self._executor.cancel()
            self._report_error("cancelled")
            raise
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=5)
            _log(f"用时 {round(time.monotonic() - started, 1)}s")
            self._outstanding = None

    def _run_text(self, payload: dict[str, object], *, timeout_seconds: int) -> None:
        job = payload.get("text")
        if not isinstance(job, dict):
            self._report_error("unsupported")
            return
        system = str(job.get("system", ""))
        user = str(job.get("user", ""))
        _log(
            f"文本请求（schema={job.get('schema_name')}，"
            f"提示词 {len(system) + len(user)} 字符，超时 {timeout_seconds}s）"
        )
        result = self._executor.run_text(
            system=system,
            user=user,
            timeout_seconds=timeout_seconds,
            should_cancel=self._abandoned.is_set,
        )
        self._report_text(result)

    def _run_image(self, payload: dict[str, object], *, timeout_seconds: int) -> None:
        job = payload.get("image")
        if not isinstance(job, dict):
            self._report_error("unsupported")
            return
        prompt = str(job.get("prompt", ""))
        _log(f"出图请求（提示词 {len(prompt)} 字符，超时 {timeout_seconds}s）")
        result = self._executor.run_image(
            prompt=prompt,
            negative_prompt=str(job.get("negative_prompt", "")),
            size=str(job.get("size", "1024*1024")),
            timeout_seconds=timeout_seconds,
            should_cancel=self._abandoned.is_set,
        )
        self._report_image(result)

    # ---------------------------------------------------------------- 回传

    def _report_text(self, result: TextResult) -> None:
        _log(f"完成，结果 = {'ok' if result.error_code is None else result.error_code}")
        self._post(
            text=result.text,
            model_id=result.model_id,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            error_code=result.error_code,
        )

    def _report_image(self, result: ImageResult) -> None:
        if result.error_code is not None or result.data is None:
            _log(f"完成，结果 = {result.error_code or 'cli_error'}")
            self._post(error_code=result.error_code or "cli_error")
            return
        _log(f"完成，拿到一张 {result.mime_type}，{len(result.data)} 字节")
        self._post(
            image_base64=base64.b64encode(result.data).decode("ascii"),
            image_mime=result.mime_type,
            revised_prompt=result.revised_prompt,
            model_id=result.model_id,
        )

    def _report_error(self, error_code: str) -> None:
        _log(f"无法处理这条请求：{error_code}")
        self._post(error_code=error_code)

    def _post(self, **fields: object) -> None:
        outstanding = self._outstanding
        if outstanding is None:
            return
        try:
            self._client.post_result(
                outstanding.request_id,
                lease_token=outstanding.lease_token,
                budget_left_seconds=outstanding.deadline - time.monotonic(),
                **fields,  # type: ignore[arg-type]
            )
        except (TransportError, RedirectRefusedError) as exc:
            # 回不去就让它超时。**不重投**：重投意味着同一条请求可能被跑两遍，
            # 而每一遍都实打实扣订阅额度。
            _log(f"结果回传失败（服务端会按超时处理）：{exc}")

    def _heartbeat_loop(self) -> None:
        """跑活的这几分钟里持续刷心跳，并接收"别跑了"。

        少了心跳，服务端会在 20 秒后判定"没有连接器"，把同一时刻进来的
        下一条请求直接拒掉——而我们其实好好地在干活。
        而"别跑了"这一路更重要：用户在网页上取消之后，桌面上这台机器
        必须立刻停，否则烧的是他自己的订阅额度。
        """
        stop = self._heartbeat_stop
        while not stop.wait(BUSY_HEARTBEAT_SECONDS):
            outstanding = self._outstanding
            with contextlib.suppress(TransportError, RedirectRefusedError):
                alive = self._client.heartbeat(
                    self._provider,
                    kinds=self._kinds,
                    version=self._version,
                    request_id=outstanding.request_id if outstanding else None,
                )
                if outstanding is not None and not alive:
                    _log("服务端说这条已经没人等了（取消或超时），正在停止本轮")
                    self._abandoned.set()
                    self._executor.cancel()
                    return

    def cancel_outstanding(self) -> None:
        """兜底：Ctrl-C 落在两条请求**之间**时把在途那条收掉。

        跑活当中被 Ctrl-C 的那种由 `_handle` 自己就地回报（见那里的注释），
        所以走到这里通常已经没有 outstanding 了——这不是空转，是第二道闸。
        """
        if self._outstanding is None:
            return
        self._post(error_code="cancelled")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m apps.local_runner",
        description="AIGC Studio 本机连接器（试点）：文本 + Codex 原生出图",
    )
    parser.add_argument("--server", help="服务端地址，例如 http://127.0.0.1:3100")
    parser.add_argument("--provider", choices=list(PROVIDERS))
    parser.add_argument(
        "--model",
        default=None,
        help="传给 CLI 的模型名。不填就用 CLI 自己的默认值——这里不写死任何模型 id。",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help=f"空闲轮询间隔，默认 {DEFAULT_POLL_SECONDS}s",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="只体检本机能力（可执行文件、版本、真实支持的能力），不连服务端",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.doctor:
        return doctor()

    if not args.server or not args.provider:
        _log("缺 --server 或 --provider（体检请用 --doctor）")
        return 2

    try:
        normalize_server_url(args.server)
    except UrlPolicyError as exc:
        _log(f"服务端地址不合规：{exc}")
        return 2

    try:
        executor = executors.build_executor(args.provider, model=args.model)
    except NotInstalledError as exc:
        _log(str(exc))
        return 2

    reason = executor.unavailable_reason()
    if reason:
        # 起都不起。一个报不出任何能力的连接器连上去只会让界面显示
        # "在线但不支持"，用户看不出真正该修的是他的登录方式。
        _log(f"拒绝启动：{reason}")
        _log("这条路径只接受订阅登录；用 --doctor 看详细体检。")
        return 2

    client = BridgeClient(args.server, _read_token())
    worker = Worker(
        client,
        executor=executor,
        poll_seconds=max(0.2, args.poll_seconds),
    )
    try:
        return worker.serve()
    except KeyboardInterrupt:
        _log("收到 Ctrl-C，正在停止…")
        worker.stop()
        worker.cancel_outstanding()
        return 0
