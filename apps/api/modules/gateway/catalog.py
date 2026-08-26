"""能力 → Provider → 适配器的**唯一一份**目录。

在此之前这份映射存在两处：`service._build()` 里"哪家提供哪个能力、有哪些
模型"，`probe.py` 里"这个能力该用哪家的 Key 去验证"。BYOK 接入 Gateway
之后（ADR-027）还要再来一份"用户配的这把 Key 该实例化哪个适配器"——
三份迟早对不上，表现是"设置页能配、真调用时说没有这家 Provider"。

所以目录本身抽到这里，`service.py` 与 `probe.py` 都从这里取。
这个模块**只描述有什么**，不做任何解析决策：路由排序、熔断、failover
在 `service.py`，Key 验证在 `probe.py`。

平台 Key 的读取也在这里，且用**显式 if** 而不是 `{id: lambda}` 表——
lambda 捕获的是变量不是值，这个仓库已经因此让 DeepSeek 拿着万相的 Key
去请求过一次。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adapters.providers.dashscope import DashScopeImageProvider
from adapters.providers.deepseek import DeepSeekProvider
from apps.api.core.config import get_settings


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """一家 Provider 在某个能力上的全部信息。

    `models` 是 (模型 id, 优先级) 的有序元组，数字大的先用。
    优先级写在这里而不是数据库：它表达的是"同样的活儿哪个模型更划算"，
    是代码层面的路由策略，不是价格——价格一律在 `model_pricing` 表里。
    """

    provider_id: str
    capability: str
    label: str
    adapter: Any  # Callable[..., TextProvider | ImageProvider]
    models: tuple[tuple[str, int], ...]


SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        provider_id="provider.deepseek",
        capability="text_generation",
        label="DeepSeek",
        adapter=DeepSeekProvider,
        # deepseek-chat 优先级更高：同样的结构化任务它只用 11 个
        # completion token，V4 要 60 个（含推理）。Router 分类、
        # schema 填充这类活儿不需要推理能力。
        models=(("deepseek-chat", 100), ("deepseek-v4-flash", 80)),
    ),
    ProviderSpec(
        provider_id="provider.dashscope",
        capability="image_generation",
        label="通义万相",
        adapter=DashScopeImageProvider,
        models=(("wan2.2-t2i-flash", 100), ("wan2.2-t2i-plus", 60)),
    ),
)


def provider_of() -> dict[str, str]:
    """能力 → 由哪家提供。

    一个能力目前只对一家：用户不需要在设置页选 Provider。多家并存要等
    "同能力多 Provider 的 BYOK"，那时这里返回的就不再是单值了。
    """
    return {spec.capability: spec.provider_id for spec in SPECS}


def labels() -> dict[str, str]:
    return {spec.provider_id: spec.label for spec in SPECS}


def spec_for(provider_id: str, *, capability: str | None = None) -> ProviderSpec | None:
    """按 provider_id 取目录项。

    给了 `capability` 就一并校验二者匹配——用户存下 Key 之后平台把某个
    能力改绑到别家，旧的凭证行会指向一个"能力对不上"的 Provider，
    这时候必须查不到，而不是拿它去调一个风马牛不相及的接口。
    """
    for spec in SPECS:
        if spec.provider_id == provider_id and capability in (None, spec.capability):
            return spec
    return None


def platform_key(provider_id: str) -> str:
    """平台自己的 Key。没配置时返回空串，由调用方决定怎么办。"""
    s = get_settings()
    if provider_id == "provider.deepseek":
        return s.deepseek_api_key.get_secret_value()
    if provider_id == "provider.dashscope":
        return s.dashscope_api_key.get_secret_value()
    return ""


# 推理模型。思考 token 计入输出预算，用在分类和结构化抽取上会返回
# **空内容且不报错**——这个仓库已经踩过两次，ADR-024 的第二条硬约束
# （`model_policy.no_reasoning_roles`）就是为它写的。
#
# 判定写成显式集合而不是按 id 前缀猜：`deepseek-v4-flash` 里没有任何
# 字符表明它会思考，靠 `"v4" in model_id` 这种规则迟早会误判下一个
# 命名风格不同的模型。
REASONING_MODELS: frozenset[str] = frozenset({"deepseek-v4-flash"})


def is_reasoning(model_id: str) -> bool:
    return model_id in REASONING_MODELS


# 模型的展示信息。**只讲模型本身的定位（快 / 推理强 / 细节多），不讲价格**：
# 价格在 `model_pricing` 表里，上游一调价，代码里的"更便宜"就变成了谎话。
#
# 放在目录里而不是前端，理由同 `credentials.CAPABILITY_LABELS`：有哪些模型
# 是后端定的，前端遇到没见过的 id 只能把裸 id 怼给用户。
MODEL_LABELS: dict[str, tuple[str, str]] = {
    "deepseek-chat": ("快速档", "响应快，适合分类、结构化抽取这类不需要推理的环节"),
    "deepseek-v4-flash": ("推理档", "带思考过程，复杂改写更稳；分类与结构化抽取环节会自动回退"),
    "wan2.2-t2i-flash": ("快速档", "出图快，适合大批量分镜草图"),
    "wan2.2-t2i-plus": ("精细档", "细节与材质更丰富，单张耗时更长"),
}


def spec_for_capability(capability: str) -> ProviderSpec | None:
    for spec in SPECS:
        if spec.capability == capability:
            return spec
    return None


def model_ids(capability: str) -> tuple[str, ...]:
    """这个能力下所有可选的模型 id，按优先级排好。

    "用户能选哪些模型"和"Gateway 会试哪些模型"必须是同一份列表——
    分家的下场是设置页存下一个路由表里根本没有的 id，
    偏好静默失效而没有任何人知道。
    """
    spec = spec_for_capability(capability)
    return tuple(model for model, _ in spec.models) if spec else ()
