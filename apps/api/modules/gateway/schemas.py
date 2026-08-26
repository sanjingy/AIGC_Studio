"""模型目录的对外 DTO。

**不把 `catalog.SPECS` 直接吐出去**：那里面挂着适配器类和路由优先级，
前者根本不可序列化，后者是内部路由策略——今天把 `deepseek-chat` 的优先级
从 100 调到 90 是一次纯粹的内部调整，不该变成一次前端可见的 API 变更。

对外只承诺三件事：这个能力有没有接、能选哪几个模型、不选时默认走哪个。
"""

from __future__ import annotations

from pydantic import BaseModel


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
