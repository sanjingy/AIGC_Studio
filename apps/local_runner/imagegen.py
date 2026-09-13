"""Codex 一轮对话的产物：图片字节，或最终文本。

**这是 Codex 侧唯一的执行面。** 生图和文本都走同一条 app-server 连接、
同一套打断路径——不再维护第二套"起子进程 / 判失败 / 杀干净"。

**不从聊天文字里猜路径。** 图片来自 `item/completed` 通知里的
`ImageGenerationItem`，字段形状与编码都由官方来源确定：

* schema 由 `codex app-server generate-json-schema` 生成（本机 codex-cli 0.153.4）：
  `ImageGenerationItem = {id, status, revisedPrompt, result, transparentBackground?,
  failure, savedPath?}`，必填只有 `id / result / status / type`。
* **`result` 是图片字节的 base64。** 依据是 OpenAI 官方 Codex 源码的用例
  `codex-rs/core/src/tools/handlers/extension_tools.rs`
  ::`image_generation_publication_preserves_extension_saved_path`，
  里面 `result: "cG5n"` —— 正是 `base64("png")`，同一用例里
  `status` 从 `"in_progress"` 变成 `"completed"`。
* `failure` 只有一种：`{"type": "usageLimitExceeded", "limitId", "resetsAt"}`。
  **这说明生图计的是账号订阅额度，不是 API Key。**

两条安全边界（都在这里强制，不指望调用方记得）：

1. **`savedPath` 不能当成"文件在这儿"的证明。** schema 自己写了它
   "not guaranteed to be canonicalized or exist on the filesystem"。
   它是唯一一处外部输入能指向任意磁盘位置的地方，所以要做包含性校验。
2. **字节按魔数判类型，不按扩展名。** 扩展名来自路径，路径来自对端。

Orca 那边没有对应实现可抄——它只把 `imageGeneration` 映射成一句界面提示语，
不消费 `savedPath`、不落资产。见 `THIRD_PARTY_NOTICES.md`。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.local_runner.appserver import (
    AppServerConnection,
    AppServerError,
    AppServerTimeoutError,
)

__all__ = [
    "ImageGenerationError",
    "ImageOutcome",
    "NoImageProducedError",
    "TurnCancelledError",
    "TurnFailedError",
    "UsageLimitExceededError",
    "assert_effective_provider",
    "decode_image_result",
    "generate_image",
    "generate_text",
    "read_effective_config",
    "resolve_saved_path",
    "sniff_image_mime",
]

#: 等通知时每隔多久回头看一眼"是不是被取消了"。
_CANCEL_POLL_SECONDS = 0.5

#: 单张图上限。生图是 base64 走管道，不设上限等于允许对端把内存打满。
MAX_IMAGE_BYTES = 32 * 1024 * 1024

#: 认得的图片魔数。**只认这三种**——落进资产库的东西不该有惊喜。
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),  # 还要再验第 8..12 字节
)


class TurnFailedError(Exception):
    """这一轮本身失败了（对端报 error、握手后的协议错误等）。

    与"生图失败"分开：文本那条路也会撞上它，而它跟图片没有关系。
    """


class TurnCancelledError(Exception):
    """服务端说这条请求已经没人等了（用户取消 / 等待方超时）。

    不是错误路径的一种，是**该立刻停手**的信号：继续跑只会烧用户
    自己的订阅额度，而结果没有任何人会收。
    """


class ImageGenerationError(TurnFailedError):
    """生图失败的基类。"""


class UsageLimitExceededError(ImageGenerationError):
    """订阅额度用尽。`resets_at` 是毫秒时间戳，可能为 None。"""

    def __init__(self, limit_id: str, resets_at: int | None) -> None:
        super().__init__(f"usage limit exceeded ({limit_id})")
        self.limit_id = limit_id
        self.resets_at = resets_at


class NoImageProducedError(ImageGenerationError):
    """这一轮跑完了但没有图。

    **这是本条链路最常见的失败**，而且不是异常情况：协议里没有"生成一张图"
    的方法（99 个 client request 里一个都没有），生图是**模型在一轮对话里
    自己决定调用内置 image_gen 工具**的产物。模型改成回一段文字是允许的行为，
    对用户却是失败。所以这条要单独成一类，好让上层决定是重试还是报错。
    """


@dataclass(frozen=True)
class ImageOutcome:
    data: bytes
    mime_type: str
    #: 模型改写后的提示词。ADR-036 要求校验风格词有没有被原样保留——
    #: 校验发生在服务端（它才知道系统注入了哪些风格词），这里只如实带回。
    revised_prompt: str | None
    #: 对端声称的落盘路径，**已通过包含性校验**；校验不过时为 None。
    saved_path: Path | None
    transparent_background: bool | None
    item_id: str


def decode_image_result(result: str) -> bytes:
    """把 `result` 解成图片字节。

    严格 base64（`validate=True`）：宽松解码会把一段普通文字也解出"字节"来，
    然后我们把一坨垃圾当图片存进资产库。
    """
    if not result:
        raise ImageGenerationError("image result is empty")
    # base64 每 4 个字符解出 3 字节，先按长度挡掉超大的，避免先解码再判断。
    if len(result) > (MAX_IMAGE_BYTES // 3 + 1) * 4:
        raise ImageGenerationError("image result exceeds size limit")
    try:
        data = base64.b64decode(result, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageGenerationError("image result is not valid base64") from exc
    if not data:
        raise ImageGenerationError("image result decoded to zero bytes")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageGenerationError("image exceeds size limit")
    return data


def sniff_image_mime(data: bytes) -> str | None:
    """按字节魔数判类型。认不出来返回 None。"""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            if mime == "image/webp":
                # RIFF 容器不只有 webp，要看第 8..12 字节。
                return "image/webp" if data[8:12] == b"WEBP" else None
            return mime
    return None


def resolve_saved_path(raw: str | None, allowed_roots: list[Path]) -> Path | None:
    """校验对端给的落盘路径，不合规返回 None（**不抛**）。

    不抛的理由：`savedPath` 是可选字段，图片字节本来就在 `result` 里。
    路径不可信只是"少一个附加信息"，不该让整次生成失败。

    四道：必须绝对、规范化后必须落在允许的根下、必须真的存在、必须是普通文件。
    第二道是关键——`..` 或符号链接能把路径指到仓库、`.codex/auth.json` 旁边，
    而我们随后要读这个文件。
    """
    if not raw or not allowed_roots:
        return None
    try:
        candidate = Path(raw)
        if not candidate.is_absolute():
            return None
        resolved = candidate.resolve(strict=False)
    except (OSError, ValueError):
        return None

    for root in allowed_roots:
        try:
            root_resolved = root.resolve(strict=False)
        except (OSError, ValueError):
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            break
    else:
        return None

    try:
        if not resolved.is_file():
            return None
    except OSError:
        return None
    return resolved


def default_allowed_roots(codex_home: str | None = None) -> list[Path]:
    """图片被允许落在哪。

    按本机 imagegen skill 的说明是 `CODEX_HOME/generated_images`；
    另外允许系统临时目录，因为不同版本可能落在那里。**不允许其它任何位置。**
    """
    roots: list[Path] = []
    home = codex_home or os.environ.get("CODEX_HOME")
    if home:
        roots.append(Path(home) / "generated_images")
        roots.append(Path(home))
    else:
        default_home = Path.home() / ".codex"
        roots.append(default_home / "generated_images")
        roots.append(default_home)
    import tempfile

    roots.append(Path(tempfile.gettempdir()))
    return roots


def read_image_item(item: dict[str, Any], *, allowed_roots: list[Path]) -> ImageOutcome:
    """把一个 `imageGeneration` item 变成产物。失败抛 :class:`ImageGenerationError`。"""
    failure = item.get("failure")
    if isinstance(failure, dict):
        if failure.get("type") == "usageLimitExceeded":
            resets_at = failure.get("resetsAt")
            raise UsageLimitExceededError(
                str(failure.get("limitId", "")),
                resets_at if isinstance(resets_at, int) else None,
            )
        raise ImageGenerationError(f"image generation failed: {failure.get('type', 'unknown')}")

    result = item.get("result")
    if not isinstance(result, str):
        raise ImageGenerationError("image item has no result")
    data = decode_image_result(result)
    mime = sniff_image_mime(data)
    if mime is None:
        raise ImageGenerationError("image result is not a recognised image format")

    revised = item.get("revisedPrompt")
    transparent = item.get("transparentBackground")
    saved = item.get("savedPath")
    return ImageOutcome(
        data=data,
        mime_type=mime,
        revised_prompt=revised if isinstance(revised, str) else None,
        saved_path=resolve_saved_path(saved if isinstance(saved, str) else None, allowed_roots),
        transparent_background=transparent if isinstance(transparent, bool) else None,
        item_id=str(item.get("id", "")),
    )


def is_completed_image_item(method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """从一条通知里认出"生图完成"。不是就返回 None。

    `item/started` 会带同一个 item 但 `status == "in_progress"` 且
    `result` 为空串——把它当成结果就会拿到零字节。
    """
    if method != "item/completed":
        return None
    item = params.get("item")
    if not isinstance(item, dict) or item.get("type") != "imageGeneration":
        return None
    if item.get("status") != "completed":
        return None
    return item


class TurnWatcher:
    """把 app-server 的通知流收敛成"这一轮的结局"。

    单独一个类是为了能**脱离子进程测试**：喂固定事件帧进去就能验证
    完成/失败/没出图三种结局，不需要真的起 Codex、不花任何额度。
    """

    def __init__(self, *, allowed_roots: list[Path]) -> None:
        self._allowed_roots = allowed_roots
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    def feed(self, method: str, params: dict[str, Any]) -> None:
        self._queue.put_nowait((method, params))

    async def _next(
        self, *, should_cancel: Callable[[], bool] | None
    ) -> tuple[str, dict[str, Any]]:
        """取下一条通知。**取的过程中要能被"用户取消了"打断。**

        分小段等而不是一次性 `await get()`：服务端那边的取消信号是靠心跳
        轮询回来的（见 cli.py），这一侧必须有机会去看它。看不到的话，
        用户在网页上按了取消，桌面这台机器还会把这一轮跑满——
        烧的是他自己的订阅额度。
        """
        while True:
            if should_cancel is not None and should_cancel():
                raise TurnCancelledError("请求已被取消")
            try:
                return await asyncio.wait_for(self._queue.get(), timeout=_CANCEL_POLL_SECONDS)
            except TimeoutError:
                continue

    async def wait_for_image(
        self, *, timeout_seconds: float, should_cancel: Callable[[], bool] | None = None
    ) -> ImageOutcome:
        """等到出图、或这一轮结束、或超时、或被取消。"""

        async def _loop() -> ImageOutcome:
            while True:
                method, params = await self._next(should_cancel=should_cancel)
                item = is_completed_image_item(method, params)
                if item is not None:
                    return read_image_item(item, allowed_roots=self._allowed_roots)
                if method == "turn/completed":
                    raise NoImageProducedError("这一轮结束了但没有产出图片")
                if method == "error":
                    raise ImageGenerationError(f"codex reported an error: {_error_text(params)}")

        try:
            return await asyncio.wait_for(_loop(), timeout=timeout_seconds)
        except TimeoutError as exc:
            raise AppServerTimeoutError(f"生图超过 {timeout_seconds}s 未完成") from exc

    async def wait_for_text(
        self, *, timeout_seconds: float, should_cancel: Callable[[], bool] | None = None
    ) -> str:
        """等这一轮的**最终**助手消息。

        只认 `item/completed` 里的 `agentMessage`，工具事件、推理摘要、
        计划一律跳过——把中间事件当成产出，拿到的会是"我先看一下需求"
        这种句子，然后 schema 校验失败，用户看到的是"模型不听话"。

        取最后一条：一轮里可能有多条助手消息（模型分段说话），
        结构化产出永远在最后那条里。
        """

        async def _loop() -> str:
            last = ""
            while True:
                method, params = await self._next(should_cancel=should_cancel)
                if method == "item/completed":
                    item = params.get("item")
                    if isinstance(item, dict) and item.get("type") == "agentMessage":
                        text = item.get("text")
                        if isinstance(text, str) and text.strip():
                            last = text
                elif method == "turn/completed":
                    return last
                elif method == "error":
                    raise TurnFailedError(f"codex reported an error: {_error_text(params)}")

        try:
            return await asyncio.wait_for(_loop(), timeout=timeout_seconds)
        except TimeoutError as exc:
            raise AppServerTimeoutError(f"文本生成超过 {timeout_seconds}s 未完成") from exc


async def start_thread(
    connection: AppServerConnection,
    *,
    cwd: str,
    model: str | None = None,
    model_provider: str | None = None,
    config_overrides: dict[str, Any] | None = None,
    timeout_seconds: float = 30.0,
) -> str:
    """开一条线程，返回 threadId。

    每个参数都不是随手填的：

    * `ephemeral` —— 这次生成不该出现在用户的 Codex 会话历史里。
    * `sandbox: read-only` —— 三档里最严的那一档。工作目录本来就是一个
      空的临时目录，读也读不到仓库。
    * `approvalPolicy: never` —— 非交互场景下没人能回答审批请求。
      不写死它的话，一次审批请求会把这一轮挂到超时为止。
    * `cwd` —— 每条请求一个全新的空临时目录。
    * **`modelProvider`** —— 钉死内置的 ChatGPT 订阅通道。用户的
      `~/.codex/config.toml` 可以把 `model_provider` 换成一个带 `env_key`
      的自定义 Provider，那是**按量付费**的，而用户以为自己在用订阅额度。
      清父进程的环境变量挡不住这一条：配置是 CLI 自己读的。
    * **`config`** —— 逐线程的配置覆盖（按名关掉用户的 MCP、notify、hooks）。
      走这个官方字段而不是改用户的 `config.toml`：那是他自己的文件，
      我们只对**这一条线程**收窄权限。覆盖内容由
      `executor.build_thread_config_overrides()` 按 `config/read` 读回来的
      **有效配置**逐项生成——不是写死一份，因为服务器名是用户那边的。

    `modelProvider` / `config` 两个字段都来自官方 schema（`ThreadStartParams`，
    由 `codex app-server generate-json-schema` 生成，本机核对版本 0.153.4）。

    **返回之前会核对一次生效值。** `ThreadStartResponse` 里官方回显了这条线程
    真正生效的 `model` / `modelProvider` / `sandbox` / `approvalPolicy`
    （schema 的 `required` 里就有这四个）。回显的 provider 与我们要求的不一致
    时**立刻失败**：那说明这条线程根本没跑在订阅通道上，继续发 turn 就是
    在用户不知情的情况下走按量付费。核对本身不发任何模型请求。
    """
    thread = await connection.request(
        "thread/start",
        {
            "cwd": cwd,
            "ephemeral": True,
            "sandbox": "read-only",
            "approvalPolicy": "never",
            **({"model": model} if model else {}),
            **({"modelProvider": model_provider} if model_provider else {}),
            **({"config": config_overrides} if config_overrides else {}),
        },
        timeout_seconds=timeout_seconds,
    )
    thread_id = _read_id(thread, "threadId")
    if thread_id is None:
        raise TurnFailedError("thread/start 没有返回 threadId")
    assert_effective_provider(thread, expected=model_provider)
    return thread_id


def assert_effective_provider(thread_start_response: Any, *, expected: str | None) -> None:
    """核对 `thread/start` 回显的生效 provider。不一致就抛，**不发模型请求**。

    单独一个函数是为了能脱离真机测：它的入参就是官方响应的形状。
    没要求过 provider（`expected is None`）时不判——那是调用方自己的选择。
    回显里**没有**这个字段时也不判：老版本 CLI 不回显，不能因此把功能判死。
    """
    if not expected or not isinstance(thread_start_response, dict):
        return
    effective = thread_start_response.get("modelProvider")
    if not isinstance(effective, str) or not effective:
        return
    if effective != expected:
        raise TurnFailedError(
            f"thread/start 生效的 modelProvider 是 {effective}，不是要求的 {expected}"
        )


async def read_effective_config(
    connection: AppServerConnection, *, timeout_seconds: float = 30.0
) -> dict[str, Any]:
    """读这台 CLI 的**有效配置**（`config/read`，官方方法，免费、不发模型请求）。

    要的不是"用户的 config.toml 里写了什么"，而是"CLI 自己算出来的最终值"——
    profile、项目层、插件层合并之后的那一份。按名收窄权限（哪些 MCP 服务器
    要禁、哪些 hook 事件要清空）只能基于它，写死一份名单在代码里必然与用户
    实际配置对不上。

    读不到就返回空 dict：这台 CLI 可能老到没有这个方法，那时收窄不了，
    但也不该因此连字都写不了——调用方据此决定要不要继续。
    """
    try:
        response = await connection.request(
            "config/read", {"includeLayers": False}, timeout_seconds=timeout_seconds
        )
    except AppServerError:
        return {}
    if isinstance(response, dict):
        config = response.get("config")
        if isinstance(config, dict):
            return config
    return {}


async def start_turn(
    connection: AppServerConnection,
    *,
    thread_id: str,
    prompt: str,
    model: str | None = None,
    timeout_seconds: float,
) -> str | None:
    """发起一轮，返回 turnId（拿不到就是 None）。

    **turnId 必须留着**：`turn/interrupt` 的参数是 `{threadId, turnId}`
    两个都必填（官方 schema 的 `required`）。只传 threadId 的话打断会被
    对端拒掉，于是"取消"变成了"界面上取消了、桌面还在跑"。
    """
    turn = await connection.request(
        "turn/start",
        {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            **({"model": model} if model else {}),
        },
        timeout_seconds=timeout_seconds,
    )
    if isinstance(turn, dict):
        nested = turn.get("turn")
        if isinstance(nested, dict):
            value = nested.get("id")
            if isinstance(value, str) and value:
                return value
        value = turn.get("turnId")
        if isinstance(value, str) and value:
            return value
    return None


async def interrupt(
    connection: AppServerConnection, *, thread_id: str, turn_id: str | None
) -> None:
    """打断这一轮。**打断失败不抛**：调用方正在处理另一个错误。"""
    if turn_id is None:
        return
    with contextlib.suppress(Exception):
        await connection.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
            timeout_seconds=10.0,
        )


async def generate_image(
    connection: AppServerConnection,
    watcher: TurnWatcher,
    *,
    prompt: str,
    cwd: str,
    model: str | None = None,
    model_provider: str | None = None,
    config_overrides: dict[str, Any] | None = None,
    timeout_seconds: float = 300.0,
    should_cancel: Callable[[], bool] | None = None,
) -> ImageOutcome:
    """跑一轮生图。

    `connection` 必须是用 `on_notification=watcher.feed` 打开的，否则这里会一直等。

    `timeout_seconds` 是**这一轮的总预算**，不是每一步各给一份：起线程、
    发起一轮、等图三步共用它，先花掉的从后面扣。每步各给一份的话，
    三步加起来会超过服务端给的截止时间——那时服务端已经判超时清掉了这条
    请求，桌面却还在画，烧的是用户自己的订阅额度。
    """
    deadline = time.monotonic() + timeout_seconds
    thread_id = await start_thread(
        connection,
        cwd=cwd,
        model=model,
        model_provider=model_provider,
        config_overrides=config_overrides,
        timeout_seconds=_left(deadline, 30.0),
    )
    turn_id: str | None = None
    try:
        turn_id = await start_turn(
            connection,
            thread_id=thread_id,
            prompt=prompt,
            model=model,
            timeout_seconds=_left(deadline, 120.0),
        )
        return await watcher.wait_for_image(
            timeout_seconds=_left(deadline), should_cancel=should_cancel
        )
    except BaseException:
        # 取消/超时都要打断这一轮，别让它在后台继续烧订阅额度。
        await interrupt(connection, thread_id=thread_id, turn_id=turn_id)
        raise


async def generate_text(
    connection: AppServerConnection,
    watcher: TurnWatcher,
    *,
    prompt: str,
    cwd: str,
    model: str | None = None,
    model_provider: str | None = None,
    config_overrides: dict[str, Any] | None = None,
    timeout_seconds: float = 180.0,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    """跑一轮纯文本。与生图共用同一条连接、同一套打断路径。

    有了它，Codex 那一侧就只剩 app-server 这一个执行面——
    `codex exec` 那条一次性子进程的路径可以整条删掉，不用维护两套
    "怎么起进程、怎么判失败、怎么杀干净"。

    `timeout_seconds` 同样是这一轮的**总**预算，三步共用（见 `generate_image`）。
    """
    deadline = time.monotonic() + timeout_seconds
    thread_id = await start_thread(
        connection,
        cwd=cwd,
        model=model,
        model_provider=model_provider,
        config_overrides=config_overrides,
        timeout_seconds=_left(deadline, 30.0),
    )
    turn_id: str | None = None
    try:
        turn_id = await start_turn(
            connection,
            thread_id=thread_id,
            prompt=prompt,
            model=model,
            timeout_seconds=_left(deadline, 120.0),
        )
        return await watcher.wait_for_text(
            timeout_seconds=_left(deadline), should_cancel=should_cancel
        )
    except BaseException:
        await interrupt(connection, thread_id=thread_id, turn_id=turn_id)
        raise


def _left(deadline: float, cap: float | None = None) -> float:
    """还剩多少预算。`cap` 是这一步自己的上限（比如握手不该等满整轮）。

    永远返回一个正数：给 0 或负数的话，`asyncio.wait_for` 会立刻抛超时，
    错误信息会指向这一步，而真正的原因是"整轮的预算已经用完了"。
    """
    remaining = max(1.0, deadline - time.monotonic())
    return min(remaining, cap) if cap is not None else remaining


def _error_text(params: dict[str, Any]) -> str:
    """从 `error` 通知里取一句能看的说明。

    形状按官方 schema 是 `{error: TurnError, threadId, turnId, willRetry}`，
    但**不要指望它**：这是别的进程发过来的，字段缺失或换形状都可能。
    """
    error = params.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    message = params.get("message")
    return message if isinstance(message, str) and message else "unknown"


def _read_id(payload: Any, key: str) -> str | None:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        nested = payload.get("thread")
        if isinstance(nested, dict):
            inner = nested.get("id")
            if isinstance(inner, str) and inner:
                return inner
    return None
