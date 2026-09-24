"""模型目录的对外 DTO。

**不把 `catalog.SPECS` 直接吐出去**：那里面挂着适配器类和路由优先级，
前者根本不可序列化，后者是内部路由策略——今天把 `deepseek-chat` 的优先级
从 100 调到 90 是一次纯粹的内部调整，不该变成一次前端可见的 API 变更。

对外只承诺三件事：这个能力有没有接、能选哪几个模型、不选时默认走哪个。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ModelOptionOut(BaseModel):
    """一个可选模型。

    `label` / `note` 只讲模型本身的定位（快、推理强、细节多），
    **不讲价格也不讲"更省钱"**：价格在 `model_pricing` 表里，上游一调价，
    代码里冻着的那句结论就变成了谎话（DeepSeek 2026-08-17 涨过 350%）。
    """

    model_id: str
    label: str
    note: str


class CapabilityModelsOut(BaseModel):
    capability: str
    label: str
    #: 平台真的有这家 Provider 的适配器。False 时 `models` 必然是空的，
    #: 界面该照实说"未接入"，而不是给一个选了不生效的下拉框。
    available: bool
    provider_id: str | None
    provider_label: str | None
    #: Skill 的 `model_policy.user_selectable` 允不允许用户改这个能力（ADR-024）。
    user_selectable: bool
    models: list[ModelOptionOut]
    #: 项目没有设置偏好时 Gateway 会先试的那个。前端用它标注"默认"。
    default_model_id: str | None
    #: `available=False` 时为什么。由后端给，前端不要自己编文案。
    unavailable_reason: str | None


class ModelCatalogOut(BaseModel):
    items: list[CapabilityModelsOut]


class ProviderOptionOut(BaseModel):
    """某个能力下可选的一家上游。

    `kind="catalog"` 是目录里真有适配器的一家；`kind="custom"` 是文本能力的
    OpenAI 兼容自定义端点——它的模型不是目录给的，是组织自己填的那一个，
    所以没配置时 `models` 为空、`available=False`，界面不能把它画成可选。
    """

    provider_id: str
    label: str
    kind: Literal["catalog", "custom"]
    available: bool
    models: list[ModelOptionOut]
    default_model_id: str | None
    #: 这家能不能用"平台额度"计费。自定义端点永远不能。
    supports_platform_key: bool
    unavailable_reason: str | None = None


class CredentialStatusOut(BaseModel):
    """这家有没有存自己的 Key。**只有尾号**，完整 Key 永远不出服务端。"""

    provider_id: str
    configured: bool
    masked_key: str | None
    updated_at: datetime | None


class EndpointOut(BaseModel):
    label: str
    base_url: str
    model_id: str
    masked_key: str | None
    updated_at: datetime


class SelectionOut(BaseModel):
    provider_id: str | None
    model_id: str | None
    key_source: Literal["platform", "org"]
    #: `org` = 组织显式存过；`platform` = 没存过，下面的值是平台目录默认
    layer: Literal["org", "platform"]
    updated_at: datetime | None


class CapabilityConfigOut(BaseModel):
    capability: str
    label: str
    available: bool
    #: 这个能力能不能在模型页选上游（Skill 的 `user_selectable` ∩ 已接入）
    configurable: bool
    providers: list[ProviderOptionOut]
    selection: SelectionOut | None
    credentials: list[CredentialStatusOut]
    #: 只有文本能力有这一项；未配置为 null
    custom_endpoint: EndpointOut | None = None
    supports_custom_endpoint: bool = False
    unavailable_reason: str | None = None


class ModelConfigOut(BaseModel):
    items: list[CapabilityConfigOut]


class SelectionIn(BaseModel):
    provider_id: str = Field(min_length=1, max_length=32)
    #: 空表示"这家的目录默认顺序"
    model_id: str | None = Field(default=None, max_length=128)
    key_source: Literal["platform", "org"]


class EndpointIn(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=512)
    model_id: str = Field(min_length=1, max_length=128)
    #: 更新已有端点时可以不填，沿用原来那把
    api_key: str | None = Field(default=None, min_length=8, max_length=512)


class EndpointTestIn(BaseModel):
    """全部可选：没给的用已保存的值。"""

    base_url: str | None = Field(default=None, max_length=512)
    model_id: str | None = Field(default=None, max_length=128)
    api_key: str | None = Field(default=None, min_length=8, max_length=512)
