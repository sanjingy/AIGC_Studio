"""本机运行时的请求/响应形状。

四个端点两套鉴权：poll / heartbeat / result 用桥接令牌（只授权这三条路径），
status 用正常的登录态。形状分开写在这里，router 只负责接线。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

#: 服务端认的 Provider。**没有 "auto"、没有通配**：跨 Provider 抢单会让
#: 一条本该由 Codex 跑的请求被 Claude 拿走，用户看到的模型与他配的不一致。
ProviderName = Literal["codex", "claude"]

#: 一条请求要什么能力。与 `transport.RequestKind` 同一组取值。
RequestKind = Literal["text", "image"]

#: 连接器能回报的失败原因。**有界枚举**——不接受自由文本，
#: 更不接受把子进程 stderr 原样回传：那里面会有本地路径、用户名，
#: 极端情况下还有登录提示里的账号。
ErrorCode = Literal[
    "timeout",  # 本机 CLI 超时，连接器已杀掉进程树
    "auth_required",  # CLI 未登录 / 登录过期
    "rate_limited",  # 订阅额度或速率限制（Codex 的 usageLimitExceeded 归这里）
    "no_image",  # 这一轮跑完了但模型没画图。协议层没有"生成一张图"的方法，
    # 出图是模型自己决定调 image_gen 的产物，所以这不是异常而是常态，单独成类
    "cli_error",  # CLI 非零退出或输出无法解析
    "unsupported",  # 这台连接器跑不了这种请求（例如 claude 拿到了出图）
    "cancelled",  # 连接器被 Ctrl-C 停掉，或用户在网页上取消了任务
]


def _default_kinds() -> list[RequestKind]:
    """默认只报文本。

    写成具名函数而不是 lambda：`Field(default_factory=...)` 会拿它的返回
    类型去对齐字段类型，lambda 推出来的是 `list[str]`，与 `list[RequestKind]`
    对不上（mypy strict 会拦）。
    """
    return ["text"]


MAX_RESULT_CHARS = 262_144

#: 回传图片的 base64 上限。解码后约 12 MiB —— 1024×1024 的 PNG 通常
#: 1~3 MiB，给到 12 已经很宽；不设上限等于允许一次请求把 API 进程的
#: 内存打满，而这条路径的调用方是用户桌面上的进程。
MAX_IMAGE_BASE64_CHARS = 16 * 1024 * 1024


class PollIn(BaseModel):
    """连接器的取单请求。

    `kinds` 是这台连接器**真实**支持的能力，由它自己按本机装了什么、
    版本支不支持来报。服务端据此决定给不给它出图请求，也据此在界面上
    显示"本机能不能出图"——写死在服务端就成了一个假入口。
    """

    provider: ProviderName
    kinds: list[RequestKind] = Field(default_factory=_default_kinds, max_length=4)
    #: 可执行文件版本，只用于展示与排障。不接受长文本。
    version: Annotated[str, StringConstraints(max_length=120)] | None = None


class TextJobOut(BaseModel):
    system: str
    user: str
    schema_name: str
    max_output_tokens: int


class ImageJobOut(BaseModel):
    """出图请求。**提示词由服务端合成好了**，连接器不加也不改。

    风格锁定在 `consistency.compose` 那一层（17_ConsistencyEngine §4），
    让桌面进程再拼一次等于把画风控制权交给一个我们看不见的地方。
    """

    prompt: str
    negative_prompt: str
    size: str
    n: int


class PollOut(BaseModel):
    request_id: uuid.UUID
    #: 一次性凭据。回结果时必须带上，服务端比对后原子作废。
    lease_token: str
    kind: RequestKind
    #: **剩余**秒数（绝对截止时间减去现在），不是原始配置值。
    timeout_seconds: int
    text: TextJobOut | None = None
    image: ImageJobOut | None = None


class HeartbeatIn(BaseModel):
    """忙碌时的心跳。

    单独一个端点，不是 poll 上的一个 `busy` 标志：这两件事的返回值
    根本不同（一个可能带回一条要干的活，一个只回答"你手上那条还要不要"），
    挤在一个端点上会逼出一个"两种含义共用一个 200"的形状。

    带上 `request_id` 时，服务端顺便回答**它还在不在**——用户在网页上
    取消了任务、或者等待方已经超时的话，租约就没了，连接器据此立刻停手，
    不再继续烧订阅额度。
    """

    provider: ProviderName
    kinds: list[RequestKind] = Field(default_factory=_default_kinds, max_length=4)
    version: Annotated[str, StringConstraints(max_length=120)] | None = None
    request_id: uuid.UUID | None = None


class HeartbeatOut(BaseModel):
    #: 没带 request_id 时恒为 True（没什么可判断的）。
    outstanding: bool


class ResultIn(BaseModel):
    """一条结果。文本、图片、错误**三选一**。"""

    lease_token: str = Field(min_length=8, max_length=256)
    # 约束写在 Annotated 里而不是 Field(max_length=...)：字段是 `str | None`，
    # 把长度约束挂在联合类型上，不同 pydantic 版本的处理并不一致。
    text: Annotated[str, StringConstraints(max_length=MAX_RESULT_CHARS)] | None = None
    image_base64: Annotated[str, StringConstraints(max_length=MAX_IMAGE_BASE64_CHARS)] | None = None
    image_mime: Annotated[str, StringConstraints(max_length=64)] | None = None
    revised_prompt: Annotated[str, StringConstraints(max_length=8000)] | None = None
    model_id: Annotated[str, StringConstraints(max_length=200)] | None = None
    tokens_in: int | None = Field(default=None, ge=0, le=100_000_000)
    tokens_out: int | None = Field(default=None, ge=0, le=100_000_000)
    error_code: ErrorCode | None = None


class Capabilities(BaseModel):
    """这台连接器**现在**能干什么。

    不是产品愿景，是心跳里自报的实况：`text` 看有没有文本连接器在线，
    `image` 看出图那台在不在、支不支持。视频和语音恒为 False——
    本机三个 CLI 里没有一个有订阅制的视频/语音生成能力，写 True 就是
    摆了一个点下去必然失败的入口。
    """

    text: bool = False
    image: bool = False
    video: bool = False
    audio: bool = False


class StatusOut(BaseModel):
    #: 本 org 是否配了本机运行时。别的 org 配了，这里一律 False。
    enabled: bool
    #: 文本走哪个 CLI；图片走哪个 CLI。可以不是同一个。
    text_provider: ProviderName | None
    image_provider: ProviderName | None
    #: 配置里属于本 org 的项目。不属于的不返回，避免跨租户探测。
    project_ids: list[uuid.UUID]
    capabilities: Capabilities
    #: 桌面连接器最近是否还在心跳（文本那台 / 出图那台）。
    runner_connected: bool
    image_runner_connected: bool
    #: 连接器自报的版本，排障用。没连上时为 None。
    image_runner_version: str | None = None
    #: 现在能不能选"本机出图"。为 False 时 `image_unavailable_reason`
    #: 一定有值——界面要把原因原样说给用户看，不能只把按钮变灰。
    image_available: bool = False
    image_unavailable_reason: str | None = None
    #: 恒为 True：这是试点，界面必须如实标注，不能让用户以为它是正式能力。
    pilot: bool = True
