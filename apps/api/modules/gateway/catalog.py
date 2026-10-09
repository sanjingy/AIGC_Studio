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

import uuid
from dataclasses import dataclass
from typing import Any

from adapters.providers.dashscope import DashScopeImageProvider
from adapters.providers.deepseek import DeepSeekProvider
from adapters.providers.openai_compat import OpenAICompatTextProvider
from adapters.providers.openai_images import OpenAIImagesProvider
from adapters.providers.openai_responses import OpenAIResponsesTextProvider
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


# 组织自带 Key 的供应商连接（ADR-039，取代 ADR-031 第 5 条的"一个文本自定义端点"）。
#
# **连接不进 `SPECS`**：`SPECS` 是进程级常量，连接是每个 org 多行、随时会改的数据
# （`org_provider_connections`）。每个连接在运行期以一条虚拟路由出现：
# `provider_id = "provider.org:<连接 id>"`。组织默认、项目偏好、Gateway 路由三处
# 都用这同一个串指代它。
ORG_PROVIDER_PREFIX = "provider.org:"
ORG_PROVIDER_LABEL = "自带 Key 的供应商"

# 迁移前文本自定义端点的固定 id。迁移把它全部改写成 `provider.org:<id>`；
# 只有"端点早已删除"的旧项目偏好还会留着它，解析时按"连接不存在"报错。
LEGACY_CUSTOM_TEXT_PROVIDER_ID = "provider.custom.text"

# 本机会员 CLI（ADR-041）。**只**作为项目的文本偏好出现：`provider.local:<claude|codex>`。
# 它不进路由表、不进组织默认，也**不经过 Gateway**——由 `agent/llm.py::RoutingLLM`
# 在 Gateway 之前接走。这里只登记写法，让 Gateway 认得它并拒绝，而不是把它当成
# 一条过期偏好、悄悄落到平台或用户自己的付费 Key 上。
LOCAL_PROVIDER_PREFIX = "provider.local:"
LOCAL_CAPABILITY = "text_generation"
#: 本机 CLI 跑出来的 `agent_runs.model_id` 一律以它开头（`local-cli.claude[:<自报模型>]`）。
#: 计费靠它认出"这次是用户自己的会员额度"，不靠模型名猜。
LOCAL_MODEL_PREFIX = "local-cli."


def is_local_model(model_id: str | None) -> bool:
    return bool(model_id) and str(model_id).startswith(LOCAL_MODEL_PREFIX)


def local_ref(provider: str) -> str:
    return f"{LOCAL_PROVIDER_PREFIX}{provider}"


def parse_local_ref(value: str | None) -> str | None:
    """`provider.local:<名字>` → 名字。前缀不对返回 None。

    名字**不在这里校验**：前缀对、名字认不出（`provider.local:gemini`）照样返回原串，
    调用方按"选了本机但这个 CLI 不可用"报错，不当成"不是本机"往付费路由上落。
    """
    if not value or not value.startswith(LOCAL_PROVIDER_PREFIX):
        return None
    return value[len(LOCAL_PROVIDER_PREFIX) :]


@dataclass(frozen=True, slots=True)
class ProtocolSpec:
    """一个协议绑定**唯一**的能力与**唯一**的适配器（ADR-039 第 2 条）。

    能力由协议推出，连接表里不存 capability：没有这一列，就没有人能往里存
    `"tts"`。端点自己声明什么都不采信，只按这里写死的契约调。
    """

    protocol: str
    capability: str
    adapter: Any  # Callable[..., TextProvider | ImageProvider]
    label: str
    #: 这个协议出的图有没有做过一致性实测（ADR-039 第 7 条）。只对出图协议有意义：
    #: 文本协议为 None。实测前一律 False，界面据此标"未实测"，不得宣称与万相同等一致。
    #: 第二批专用协议附上实测记录后才能改成 True。
    consistency_verified: bool | None = None


PROTOCOLS: dict[str, ProtocolSpec] = {
    "openai_chat": ProtocolSpec(
        protocol="openai_chat",
        capability="text_generation",
        adapter=OpenAICompatTextProvider,
        label="OpenAI 兼容对话（/chat/completions）",
    ),
    # 2026-10-09 ADR-039 后续决定：Codex 系模型只挂在 Responses 上。仍是文本能力，
    # 与 `openai_chat` 并列由用户显式选择；`/models` 不说明模型走哪个接口，不自动判定。
    "openai_responses": ProtocolSpec(
        protocol="openai_responses",
        capability="text_generation",
        adapter=OpenAIResponsesTextProvider,
        label="OpenAI Responses（/responses）",
    ),
    "openai_images": ProtocolSpec(
        protocol="openai_images",
        capability="image_generation",
        adapter=OpenAIImagesProvider,
        label="OpenAI 兼容出图（/images/generations）",
        consistency_verified=False,
    ),
}


#: 地址按 OpenAI 规矩规整（剥完整请求地址后缀、只填主机补 `/v1`）的协议。
#: 第二批专用出图协议的地址形状各不相同，不在此列。
OPENAI_BASE_PROTOCOLS: frozenset[str] = frozenset(
    {"openai_chat", "openai_responses", "openai_images"}
)


def protocol_spec(protocol: str) -> ProtocolSpec | None:
    return PROTOCOLS.get(protocol)


def consistency_verified(protocol: str) -> bool | None:
    """按协议给出"一致性实测过没有"。白名单外的协议与文本协议都是 None。"""
    spec = PROTOCOLS.get(protocol)
    return spec.consistency_verified if spec else None


def protocols_for(capability: str) -> tuple[str, ...]:
    return tuple(p.protocol for p in PROTOCOLS.values() if p.capability == capability)


def supports_org_connections(capability: str) -> bool:
    """这个能力能不能指向组织自带的连接。由协议白名单推出，不另写一份。"""
    return bool(protocols_for(capability))


def org_provider_id(connection_id: uuid.UUID) -> str:
    return f"{ORG_PROVIDER_PREFIX}{connection_id}"


def is_org_provider(provider_id: str | None) -> bool:
    return bool(provider_id) and str(provider_id).startswith(ORG_PROVIDER_PREFIX)


_UUID_CHARS = 36


def parse_org_ref(value: str) -> tuple[uuid.UUID | None, str | None] | None:
    """解析 `provider.org:<uuid>[:<model_id>]`。不是这个前缀返回 None。

    uuid 定长 36 位，所以模型 id 里出现 `/`、`:`（OpenRouter、硅基流动的模型名
    都有）也不会切错。前缀对、uuid 坏了返回 `(None, None)`——调用方按
    "连接不存在"报错，不当成"不是连接"往平台默认上落。旧的
    `provider.custom.text` 也走这一支。
    """
    if value == LEGACY_CUSTOM_TEXT_PROVIDER_ID:
        return None, None
    if not value.startswith(ORG_PROVIDER_PREFIX):
        return None
    rest = value[len(ORG_PROVIDER_PREFIX) :]
    try:
        connection_id = uuid.UUID(rest[:_UUID_CHARS])
    except ValueError:
        return None, None
    tail = rest[_UUID_CHARS:]
    if not tail:
        return connection_id, None
    if not tail.startswith(":") or len(tail) == 1:
        return None, None
    return connection_id, tail[1:]


def providers_for(capability: str) -> tuple[ProviderSpec, ...]:
    """这个能力下所有**真有适配器**的 Provider，按 `SPECS` 里的书写顺序。

    同一能力允许多家并存：设置页按这份列表给用户选上游，Gateway 按同一份
    列表建路由。第一家是"组织和项目都没选时"的平台默认。
    """
    return tuple(spec for spec in SPECS if spec.capability == capability)


def default_provider(capability: str) -> ProviderSpec | None:
    specs = providers_for(capability)
    return specs[0] if specs else None


def provider_of() -> dict[str, str]:
    """能力 → **平台默认**由哪家提供。

    同能力多 Provider 之后这不再是"唯一的那一家"，只是"没人选时用哪家"。
    `configurable_capabilities` 与旧的单 Provider 凭证接口仍然按它取默认值。
    """
    out: dict[str, str] = {}
    for spec in SPECS:
        out.setdefault(spec.capability, spec.provider_id)
    return out


def provider_label(provider_id: str) -> str:
    if is_org_provider(provider_id) or provider_id == LEGACY_CUSTOM_TEXT_PROVIDER_ID:
        return ORG_PROVIDER_LABEL
    return labels().get(provider_id, provider_id)


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
    """平台默认那一家。保留这个名字给只关心"默认"的旧调用方。"""
    return default_provider(capability)


def model_ids(capability: str, provider_id: str | None = None) -> tuple[str, ...]:
    """这个能力下所有可选的模型 id，按 Provider 顺序、再按优先级排好。

    "用户能选哪些模型"和"Gateway 会试哪些模型"必须是同一份列表——
    分家的下场是设置页存下一个路由表里根本没有的 id，
    偏好静默失效而没有任何人知道。

    给了 `provider_id` 就只列那一家的：组织默认要校验"这个模型属于你选的
    这家"，不能拿 A 家的模型名配 B 家的 Key。
    """
    out: list[str] = []
    for spec in providers_for(capability):
        if provider_id is not None and spec.provider_id != provider_id:
            continue
        out.extend(model for model, _ in spec.models)
    return tuple(out)


def provider_for_model(capability: str, model_id: str) -> ProviderSpec | None:
    """这个模型 id 属于哪一家。旧的项目偏好只存了模型 id，靠它反查 Provider。

    目录里同一能力下的模型 id 不允许重名（`test_catalog_model_ids_unique`），
    否则这里的答案就不唯一了。
    """
    for spec in providers_for(capability):
        if any(model == model_id for model, _ in spec.models):
            return spec
    return None
