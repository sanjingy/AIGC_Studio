"""Provider 统一接口（05_ModelRuntimeSpec.md §5）。

业务层只声明"要什么能力"，由 Gateway 决定用哪家（ADR-002）。
所以这里的接口必须是能力粒度的，不能泄露任何厂商特有的概念。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


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

    async def generate_text(self, request: TextRequest) -> TextResponse: ...


class ImageProvider(Protocol):
    provider_id: str
    model_id: str

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
