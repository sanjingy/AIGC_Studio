"""本机运行时的业务层。

对外能力：

* :func:`applies_to` / :func:`image_applies_to` —— 这一条请求**能不能**走本机。
  逐请求判定，不是全局开关：同一个部署里，白名单外的项目照常走 Mock / Gateway。
* :func:`preflight_image` —— 用户在网页上选了"本机出图"之后，**建任务之前**
  先把不可能成功的情况挡掉（没开、没连上、那台连接器不支持出图）。
  挡在建任务之前是有意的：建了任务就要预扣，预扣完再失败还得退，
  用户还会在任务列表里看到一条本来就不该存在的失败记录。
* :func:`complete_text` / :func:`complete_image` —— 把一条请求送到桌面，等结果。
* :func:`take_request` / :func:`heartbeat` / :func:`submit_result` /
  :func:`describe_status` —— 四个端点的实质逻辑。

**这条路径不做 failover。** 选中了本机就只有本机：失败必须以失败告终，
不许悄悄退回 Gateway（那是付费的，用户以为自己在用订阅额度），
也不许退回 Mock（那会把假数据当成真产出写进档案）。

**计费口径**：本机出图照常走 `image.generate` 任务，照常预扣、照常结算——
`render.py` 那条链路一个字都没为它改。这不是"顺手做的"，是刻意的：
"用户自己的订阅额度折算多少 Credits"这件事今天没有定义，而在没有定义之前
把它当成免费，等于凭空改了资金语义。失败时按错误目录 RELEASE 退回预扣
（平台侧确实没有产生上游成本），成功时与 API 出图同价。这条限制写在
`15_LOCAL_RUNTIME.md`，对外开放前必须先定折算规则。
"""

from __future__ import annotations

import base64
import binascii
import io
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError
from PIL.Image import DecompressionBombError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.config import get_settings
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.local_runtime import transport
from apps.api.modules.local_runtime.schemas import Capabilities, StatusOut
from apps.api.modules.project import service as project_service

log = get_logger(__name__)

#: 单条请求的提示词总长上限。桌面 CLI 的输入是从 stdin 或 JSON-RPC 送进去的，
#: 不设上限等于允许任何人用一条请求把连接器的内存打满。
MAX_PROMPT_CHARS = 200_000

#: 解码后的图片上限。与连接器侧那道闸同一个数量级，两边都要有——
#: 一边是防对端、一边是防自己写进对象存储。
MAX_IMAGE_BYTES = 12 * 1024 * 1024

#: 等结果时每一小段的长度。分段是为了在等待中途还能发现"用户取消了"。
_WAIT_SLICE_SECONDS = 5

#: 认得的图片魔数。**按字节判，不按对端说的 MIME 判。**
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),  # 还要再验第 8..12 字节
)

#: Pillow 解出来的格式名 → MIME。两边必须对得上，对不上就是可疑。
_FORMAT_OF: dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}

#: 解压后的像素上限（约 40 兆像素 ≈ 6300×6300）。
#: 字节数小不代表解压后小——这就是解压炸弹。出图端的产物远小于这个数。
MAX_IMAGE_PIXELS = 40_000_000

#: 最小边长。几像素的"图"不是产物，是一次失败的生成或者一段垃圾。
MIN_IMAGE_SIDE = 16

#: 有界错误码 → 平台错误目录。
#:
#: 文本那条沿用 `provider.*`（它替代的就是一次 Provider 调用，
#: 用户看到的失败与上游失败没有区别）；出图这条用 `local_runtime.*`，
#: 因为**能修的人是用户自己**——去桌面上启动连接器、登录 Codex、等额度。
#: 复用"正在切换备用通道"那句文案，用户永远不知道该点哪里。
_TEXT_ERROR_MAP: dict[str, str] = {
    "timeout": "provider.transient.timeout",
    "rate_limited": "provider.rate_limit.exceeded",
    "auth_required": "provider.unavailable",
    "cli_error": "provider.unavailable",
    "unsupported": "provider.params.invalid",
    "cancelled": "provider.unavailable",
    "no_image": "provider.unavailable",
}

_IMAGE_ERROR_MAP: dict[str, str] = {
    "timeout": "local_runtime.timeout",
    "rate_limited": "local_runtime.usage_limit",
    "auth_required": "local_runtime.auth_required",
    "cli_error": "local_runtime.failed",
    "unsupported": "local_runtime.capability_unsupported",
    "cancelled": "local_runtime.failed",
    "no_image": "local_runtime.no_image",
    # 只在服务端内部产生：回传的不是一张认得出的图。
    "result_invalid": "local_runtime.result_invalid",
}


@dataclass(frozen=True, slots=True)
class LocalCompletion:
    text: str
    model_id: str
    #: 本机 CLI 不回报 token 用量时为 0。**0 的含义是「未回报」而不是「没用」**，
    #: 不许拿它去反推成本——见 15_LOCAL_RUNTIME.md 的「不做什么」。
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True, slots=True)
class LocalImage:
    data: bytes
    mime_type: str
    #: 如实标注是谁画的。写成 wan2.x 之类会让资产 metadata 与真实执行者
    #: 对不上，而那是后面所有成本复盘与画风追溯的依据。
    model_id: str
    #: 模型改写后的提示词。Codex 会改写（`revisedPrompt`），
    #: 落进资产 metadata 的 `actual_prompt`，与万相那条链路同一个字段名。
    revised_prompt: str | None


def is_enabled() -> bool:
    return get_settings().local_cli_enabled


def text_provider() -> str:
    return get_settings().local_cli_provider


def image_provider() -> str:
    return get_settings().local_cli_image_provider


def _in_scope(org_id: uuid.UUID | None, project_id: uuid.UUID | None) -> bool:
    """开关 + org + project 白名单。三者缺一不可，**没有「缺省即命中」**。"""
    settings = get_settings()
    if not settings.local_cli_enabled:
        return False
    if org_id is None or project_id is None:
        return False
    if settings.local_cli_org_id != org_id:
        return False
    return project_id in set(settings.local_cli_project_ids)


def applies_to(org_id: uuid.UUID | None, project_id: uuid.UUID | None) -> bool:
    """这一条**文本**请求走不走本机 CLI。

    不挂项目的调用（资产库里那条直接生成角色档案的路径）一律走原来的实现。
    """
    return _in_scope(org_id, project_id)


def image_applies_to(org_id: uuid.UUID | None, project_id: uuid.UUID | None) -> bool:
    """这一条**出图**请求能不能走本机。

    与文本同一份白名单：试点范围是"这个项目"，不是"这个能力"。
    """
    return _in_scope(org_id, project_id)


async def preflight_image(org_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """建任务之前先判死。不通过就抛，抛的是能告诉用户该干什么的错误码。"""
    if not image_applies_to(org_id, project_id):
        raise AppError(
            "local_runtime.not_configured",
            message="local runtime is not configured for this org/project",
        )
    provider = image_provider()
    state = await transport.runner_state(provider)
    if state is None:
        raise AppError(
            "local_runtime.offline",
            message=f"no local runner heartbeat for {provider}",
        )
    if "image" not in state.kinds:
        raise AppError(
            "local_runtime.capability_unsupported",
            message=f"local runner {provider} does not report image capability",
            detail={"kinds": list(state.kinds)},
        )


async def complete_text(
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    system: str,
    user: str,
    schema_name: str,
    max_output_tokens: int,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> LocalCompletion:
    """在桌面上跑一次文本生成。

    先判活再入队：连接器没在跑的时候直接失败，比让用户干等三分钟再看到
    超时要好得多——后者他还会以为是模型慢。

    等结果与出图**走同一套分段等待**（`_await_reply_watching_cancel`）。
    以前这里是一次 `await_reply(timeout_seconds=180)`，底层就是一次
    `BLPOP`：整整 180 秒（可配到 900）占住 `core/redis.py` 那个**进程级
    共享池**里的一条连接，而 SSE、熔断器、幂等键都在用同一个池。
    分段之后顺带把取消能力也拿到了——两条路径从此只有一种等法。
    """
    if not applies_to(org_id, project_id):
        # 兜底。正常路径上 llm.py 已经判过一次，走到这里说明有人绕过了选择器。
        raise AppError(
            "common.forbidden",
            message="local runtime not configured for this org/project",
        )

    settings = get_settings()
    provider = settings.local_cli_provider
    timeout_seconds = settings.local_cli_timeout_seconds

    if len(system) + len(user) > MAX_PROMPT_CHARS:
        raise AppError(
            "provider.params.invalid",
            message=f"local runtime prompt exceeds {MAX_PROMPT_CHARS} chars",
        )

    state = await transport.runner_state(provider)
    if state is None or "text" not in state.kinds:
        raise AppError(
            "provider.unavailable",
            message=f"local runner for {provider} is not connected",
        )

    request_id = uuid.uuid4()
    try:
        accepted = await transport.enqueue(
            provider=provider,
            kind="text",
            request_id=request_id,
            timeout_seconds=timeout_seconds,
            text=transport.TextJob(
                system=system,
                user=user,
                schema_name=schema_name,
                max_output_tokens=max_output_tokens,
            ),
        )
        if not accepted:
            raise AppError(
                "provider.rate_limit.exceeded",
                message="local runtime queue is full",
            )
        log.info(
            # 只记形状，不记内容：提示词里有用户的小说原文。
            "local_runtime.enqueued",
            request_id=str(request_id),
            provider=provider,
            kind="text",
            schema_name=schema_name,
            prompt_chars=len(system) + len(user),
        )
        reply = await _await_reply_watching_cancel(
            request_id=request_id,
            deadline=time.monotonic() + timeout_seconds,
            is_cancelled=is_cancelled,
            cancelled_error="provider.unavailable",
        )
    finally:
        # 超时、取消、异常都要清干净：留在队列里的请求会被下一次 poll 捞去
        # 再跑一遍，而用户那边早已看到失败。
        await transport.cleanup(provider=provider, kind="text", request_id=request_id)

    if reply is None:
        log.warning("local_runtime.timeout", request_id=str(request_id), provider=provider)
        raise AppError(
            "provider.transient.timeout",
            message=f"local runner did not answer within {timeout_seconds}s",
        )

    if reply.error_code:
        code = _TEXT_ERROR_MAP.get(reply.error_code, "provider.unavailable")
        log.warning(
            "local_runtime.failed",
            request_id=str(request_id),
            provider=provider,
            kind="text",
            error_code=reply.error_code,
        )
        raise AppError(code, message=f"local runner reported {reply.error_code}")

    if not reply.text or not reply.text.strip():
        raise AppError(
            "provider.unavailable",
            message="local runner returned empty text",
        )

    return LocalCompletion(
        text=reply.text,
        # 如实标注是谁跑的。写成 deepseek-chat 之类会让 agent_runs.model_id
        # 与真实执行者对不上，那张表是后面所有成本复盘的依据。
        model_id=reply.model_id or f"local-cli.{provider}",
        tokens_in=reply.tokens_in or 0,
        tokens_out=reply.tokens_out or 0,
    )


async def complete_image(
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    prompt: str,
    negative_prompt: str = "",
    size: str = "1024*1024",
    n: int = 1,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> LocalImage:
    """在桌面上跑一次出图，把图片字节拿回来。

    `is_cancelled` 由 Worker 传进来（读 `tasks.status`）。等待被切成小段，
    每段之间问一次——用户在网页上按了取消，这里就要立刻把租约清掉，
    连接器下一次心跳看到"这条已经没人等了"就会打断那一轮，
    不再继续烧订阅额度。没有它的话，取消只是把界面上的状态改了，
    桌面上那台机器还要跑满几分钟。
    """
    if not image_applies_to(org_id, project_id):
        raise AppError(
            "local_runtime.not_configured",
            message="local runtime not configured for this org/project",
        )

    settings = get_settings()
    provider = settings.local_cli_image_provider
    timeout_seconds = settings.local_cli_image_timeout_seconds

    if len(prompt) > MAX_PROMPT_CHARS:
        raise AppError(
            "provider.params.invalid",
            message=f"local runtime prompt exceeds {MAX_PROMPT_CHARS} chars",
        )

    state = await transport.runner_state(provider)
    if state is None:
        raise AppError("local_runtime.offline", message=f"no heartbeat for {provider}")
    if "image" not in state.kinds:
        raise AppError(
            "local_runtime.capability_unsupported",
            message=f"local runner {provider} cannot generate images",
        )

    request_id = uuid.uuid4()
    deadline = time.monotonic() + timeout_seconds
    try:
        accepted = await transport.enqueue(
            provider=provider,
            kind="image",
            request_id=request_id,
            timeout_seconds=timeout_seconds,
            image=transport.ImageJob(
                prompt=prompt, negative_prompt=negative_prompt, size=size, n=n
            ),
        )
        if not accepted:
            raise AppError("local_runtime.busy", message="local runtime queue is full")
        log.info(
            "local_runtime.enqueued",
            request_id=str(request_id),
            provider=provider,
            kind="image",
            prompt_chars=len(prompt),
        )
        reply = await _await_reply_watching_cancel(
            request_id=request_id, deadline=deadline, is_cancelled=is_cancelled
        )
    finally:
        await transport.cleanup(provider=provider, kind="image", request_id=request_id)

    if reply is None:
        log.warning(
            "local_runtime.timeout", request_id=str(request_id), provider=provider, kind="image"
        )
        raise AppError(
            "local_runtime.timeout",
            message=f"local runner did not answer within {timeout_seconds}s",
        )

    if reply.error_code:
        code = _IMAGE_ERROR_MAP.get(reply.error_code, "local_runtime.failed")
        log.warning(
            "local_runtime.failed",
            request_id=str(request_id),
            provider=provider,
            kind="image",
            error_code=reply.error_code,
        )
        raise AppError(code, message=f"local runner reported {reply.error_code}")

    if not reply.image_base64:
        raise AppError("local_runtime.result_invalid", message="local runner returned no image")

    data, mime = decode_image(reply.image_base64)
    log.info(
        "local_runtime.image_received",
        request_id=str(request_id),
        provider=provider,
        bytes=len(data),
        mime=mime,
    )
    return LocalImage(
        data=data,
        mime_type=mime,
        model_id=reply.model_id or f"local-cli.{provider}",
        revised_prompt=reply.revised_prompt,
    )


async def _await_reply_watching_cancel(
    *,
    request_id: uuid.UUID,
    deadline: float,
    is_cancelled: Callable[[], Awaitable[bool]] | None,
    cancelled_error: str = "local_runtime.failed",
) -> transport.Reply | None:
    """分段等结果，段与段之间看一眼用户有没有取消。

    文本和出图共用它。分段还有一个与取消无关的理由：底层是 `BLPOP`，
    一次等满整个超时会把 `core/redis.py` 那个进程级共享池里的一条连接
    占住好几分钟，而 SSE、熔断器、幂等键都在用同一个池。

    `cancelled_error` 由调用方给：出图那条用 `local_runtime.*`（能修的人是
    用户自己），文本那条替代的是一次 Provider 调用，用 `provider.*`。
    """
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        slice_seconds = min(_WAIT_SLICE_SECONDS, max(1, int(remaining)))
        reply = await transport.await_reply(
            request_id=request_id, timeout_seconds=int(slice_seconds)
        )
        if reply is not None:
            return reply
        if is_cancelled is not None and await is_cancelled():
            # cleanup 由调用方的 finally 做，这里只负责结束等待。
            # 租约一没，连接器的下一次心跳就会收到 outstanding=false。
            raise AppError(cancelled_error, message="task was cancelled")


# ---------------------------------------------------------------- 图片字节


def sniff_image_mime(data: bytes) -> str | None:
    """按字节魔数判类型。认不出来返回 None。

    不按对端给的 `image_mime` 判：那是桌面进程说的，而这份字节马上要被
    写进对象存储、再被浏览器当图片加载。
    """
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            if mime == "image/webp":
                return "image/webp" if data[8:12] == b"WEBP" else None
            return mime
    return None


def decode_image(raw: str) -> tuple[bytes, str]:
    """严格解 base64，**并且真的把它解码成一张图**。任何一步不对就抛
    `local_runtime.result_invalid`。

    四道闸，一道比一道贵，所以按这个顺序：

    1. **长度**——在解码之前就挡掉超大的，别先分配几十兆再判断。
    2. **严格 base64**（`validate=True`）：宽松解码会把一段普通文字也解出
       "字节"来，然后我们把一坨垃圾当图片存进资产库。
    3. **字节魔数**：便宜的预筛，也顺便定下 MIME——扩展名和对端说的
       `image_mime` 都不作数。
    4. **真解码**（Pillow）：魔数只有几个字节，"PNG 头 + 一坨随机数据"
       能轻松骗过它。这一道要求像素真的解得出来、尺寸合理。
       解码前先看 `size`：宽高相乘就是要分配的内存，一张 100×100 的压缩包
       可以声称自己是 50000×50000（解压炸弹），必须在 `load()` 之前挡。
    """
    if len(raw) > (MAX_IMAGE_BYTES // 3 + 1) * 4:
        raise AppError("local_runtime.result_invalid", message="image exceeds size limit")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError("local_runtime.result_invalid", message="image is not valid base64") from exc
    if not data:
        raise AppError("local_runtime.result_invalid", message="image decoded to zero bytes")
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError("local_runtime.result_invalid", message="image exceeds size limit")
    mime = sniff_image_mime(data)
    if mime is None:
        raise AppError("local_runtime.result_invalid", message="image is not a recognised format")
    _assert_really_an_image(data, mime)
    return data, mime


def _assert_really_an_image(data: bytes, mime: str) -> None:
    """用 Pillow 真解一遍。解不出来、格式对不上、尺寸离谱，一律拒。

    `verify()` 只查结构（PNG 的 CRC 之类），**查不出全部损坏**；
    `load()` 才是真把像素解出来。verify 之后 Pillow 的文件对象不可再用，
    所以要重开一次——这不是冗余，是它的用法。
    """
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = (probe.format or "").upper()
            width, height = probe.size
            # 尺寸检查必须在 load 之前：宽高相乘就是即将分配的内存。
            if width < MIN_IMAGE_SIDE or height < MIN_IMAGE_SIDE:
                raise AppError(
                    "local_runtime.result_invalid",
                    message=f"image is too small ({width}x{height})",
                )
            if width * height > MAX_IMAGE_PIXELS:
                raise AppError(
                    "local_runtime.result_invalid",
                    message=f"image is too large ({width}x{height})",
                )
            probe.verify()
        # verify 过的对象已经不能再读像素，重开一次真解。
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except AppError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, DecompressionBombError) as exc:
        # Pillow 在文件截断/损坏时抛的是 OSError("image file is truncated") 这类，
        # 类型不统一，所以按一组来接。
        raise AppError(
            "local_runtime.result_invalid", message=f"image cannot be decoded: {type(exc).__name__}"
        ) from exc

    expected = _FORMAT_OF.get(fmt)
    if expected != mime:
        # 魔数说是 PNG，Pillow 解出来却是别的——这种不一致本身就是危险信号。
        raise AppError(
            "local_runtime.result_invalid",
            message=f"image format mismatch (magic={mime}, decoded={fmt or 'unknown'})",
        )


# ---------------------------------------------------------------- 端点侧


def _assert_provider(provider: str) -> None:
    """连接器自称的 provider 必须是本部署配的两个之一。

    跨 Provider 抢单会让用户拿到一个他没配过的模型的产出。
    """
    settings = get_settings()
    if provider not in (settings.local_cli_provider, settings.local_cli_image_provider):
        raise AppError("common.forbidden", message="provider mismatch")


def _allowed_kinds(provider: str, requested: list[str]) -> list[transport.RequestKind]:
    """这台连接器**被允许**取哪几种活。

    两侧取交集：连接器自报支持什么，配置又把哪一种能力指给了它。
    配置里文本走 claude 时，一台 codex 连接器就不该拿到文本请求——
    否则用户在设置里选的那个模型形同虚设。
    """
    settings = get_settings()
    allowed: list[transport.RequestKind] = []
    if "text" in requested and provider == settings.local_cli_provider:
        allowed.append("text")
    if "image" in requested and provider == settings.local_cli_image_provider:
        allowed.append("image")
    return allowed


async def take_request(
    *, provider: str, kinds: list[str], version: str | None = None
) -> transport.LeasedRequest | None:
    """连接器取单。顺带刷心跳。

    出图排在文本前面：出图是用户盯着屏幕等的那一个，文本那条链路上游还有
    别的兜底路径。
    """
    _assert_enabled()
    _assert_provider(provider)
    allowed = _allowed_kinds(provider, kinds)
    await transport.touch_heartbeat(provider, kinds=list(allowed), version=version)
    order: tuple[transport.RequestKind, ...] = ("image", "text")
    for kind in order:
        if kind not in allowed:
            continue
        leased = await transport.take(provider=provider, kind=kind)
        if leased is not None:
            return leased
    return None


async def heartbeat(
    *,
    provider: str,
    kinds: list[str],
    version: str | None = None,
    request_id: uuid.UUID | None = None,
) -> bool:
    """忙碌心跳。返回"你手上那条还有人等吗"。

    连接器跑一次生成要几分钟，这几分钟里它不能取新单，但心跳必须继续——
    否则服务端 20 秒后判定"没有 runner"，把同一时刻进来的下一条请求直接拒掉，
    而连接器其实好好地在干活。
    """
    _assert_enabled()
    _assert_provider(provider)
    allowed = _allowed_kinds(provider, kinds)
    await transport.touch_heartbeat(provider, kinds=list(allowed), version=version)
    if request_id is None:
        return True
    return await transport.is_outstanding(request_id)


async def submit_result(
    *,
    request_id: uuid.UUID,
    lease_token: str,
    text: str | None,
    image_base64: str | None,
    image_mime: str | None,
    revised_prompt: str | None,
    model_id: str | None,
    tokens_in: int | None,
    tokens_out: int | None,
    error_code: str | None,
) -> None:
    _assert_enabled()
    given = [x is not None for x in (text, image_base64, error_code)]
    if sum(1 for x in given if x) != 1:
        raise AppError(
            "common.validation_failed",
            message="exactly one of text / image_base64 / error_code is required",
        )
    if text is not None and not text.strip():
        raise AppError("common.validation_failed", message="text must be non-empty")

    reply_error = error_code
    if image_base64 is not None:
        # **落库之前就验**：不是一张认得出的图就不要放进邮箱。
        # 但仍然要把这条请求一次性完成掉——否则等待方会一直等到超时，
        # 用户多等三分钟才看到一个本来立刻就能给出的失败。
        try:
            decode_image(image_base64)
        except AppError:
            log.warning("local_runtime.image_rejected", request_id=str(request_id))
            await transport.complete(
                request_id=request_id,
                lease_token=lease_token,
                reply=transport.Reply(error_code="result_invalid"),
            )
            raise

    outcome = await transport.complete(
        request_id=request_id,
        lease_token=lease_token,
        reply=transport.Reply(
            text=text,
            model_id=model_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            error_code=reply_error,
            image_base64=image_base64,
            image_mime=image_mime,
            revised_prompt=revised_prompt,
        ),
    )
    if outcome == transport.COMPLETE_OK:
        return
    # 两种失败对外都是 409：区分它们只会告诉调用方「这个 id 存在过」。
    log.warning(
        "local_runtime.result_rejected",
        request_id=str(request_id),
        outcome=outcome,
    )
    raise AppError(
        "common.conflict",
        message="request is not outstanding (expired, cancelled or already completed)",
    )


async def describe_status(db: AsyncSession, *, org_id: uuid.UUID) -> StatusOut:
    """给界面看的状态。**只说本 org 的事。**

    别的 org 配了本机运行时，这里必须回 `enabled=false` 且列表为空——
    否则任何一个租户都能靠这个端点探出别人的项目 id。

    `image_available` 为 False 时必定带一句中文原因：界面要把它原样说给
    用户听。只把按钮变灰而不说为什么，用户会以为是产品坏了。
    """
    settings = get_settings()
    if not settings.local_cli_enabled or settings.local_cli_org_id != org_id:
        return StatusOut(
            enabled=False,
            text_provider=None,
            image_provider=None,
            project_ids=[],
            capabilities=Capabilities(),
            runner_connected=False,
            image_runner_connected=False,
            image_available=False,
            image_unavailable_reason="这个组织没有开启本机生成",
        )

    # 配置里写错一个 id（别的租户的项目）也不能回显出去，逐个核对归属。
    owned: list[uuid.UUID] = []
    for project_id in settings.local_cli_project_ids:
        if await project_service.project_exists(db, org_id=org_id, project_id=project_id):
            owned.append(project_id)

    text_state = await transport.runner_state(settings.local_cli_provider)
    image_state = (
        text_state
        if settings.local_cli_image_provider == settings.local_cli_provider
        else await transport.runner_state(settings.local_cli_image_provider)
    )
    text_ready = text_state is not None and "text" in text_state.kinds
    image_ready = image_state is not None and "image" in image_state.kinds

    reason: str | None = None
    if not owned:
        reason = "这个项目不在本机生成的白名单里"
    elif image_state is None:
        reason = "没有检测到本地连接器，请在你的电脑上启动它"
    elif not image_ready:
        reason = "本地连接器在线，但它没有报告出图能力（需要已登录的 Codex）"

    return StatusOut(
        enabled=True,
        text_provider=settings.local_cli_provider,
        image_provider=settings.local_cli_image_provider,
        project_ids=owned,
        capabilities=Capabilities(text=text_ready, image=image_ready),
        runner_connected=text_state is not None,
        image_runner_connected=image_state is not None,
        image_runner_version=image_state.version if image_state else None,
        image_available=bool(owned) and image_ready,
        image_unavailable_reason=reason,
    )


def _assert_enabled() -> None:
    if not get_settings().local_cli_enabled:
        # 404 而不是 403：关着的时候连「有这么个端点」都不该确认。
        raise AppError("common.not_found", message="local runtime is disabled")
