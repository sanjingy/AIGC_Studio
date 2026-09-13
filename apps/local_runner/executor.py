"""统一执行接口：一条请求怎么在本机跑完。

在这之前这里有**两套**管理机制：`process.py` + `adapters.py` 的一次性
子进程（`codex exec` / `claude -p`），和 `appserver.py` 的 JSON-RPC 长连接。
两套各有一份"怎么起进程、怎么判失败、怎么杀干净"，而它们迟早会分叉。

这一版收成一个接口、两个实现：

| provider | 执行面 | 能力 |
|---|---|---|
| codex | `codex app-server`（官方 JSON-RPC） | 文本 + **图片** |
| claude | `claude -p`（官方 headless） | 文本 |

Codex 的一次性 `codex exec` 路径**已删除**：同一个 CLI 没有理由维护两条
执行路径，而 app-server 那条还多给了打断、原生生图产物和账号事件。
Claude 没有 app-server 这样的协议面（它的 headless 就是 `-p`），
所以它保留子进程路径——保留的是**一条**，不是第二套管理机制。

## 能力是探出来的，不是声明的

`capabilities()` 只在真的能跑时才把一种能力算进去：可执行文件解析得到、
（Codex）`codex features list` 里 `image_generation` 为 `true`。
服务端把这份能力原样透给界面，用户看到的"本机可以出图"必须对应
一个真的能出图的进程，否则就是一个点下去必然失败的假入口。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from apps.local_runner import adapters, imagegen
from apps.local_runner.appserver import (
    HANDSHAKE_TIMEOUT_SECONDS,
    AppServerError,
    AppServerTimeoutError,
    AppServerUnsupportedError,
    build_app_server_argv,
    open_connection,
)
from apps.local_runner.process import Runner
from apps.local_runner.resolve import Executable, NotInstalledError, resolve

#: 取消检查。由调用方给（cli.py 用服务端心跳的回答实现），
#: 从执行线程里被反复调用，所以必须是廉价且线程安全的。
CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class TextResult:
    """一次文本执行的结局。`error_code` 非空即失败。

    `error_code` 只取服务端认得的那几个有界值——子进程的 stderr 一个字节
    都不回传：那里面有本机路径、用户名，以及登录提示里的账号。
    """

    text: str | None = None
    error_code: str | None = None
    model_id: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


@dataclass(frozen=True)
class ImageResult:
    """一次出图执行的结局。"""

    data: bytes | None = None
    mime_type: str | None = None
    revised_prompt: str | None = None
    model_id: str | None = None
    error_code: str | None = None


class Executor(Protocol):
    """一台本机 CLI 能干什么、怎么干。"""

    provider: str

    def capabilities(self) -> tuple[str, ...]: ...

    #: 一种能力都报不出来时的中文原因（没登录 / 用的是 API Key / 第三方通道）。
    #: 能力为空而原因为 None，说明只是这台 CLI 本来就不支持——两者要分得开。
    def unavailable_reason(self) -> str | None: ...

    def version(self) -> str | None: ...

    def run_text(
        self,
        *,
        system: str,
        user: str,
        timeout_seconds: int,
        should_cancel: CancelCheck | None = None,
    ) -> TextResult: ...

    def run_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        size: str,
        timeout_seconds: int,
        should_cancel: CancelCheck | None = None,
    ) -> ImageResult: ...

    def cancel(self) -> None: ...


# ---------------------------------------------------------------- 提示词包装


#: 出图的指令外壳。
#:
#: **画面内容一个字都不动**——它由服务端 `consistency.compose` 合成，
#: 风格锁定就在里面（17_ConsistencyEngine §4）。这里加的只是"请调用你的
#: 图像生成工具"这一句：协议层**没有**"生成一张图"的方法（99 个 client
#: request 里一个都没有），出图是模型自己决定调内置 image_gen 的产物，
#: 我们只能在提示词层请求。
#:
#: "不要改写"那句是冲着 ADR-036 去的：模型会改写提示词
#: （产物里的 `revisedPrompt` 就是证据），而改写掉的如果是风格词，
#: 画风就从这条新链路重新漂回来了。改写后的提示词照样回传服务端存档。
_IMAGE_INSTRUCTION = """请调用你的内置图像生成工具生成 1 张图片，尺寸 {size}。
除了这张图不要输出任何解释或说明文字。

画面描述如下。**必须原样使用，不要改写、精简、翻译或替换其中任何词语**：

{prompt}
"""

_NEGATIVE_SUFFIX = """
画面中不要出现：{negative}
"""


def build_image_prompt(*, prompt: str, negative_prompt: str, size: str) -> str:
    body = _IMAGE_INSTRUCTION.format(size=_human_size(size), prompt=prompt.strip())
    if negative_prompt.strip():
        body += _NEGATIVE_SUFFIX.format(negative=negative_prompt.strip())
    return body


def _human_size(size: str) -> str:
    """`1024*1024` → `1024×1024`。认不出来就原样带过去，不猜。"""
    match = re.fullmatch(r"\s*(\d{2,5})\s*[*x×]\s*(\d{2,5})\s*", size or "")
    return f"{match.group(1)}×{match.group(2)}" if match else (size or "1024×1024")


def merge_prompt(system: str, user: str) -> str:
    return f"{system.rstrip()}\n\n---\n\n{user}"


def _left(deadline: float, cap: float | None = None) -> float:
    """还剩多少预算。

    整条请求（握手 → 起线程 → 一轮 → 等产物）共用**一份**预算：服务端发
    下来的 `timeout_seconds` 已经扣掉了排队时间，每一步再各给一份完整的值，
    加起来必然超过服务端的截止时间——那时它已经判超时清掉了请求，
    桌面却还在画，烧的是用户自己的订阅额度。
    """
    remaining = max(1.0, deadline - time.monotonic())
    return min(remaining, cap) if cap is not None else remaining


# ---------------------------------------------------------------- 账号身份

#: Codex 的内置 Provider（走 ChatGPT 订阅登录）。`config.toml` 里可以定义
#: 别的 `model_provider`（带 `env_key` 指向一个按量付费的 API），
#: 所以每条线程都**显式**把它钉回来，不接受配置里的默认值。
CHATGPT_MODEL_PROVIDER = "openai"

#: 用户可以在 `config.toml` 里**重定义内置 provider 名**。`modelProvider: "openai"`
#: 只保证"用叫这个名字的那一个"，不保证它还是官方那条订阅通道——
#: 一个 `[model_providers.openai]` 段配上 `env_key` 就把它换成了按量付费。
#: 这几个键一旦出现，就说明"订阅额度"这个前提已经不成立。
_PROVIDER_SHADOW_KEYS = ("env_key", "experimental_bearer_token", "base_url")


def assert_first_party_provider(config: dict[str, object], *, provider: str) -> None:
    """有效配置里这条 provider 还是不是官方那条。不是就抛，**不发模型请求**。

    判据全部来自 `config/read` 回来的**有效配置**（官方方法算出来的最终值，
    不是我们去解析 `config.toml`）：

    * `model_providers.<provider>` 存在且带了 `env_key` / `base_url` /
      `experimental_bearer_token` —— 用户把内置名字盖掉了，这条会走一把 Key。
    * `openai_base_url` 被设过 —— 请求端点被改了。

    改不动的那一半（`~/.claude/settings.json` 那种由 CLI 自己读的注入）
    由各自的启动开关挡（Claude 是 `--setting-sources ""`）。**两条防线
    各管一段**，不要把这一条说成"一个检查全挡住"。
    """
    providers = config.get("model_providers")
    if isinstance(providers, dict):
        entry = providers.get(provider)
        if isinstance(entry, dict):
            hit = [k for k in _PROVIDER_SHADOW_KEYS if entry.get(k)]
            if hit:
                raise ProviderShadowedError(
                    f"你电脑上的 Codex 把内置 provider「{provider}」改成了自定义端点"
                    f"（{'、'.join(hit)}），那是按量付费的通道，已拒绝。"
                )
    if config.get("openai_base_url"):
        raise ProviderShadowedError(
            "你电脑上的 Codex 配了自定义的 openai_base_url（按量付费端点），已拒绝。"
        )


class ProviderShadowedError(RuntimeError):
    """有效配置把订阅通道换成了按量付费通道。"""


def build_thread_config_overrides(config: dict[str, object]) -> dict[str, object]:
    """按**有效配置**逐项生成这一条线程的收窄覆盖。

    ## 为什么不是写死一份

    2026-09-07 在本机实测（`codex-cli 0.153.4`，全程只用官方查询方法，
    没有发过任何 turn，因此没有消耗任何额度）：

    * `config: {"mcp_servers": {}}` —— **不清空**。线程起好之后用官方的
      `mcpServerStatus/list`（带 `threadId`）查这条线程，两台服务器
      （`codex_apps`、`node_repl`）都还是 `connected`。空对象是**合并**语义。
      上一版代码里写死的那份 `THREAD_CONFIG_OVERRIDES` 因此是**无效**的，
      而注释却写着"关掉用户的 MCP"——这次把它换成了实测有效的写法。
    * `config: {"mcp_servers": {"<名字>": {"command": <原命令>, "args": [],
      "enabled": false}}}` —— **有效**。同一个查询回来 `node_repl` 变成
      `disabled`。所以要按名禁用，而名字只能从 `config/read` 读。
    * 把 `config/read` 回来的服务器定义**原样**塞回去会被配置加载器拒掉
      （`tool_timeout_sec` 读回来是 `""`，写进去要 f64）。所以只带
      `command` + `args` + `enabled`，一个最小可解析的定义。

    ## 三项各自的理由

    * `mcp_servers` —— 用户配的 MCP 会连同它的 `env`、命令一起被拉起来。
      这一轮只要模型写字/画图，多起一堆本地服务既是攻击面也是噪声。
    * `notify` —— 本机这份配置里它指向一个 `.exe`，每轮结束都会被拉起来。
      服务端派下来的活不该在用户桌面上触发副作用程序。
    * `hooks` —— 同理，逐事件清空。`hooks` 里除了事件名还有一个 `state` 键，
      它的值是 map 不是 list，一起当成事件清空会让 `thread/start` 直接报
      `invalid type: sequence, expected a map`，所以只清值是 list 的那些。

    ## 没做到的，如实写在这里

    * `codex_apps` **关不掉、逐线程覆盖之后仍在跑**：它不在
      `config.mcp_servers` 里，`plugins.<名字>.enabled = false` 也不影响它
      （实测仍是 `connected` / `starting`）。它由 Codex 官方分发
      （`serverInfo.name = plugin-runtime`），不是用户配的第三方服务器——
      **这解释了它为什么不在我们能按名禁用的那张表里，但不等于它没有对外
      能力**：它带哪些工具、会不会联网、能碰哪些本地资源，本轮没有逐项
      核查过。如实记在这里：这一跳的外部能力面**未被收窄，也未被评估**。
    * `notify` / `hooks` 这两项**没有线程级的官方回读口**（`hooks/list` 只按
      `cwds` 查，不认 `threadId`；notify 要跑完一轮才会触发）。能证明的只有
      "官方配置加载器接受了这份覆盖"，不能证明"它确实没被触发"。
      要证明得真跑一轮 = 花额度，本轮没做。
    """
    servers = config.get("mcp_servers")
    disabled: dict[str, object] = {}
    if isinstance(servers, dict):
        for name, definition in servers.items():
            if not isinstance(name, str) or not name:
                continue
            command = definition.get("command") if isinstance(definition, dict) else None
            disabled[name] = {
                # 定义必须能被配置加载器解析，所以带上原命令；`enabled=false`
                # 才是真正起作用的那一位（服务器不会被拉起来）。
                "command": command if isinstance(command, str) and command else "",
                "args": [],
                "enabled": False,
            }

    overrides: dict[str, object] = {"mcp_servers": disabled, "notify": []}

    hooks = config.get("hooks")
    if isinstance(hooks, dict):
        cleared: dict[str, list[object]] = {
            key: [] for key, value in hooks.items() if isinstance(value, list)
        }
        if cleared:
            overrides["hooks"] = cleared
    return overrides


def _log_local(message: str) -> None:
    """只印在连接器自己的窗口里。**不回传服务端**：这些话里有用户的配置细节。"""
    print(f"[local-runner] {message}", flush=True)


@dataclass(frozen=True)
class AccountCheck:
    """这台 CLI 现在是不是"用订阅额度"的状态。

    `usable=False` 时 `reason` 一定有一句中文，界面要原样说给用户听。
    **不回显任何令牌、账号、路径。**
    """

    usable: bool
    reason: str | None = None


def classify_codex_account(account: object) -> AccountCheck:
    """把 `account/read` 回来的 `account` 对象判成"能不能用"。

    形状取自官方 schema 的 `Account`（`oneOf`，用 `type` 区分）：
    `apiKey` / `chatgpt` / `amazonBedrock`。**判的是这个结构化字段，
    不是给人看的那句话**——"Logged in using ChatGPT" 这种字符串会随版本和
    语言变，拿它做资金判据不可靠。

    2026-09-07 本机实测（codex-cli 0.153.4，只发 `account/read`，
    没有发过任何 turn，未消耗任何额度）：
    `{"account": {"type": "chatgpt", "email": ..., "planType": "plus"}}`。
    """
    if not isinstance(account, dict):
        return AccountCheck(
            False, "无法确认你电脑上的 Codex 是 ChatGPT 订阅登录，已拒绝以免产生按量付费。"
        )
    kind = account.get("type")
    if kind == "chatgpt":
        return AccountCheck(True)
    if kind == "apiKey":
        return AccountCheck(
            False,
            "你电脑上的 Codex 当前用的是 API Key（按量付费）。"
            "本机生成只接受 ChatGPT 订阅登录，请改用 codex login。",
        )
    if isinstance(kind, str) and kind:
        return AccountCheck(
            False,
            f"你电脑上的 Codex 走的是第三方通道（{kind}），本机生成只接受 ChatGPT 订阅登录。",
        )
    return AccountCheck(
        False, "无法确认你电脑上的 Codex 是 ChatGPT 订阅登录，已拒绝以免产生按量付费。"
    )


def classify_codex_login_text(returncode: int, blob: str) -> AccountCheck:
    """兜底判据：`codex login status` 那句给人看的话。

    **只在 `account/read` 拿不到时才用**（老版本 CLI 没有这个方法）。
    人类可读字符串会随版本、语言变，所以它是第二判据不是第一判据。
    """
    text = blob.lower()
    if returncode != 0 or "not logged in" in text:
        return AccountCheck(False, "你电脑上的 Codex 没有登录，请在终端里跑 codex login")
    if "api key" in text:
        return AccountCheck(
            False,
            "你电脑上的 Codex 当前用的是 API Key（按量付费）。"
            "本机生成只接受 ChatGPT 订阅登录，请改用 codex login。",
        )
    if "chatgpt" not in text:
        return AccountCheck(
            False,
            "无法确认你电脑上的 Codex 是 ChatGPT 订阅登录，已拒绝以免产生按量付费。",
        )
    return AccountCheck(True)


def _run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """在同步上下文里跑完一段协程。

    `asyncio.run` 在**已经有事件循环在跑**的线程里会直接抛
    （"cannot be called from a running event loop"）。构造执行器的正常路径
    是同步的，但测试和排障脚本会在协程里 new 一个出来——那时不该炸，
    也不该悄悄退回一条更弱的判据。换一条线程跑就两边都成立。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: list[T] = []
    failure: list[BaseException] = []

    def _worker() -> None:
        try:
            box.append(asyncio.run(coro))
        except BaseException as exc:  # 原样搬回调用线程，不在这里吞
            failure.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return box[0]


async def _read_account_and_config(exe: Executable) -> tuple[object, dict[str, Any]]:
    """开一条 app-server 连接，问两个官方查询方法，然后关掉。

    `account/read` 与 `config/read` **都不发模型请求**（本机实测：整个探测
    过程没有产生任何 turn，也没有动用额度）。开一条空连接、不发 turn，
    本身也不产生费用。
    """
    argv = (
        build_app_server_argv(exe.path)
        if exe.kind == "native"
        else [*exe.argv_prefix, "app-server", "--listen", "stdio://"]
    )
    workdir = tempfile.mkdtemp(prefix="aigc-local-runner-probe-")
    connection = await open_connection(
        argv,
        cwd=workdir,
        env=adapters.child_env(),
        on_notification=lambda method, params: None,
        handshake_timeout_seconds=HANDSHAKE_TIMEOUT_SECONDS,
    )
    try:
        response = await connection.request("account/read", {}, timeout_seconds=30.0)
        account = response.get("account") if isinstance(response, dict) else None
        config = await imagegen.read_effective_config(connection, timeout_seconds=30.0)
        return account, config
    finally:
        await connection.close()
        shutil.rmtree(workdir, ignore_errors=True)


def probe_codex_account(exe: Executable) -> AccountCheck:
    """这台 Codex 现在是不是"用订阅额度"的状态。**免费、官方、不发模型请求。**

    为什么必须真查而不是"我们清了环境变量所以应该没问题"：环境变量只是
    注入路径之一。`config.toml` 里换一个带 `env_key` 的 `model_provider`，
    或者用 `codex login --with-api-key` 存一把 Key，都能让这次调用变成
    按量付费，而父进程的环境变量干干净净。

    两道，都走官方查询口：

    1. **`account/read`**（app-server）—— 结构化的账号类型（`chatgpt` /
       `apiKey` / `amazonBedrock`）。优先用它，而不是去 parse
       `codex login status` 那句给人看的话。
    2. **`config/read`** —— 有效配置里内置 provider 有没有被换成自定义端点。

    app-server 起不来（老版本、协议不认）时退回 `codex login status`：
    有一条判据总比一条都没有强，但**判据的来源要说清楚**。
    """
    try:
        account, config = _run_sync(_read_account_and_config(exe))
    except (AppServerError, NotInstalledError, OSError):
        outcome = Runner().run(
            [*exe.argv_prefix, "login", "status"],
            stdin_text="",
            cwd=tempfile.gettempdir(),
            env=adapters.child_env(),
            timeout_seconds=30,
        )
        # 进程没退出时 returncode 是 None（超时被杀）——当成失败，不当成 0。
        return classify_codex_login_text(
            outcome.returncode if outcome.returncode is not None else 1,
            outcome.stdout + "\n" + outcome.stderr,
        )

    check = classify_codex_account(account)
    if not check.usable:
        return check
    try:
        assert_first_party_provider(config, provider=CHATGPT_MODEL_PROVIDER)
    except ProviderShadowedError as exc:
        return AccountCheck(False, str(exc))
    return AccountCheck(True)


def probe_claude_account(exe: Executable) -> AccountCheck:
    """`claude auth status --json`：免费、官方、不发模型请求。

    判据三条，缺一不可：登录了、走的是第一方（不是 Bedrock/Vertex/Foundry）、
    **没有任何 API Key 在场**（`apiKeySource` 一出现就说明这次调用会计费到
    一把 Key 上，而用户以为在用订阅）。

    2026-09-07 在本机实测过这个字段的行为：设 `ANTHROPIC_API_KEY` 时输出多出
    `"apiKeySource": "ANTHROPIC_API_KEY"`；设 `CLAUDE_CODE_USE_BEDROCK=1` 时
    `authMethod` 变成 `third_party`、`apiProvider` 变成 `bedrock`。
    """
    outcome = Runner().run(
        [*exe.argv_prefix, "auth", "status", "--json"],
        stdin_text="",
        cwd=tempfile.gettempdir(),
        env=adapters.child_env(),
        timeout_seconds=30,
    )
    raw = outcome.stdout.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return AccountCheck(False, "无法确认你电脑上的 Claude 登录状态，已拒绝。")
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError:
        return AccountCheck(False, "无法确认你电脑上的 Claude 登录状态，已拒绝。")
    if not isinstance(data, dict) or not data.get("loggedIn"):
        return AccountCheck(False, "你电脑上的 Claude 没有登录，请在终端里跑 claude auth login")
    if data.get("apiKeySource"):
        return AccountCheck(
            False,
            "你电脑上的 Claude 当前会用 API Key 计费（按量付费）。"
            "本机生成只接受订阅登录，请清掉那个 Key 再试。",
        )
    if data.get("authMethod") == "third_party" or data.get("apiProvider") != "firstParty":
        return AccountCheck(
            False,
            "你电脑上的 Claude 指向的是第三方通道（Bedrock / Vertex / 自定义端点），"
            "本机生成只接受订阅登录。",
        )
    return AccountCheck(True)


# ---------------------------------------------------------------- Codex


class CodexExecutor:
    """Codex：`codex app-server` 一条 JSON-RPC 连接，文本和图片都走它。

    连接**每条请求现开现关**。长连接能省几百毫秒的启动，但代价是要自己
    管重连、管半死不活的连接、管跨请求的线程状态污染——而我们一次只跑
    一条请求，几百毫秒换掉这一整类问题很划算。
    """

    provider = "codex"

    def __init__(self, *, model: str | None = None) -> None:
        self._model = model
        self._exe: Executable = resolve("codex")
        self._version = _probe_version(self._exe)
        self._account = probe_codex_account(self._exe)
        self._image_ok = self._account.usable and _probe_image_feature(self._exe)
        # 取消状态**每条请求一份**，不是执行器一份。见 `_begin`。
        self._lock = threading.Lock()
        self._current: threading.Event | None = None

    def capabilities(self) -> tuple[str, ...]:
        """没确认是订阅登录就一种能力都不报。

        这条比"清干净环境变量"硬：环境变量只是注入路径之一，
        `config.toml` 里换一个 `model_provider` 同样能把这次调用变成按量付费。
        所以判据取官方的 `codex login status`（免费、不发模型请求），
        不是我们自己推断出来的"应该没问题"。
        """
        if not self._account.usable:
            return ()
        return ("text", "image") if self._image_ok else ("text",)

    def unavailable_reason(self) -> str | None:
        return self._account.reason

    def version(self) -> str | None:
        return self._version

    # ------------------------------------------------------------ 取消

    def _begin(self) -> threading.Event:
        """给这一条请求开一个**新的**取消标志，并把它登记成"当前这条"。

        以前这里是执行器级的一个 `threading.Event`，`cancel()` 设了之后
        **没有任何地方清**：用户取消过一次（或一次等待方超时，心跳同样会
        触发 `cancel()`），这个进程从此对所有后续请求都在第一次检查时就抛
        `TurnCancelledError` —— 界面上表现为"本机永远失败"，而连接器窗口里
        什么错都没有，只能重启进程。

        改成每请求一份而不是"入口处 clear"：`cancel()` 是从心跳线程来的，
        入口 clear 与一次紧邻的 cancel 之间仍有窗口，会把**上一条**的取消
        吞掉或把**这一条**的取消丢掉。换成新对象之后，晚到的 `cancel()`
        只会去设一个已经没人看的旧标志。
        """
        event = threading.Event()
        with self._lock:
            self._current = event
        return event

    def _end(self, event: threading.Event) -> None:
        with self._lock:
            if self._current is event:
                self._current = None

    def cancel(self) -> None:
        with self._lock:
            current = self._current
        if current is not None:
            current.set()

    def _cancel_check(self, own: threading.Event, external: CancelCheck | None) -> CancelCheck:
        def _check() -> bool:
            return own.is_set() or bool(external and external())

        return _check

    def run_text(
        self,
        *,
        system: str,
        user: str,
        timeout_seconds: int,
        should_cancel: CancelCheck | None = None,
    ) -> TextResult:
        if not self._account.usable:
            return TextResult(error_code="auth_required")
        prompt = merge_prompt(system, user)
        own = self._begin()
        try:
            text = asyncio.run(
                self._turn_text(
                    prompt=prompt,
                    timeout_seconds=timeout_seconds,
                    should_cancel=self._cancel_check(own, should_cancel),
                )
            )
        except imagegen.TurnCancelledError:
            return TextResult(error_code="cancelled")
        except ProviderShadowedError as exc:
            # 有界枚举里最贴的是 auth_required（"这台 CLI 现在不是订阅状态"），
            # 具体原因只印在连接器自己的窗口里——它含用户的配置细节，不回传。
            _log_local(str(exc))
            return TextResult(error_code="auth_required")
        except AppServerTimeoutError:
            return TextResult(error_code="timeout")
        except AppServerUnsupportedError:
            return TextResult(error_code="unsupported")
        except (AppServerError, imagegen.TurnFailedError) as exc:
            return TextResult(error_code=adapters.classify(str(exc)))
        finally:
            # 这一条结束了，晚到的 cancel() 不该再落到下一条身上。
            self._end(own)
        if not text.strip():
            return TextResult(error_code="cli_error")
        # 用量不编造：app-server 的 turn 事件里有没有可靠的 token 用量
        # 没有实测过，所以报 None，服务端记 0 并标注"未回报"。
        return TextResult(text=text.strip(), model_id=self._model or "local-cli.codex")

    def run_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        size: str,
        timeout_seconds: int,
        should_cancel: CancelCheck | None = None,
    ) -> ImageResult:
        if not self._account.usable:
            return ImageResult(error_code="auth_required")
        if not self._image_ok:
            return ImageResult(error_code="unsupported")
        wrapped = build_image_prompt(prompt=prompt, negative_prompt=negative_prompt, size=size)
        own = self._begin()
        try:
            outcome = asyncio.run(
                self._turn_image(
                    prompt=wrapped,
                    timeout_seconds=timeout_seconds,
                    should_cancel=self._cancel_check(own, should_cancel),
                )
            )
        except imagegen.TurnCancelledError:
            return ImageResult(error_code="cancelled")
        except ProviderShadowedError as exc:
            _log_local(str(exc))
            return ImageResult(error_code="auth_required")
        except imagegen.UsageLimitExceededError:
            # 订阅额度用尽。**不重试**——重试只会撞同一堵墙，
            # 而服务端有专门的文案告诉用户等额度恢复。
            return ImageResult(error_code="rate_limited")
        except imagegen.NoImageProducedError:
            return ImageResult(error_code="no_image")
        except AppServerTimeoutError:
            return ImageResult(error_code="timeout")
        except AppServerUnsupportedError:
            return ImageResult(error_code="unsupported")
        except (AppServerError, imagegen.TurnFailedError) as exc:
            return ImageResult(error_code=adapters.classify(str(exc)))
        finally:
            self._end(own)
        return ImageResult(
            data=outcome.data,
            mime_type=outcome.mime_type,
            revised_prompt=outcome.revised_prompt,
            model_id=self._model or "local-cli.codex",
        )

    # ------------------------------------------------------------ 内部

    async def _turn_text(
        self, *, prompt: str, timeout_seconds: int, should_cancel: CancelCheck
    ) -> str:
        deadline = time.monotonic() + timeout_seconds
        async with self._session(deadline) as (connection, watcher, workdir, overrides):
            return await imagegen.generate_text(
                connection,
                watcher,
                prompt=prompt,
                cwd=workdir,
                model=self._model,
                model_provider=CHATGPT_MODEL_PROVIDER,
                config_overrides=overrides,
                timeout_seconds=_left(deadline),
                should_cancel=should_cancel,
            )

    async def _turn_image(
        self, *, prompt: str, timeout_seconds: int, should_cancel: CancelCheck
    ) -> imagegen.ImageOutcome:
        deadline = time.monotonic() + timeout_seconds
        async with self._session(deadline) as (connection, watcher, workdir, overrides):
            return await imagegen.generate_image(
                connection,
                watcher,
                prompt=prompt,
                cwd=workdir,
                model=self._model,
                model_provider=CHATGPT_MODEL_PROVIDER,
                config_overrides=overrides,
                timeout_seconds=_left(deadline),
                should_cancel=should_cancel,
            )

    @contextlib.asynccontextmanager
    async def _session(self, deadline: float):  # type: ignore[no-untyped-def]
        """起一条连接 + 一个空工作目录，用完一定收干净。

        工作目录每次都是新的空临时目录：CLI 拿不到仓库上下文，也留不下
        任何东西。连接关掉时会拆掉整棵进程树——留一个跑着的 app-server
        在后台，既占内存又可能继续持有账号会话。

        **握手也从同一份预算里扣。** 服务端发下来的 `timeout_seconds` 是
        这条请求的**剩余**时间（它自己扣掉了排队时间），握手再独占一份
        15 秒的话，整条链加起来就会超过服务端的截止时间——那时服务端
        已经判超时并清掉了请求，桌面却还在画。
        """
        workdir = tempfile.mkdtemp(prefix="aigc-local-runner-")
        watcher = imagegen.TurnWatcher(allowed_roots=imagegen.default_allowed_roots())
        connection = await open_connection(
            build_app_server_argv(self._exe.path)
            if self._exe.kind == "native"
            else [*self._exe.argv_prefix, "app-server", "--listen", "stdio://"],
            cwd=workdir,
            env=adapters.child_env(),
            on_notification=watcher.feed,
            handshake_timeout_seconds=_left(deadline, HANDSHAKE_TIMEOUT_SECONDS),
        )
        try:
            # 收窄权限之前先问一次"这条 provider 还是不是订阅通道"。两件事都走
            # 官方的 `config/read`，免费、不发模型请求，所以放在每条请求上做
            # ——用户可以在两条请求之间改配置，启动时查过一次不等于现在还成立。
            config = await imagegen.read_effective_config(
                connection, timeout_seconds=_left(deadline, 30.0)
            )
            assert_first_party_provider(config, provider=CHATGPT_MODEL_PROVIDER)
            yield connection, watcher, workdir, build_thread_config_overrides(config)
        finally:
            await connection.close()
            shutil.rmtree(workdir, ignore_errors=True)


def _probe_version(exe: Executable) -> str | None:
    """跑一次 `--version`。**不推断登录状态**（查它要么越界读 auth.json，
    要么真发一次请求花钱）。"""
    outcome = Runner().run(
        [*exe.argv_prefix, "--version"],
        stdin_text="",
        cwd=tempfile.gettempdir(),
        env=adapters.child_env(),
        timeout_seconds=30,
    )
    lines = outcome.stdout.strip().splitlines()
    return lines[0].strip() if lines else None


def _probe_image_feature(exe: Executable) -> bool:
    """本机这份 Codex 到底能不能生图 —— 问它自己。

    `codex features list` 的一行形如 `image_generation  stable  true`。
    读不到就当**不能**：宁可少一个入口，也不要摆一个点下去必然失败的。
    这一步不花任何额度，也不需要登录。
    """
    outcome = Runner().run(
        [*exe.argv_prefix, "features", "list"],
        stdin_text="",
        cwd=tempfile.gettempdir(),
        env=adapters.child_env(),
        timeout_seconds=30,
    )
    if outcome.returncode != 0:
        return False
    for line in outcome.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "image_generation":
            return parts[-1].strip().lower() == "true"
    return False


# ---------------------------------------------------------------- Claude


class ClaudeExecutor:
    """Claude：官方 headless `claude -p`。**只有文本。**

    Claude Code 没有原生生图（逐条核过 `--help`），所以 `run_image` 直接
    回 `unsupported`，不去猜、不去接任何外部图像 API——那会把一条
    "用我的会员额度"的路径悄悄变成一条按量付费的路径。
    """

    provider = "claude"

    def __init__(self, *, model: str | None = None) -> None:
        self._model = model
        self._exe: Executable = resolve("claude")
        self._version = _probe_version(self._exe)
        self._account = probe_claude_account(self._exe)
        self._runner = Runner()

    def capabilities(self) -> tuple[str, ...]:
        """没确认是订阅登录就不报能力。理由同 `CodexExecutor.capabilities`。"""
        return ("text",) if self._account.usable else ()

    def unavailable_reason(self) -> str | None:
        return self._account.reason

    def version(self) -> str | None:
        return self._version

    def cancel(self) -> None:
        self._runner.cancel()

    def run_text(
        self,
        *,
        system: str,
        user: str,
        timeout_seconds: int,
        should_cancel: CancelCheck | None = None,
    ) -> TextResult:
        # `should_cancel` 在这条路上用不上：子进程一旦起来就只能等它或杀它，
        # 取消由 cli.py 调 `cancel()` 杀进程树来实现（那是从另一个线程来的）。
        del should_cancel
        if not self._account.usable:
            return TextResult(error_code="auth_required")
        workdir = tempfile.mkdtemp(prefix="aigc-local-runner-")
        try:
            job = adapters.build_claude(
                self._exe, workdir=workdir, system=system, user=user, model=self._model
            )
            outcome = self._runner.run(
                job.argv,
                stdin_text=job.stdin_text,
                cwd=workdir,
                env=job.env,
                timeout_seconds=timeout_seconds,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        parsed = adapters.parse_claude(job, outcome, model=self._model)
        return TextResult(
            text=parsed.text,
            error_code=parsed.error_code,
            model_id=parsed.model_id,
            tokens_in=parsed.tokens_in,
            tokens_out=parsed.tokens_out,
        )

    def run_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        size: str,
        timeout_seconds: int,
        should_cancel: CancelCheck | None = None,
    ) -> ImageResult:
        del prompt, negative_prompt, size, timeout_seconds, should_cancel
        return ImageResult(error_code="unsupported")


def build_executor(provider: str, *, model: str | None = None) -> Executor:
    """按 provider 造一个执行器。没装就抛 `NotInstalledError`，不假装支持。"""
    if provider == "codex":
        return CodexExecutor(model=model)
    if provider == "claude":
        return ClaudeExecutor(model=model)
    raise NotInstalledError(f"不支持的 provider：{provider}")
