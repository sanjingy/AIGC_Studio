"""Provider 统一接口（05_ModelRuntimeSpec.md §5）。

业务层只声明"要什么能力"，由 Gateway 决定用哪家（ADR-002）。
所以这里的接口必须是能力粒度的，不能泄露任何厂商特有的概念。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from apps.api.core.logging import get_logger

log = get_logger(__name__)


class KeySource(StrEnum):
    """这次调用用的是谁的 Key（ADR-027）。

    **只有来源这个枚举可以外传**——不是 Key、不是尾号、不是密文。
    有了它才能验证"配了自有 Key 的用户确实在用自己的 Key"这件事，
    而不是只能看计费数字倒推：计费走的是"库里有没有这一行"，
    真实调用走的是"解密出来的那把 Key"，这两条路径完全可能背离，
    而背离的方向恰好是平台掏钱、用户按折扣价付款。
    """

    PLATFORM = "platform"  # 平台自己的 Key（环境变量）
    ORG = "org"  # 该 org 自己配置的 Key（provider_credentials）


def signal_key_source(*, provider_id: str, model_id: str, key_source: KeySource) -> None:
    """在真正发出上游请求的那一层记下 Key 的来源。

    记在适配器里而不是 Gateway 里：Gateway 记的是"我打算用哪把"，
    适配器记的是"实际拿着哪把去请求"——中间任何一次传参错位
    （这个仓库出过一次：DeepSeek 拿着万相的 Key）都只有后者能发现。
    """
    log.info(
        "provider.call",
        provider_id=provider_id,
        model_id=model_id,
        key_source=key_source.value,
    )


@dataclass(frozen=True, slots=True)
class TextRequest:
    system: str
    user: str
    # 要求 JSON 输出。结构化输出是硬要求（03_AgentSystem.md §2）
    json_mode: bool = True
    max_output_tokens: int = 8192
    temperature: float = 0.7


@dataclass(frozen=True, slots=True)
class TextResponse:
    text: str
    tokens_in: int
    tokens_out: int
    model_id: str
    # 推理模型会先产出思考 token，且计入输出预算。
    # 单独记下来，否则排查"为什么输出是空的"会毫无头绪。
    reasoning_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ImageRequest:
    prompt: str
    negative_prompt: str = ""
    size: str = "1024*1024"
    n: int = 1
    seed: int | None = None


@dataclass(frozen=True, slots=True)
class ImageResult:
    urls: list[str]
    model_id: str
    # 上游实际使用的提示词。DashScope 默认会重写提示词，
    # 不记下来的话根本不知道画出来的东西是按什么描述生成的。
    actual_prompts: list[str] = field(default_factory=list)


class TextProvider(Protocol):
    provider_id: str
    model_id: str
    key_source: KeySource

    async def generate_text(self, request: TextRequest) -> TextResponse: ...


class ImageProvider(Protocol):
    provider_id: str
    model_id: str
    key_source: KeySource

    async def generate_image(self, request: ImageRequest) -> ImageResult: ...


def usage_of(payload: dict[str, Any]) -> tuple[int, int, int]:
    """从 OpenAI 兼容响应里取用量。缺字段时返回 0，不抛异常——
    用量统计不该让一次成功的生成失败。"""
    usage = payload.get("usage") or {}
    detail = usage.get("completion_tokens_details") or {}
    return (
        int(usage.get("prompt_tokens", 0)),
        int(usage.get("completion_tokens", 0)),
        int(detail.get("reasoning_tokens", 0)),
    )


class KeyVerifier(Protocol):
    """能验证一把 Key 是否可用的 Provider（ADR-025 的"测试连接"）。

    验证必须走**不产生生成费用**的端点：用户点一下"测试连接"就烧一张图，
    这个按钮就没人敢点了。返回一句可直接展示给用户的结论，
    失败则抛 :class:`AppError`，由 Gateway 统一脱敏后转成探测结果。
    """

    provider_id: str

    async def verify_key(self) -> str: ...
