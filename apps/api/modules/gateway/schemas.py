"""模型目录的对外 DTO。

**不把 `catalog.SPECS` 直接吐出去**：那里面挂着适配器类和路由优先级，
前者根本不可序列化，后者是内部路由策略——今天把 `deepseek-chat` 的优先级
从 100 调到 90 是一次纯粹的内部调整，不该变成一次前端可见的 API 变更。

对外只承诺三件事：这个能力有没有接、能选哪几个模型、不选时默认走哪个。
"""

from __future__ import annotations

import uuid
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
    #: 推理模型（思考 token 计入输出预算）。`model_policy.no_reasoning_roles` 的环节不能用它，
    #: 解析到它时报 `provider.byok.rejected`（`reasoning_model_not_allowed`）。
    reasoning: bool = False


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

    `kind="catalog"` 是目录里真有适配器的一家；`kind="org"` 是本组织自带 Key 的
    一个供应商连接（ADR-039），`provider_id` 是 `provider.org:<连接 id>`，模型只列
    这个能力的协议下的那几个。连接停用时 `available=False`，界面不能把它画成可选。
    """

    provider_id: str
    label: str
    kind: Literal["catalog", "org"]
    available: bool
    models: list[ModelOptionOut]
    default_model_id: str | None
    #: 这家能不能用"平台额度"计费。组织连接永远不能。
    supports_platform_key: bool
    unavailable_reason: str | None = None
    #: 组织连接的 id；目录里的一家为 null
    connection_id: uuid.UUID | None = None
    #: 出图一致性实测过没有（ADR-039 第 7 条）。组织连接的出图选项一律 false，界面标
    #: "未实测"；文本选项与平台目录（万相）为 null——不在这个字段上表态。
    consistency_verified: bool | None = None


class CredentialStatusOut(BaseModel):
    """这家有没有存自己的 Key。**只有尾号**，完整 Key 永远不出服务端。"""

    provider_id: str
    configured: bool
    masked_key: str | None
    updated_at: datetime | None


class SelectionOut(BaseModel):
    provider_id: str | None
    model_id: str | None
    key_source: Literal["platform", "org"]
    #: `org` = 组织显式存过；`platform` = 没存过，下面的值是平台目录默认
    layer: Literal["org", "platform"]
    updated_at: datetime | None
    #: 指向的组织连接已不可用时的原因码：`connection_missing` / `connection_disabled` /
    #: `capability_mismatch` / `model_missing`。正常为 null。默认不会被自动改掉，
    #: 生成时会报可读错误，界面据此提示用户重新选择。
    broken_reason: str | None = None


class CapabilityConfigOut(BaseModel):
    capability: str
    label: str
    available: bool
    #: 这个能力能不能在模型页选上游（Skill 的 `user_selectable` ∩ 已接入）
    configurable: bool
    providers: list[ProviderOptionOut]
    selection: SelectionOut | None
    credentials: list[CredentialStatusOut]
    #: 这个能力能不能指向自带 Key 的供应商连接（由协议白名单推出）
    supports_org_connections: bool = False
    unavailable_reason: str | None = None


class ModelConfigOut(BaseModel):
    items: list[CapabilityConfigOut]


class SelectionIn(BaseModel):
    #: 目录里的一家，或 `provider.org:<连接 id>`
    provider_id: str = Field(min_length=1, max_length=64)
    #: 空表示"这家的目录默认顺序"
    model_id: str | None = Field(default=None, max_length=128)
    key_source: Literal["platform", "org"]


# ---------------------------------------------------------------- 供应商连接（ADR-039）


class ProtocolOut(BaseModel):
    """协议白名单的一项：协议 → 唯一能力。"""

    protocol: str
    capability: str
    label: str
    #: 出图协议实测过一致性没有（ADR-039 第 7 条）。`openai_images` 一律 false（界面标
    #: "未实测"）；文本协议不出图，为 null。
    consistency_verified: bool | None = None


class PresetModelOut(BaseModel):
    model_id: str
    protocol: str
    capability: str
    #: 推理模型（思考 token 计入输出预算）。`model_policy.no_reasoning_roles` 的环节不能用它，
    #: 解析到它时报 `provider.byok.rejected`（`reasoning_model_not_allowed`）。
    reasoning: bool = False
    #: 出图协议实测过一致性没有（ADR-039 第 7 条）。`openai_images` 一律 false（界面标
    #: "未实测"）；文本协议不出图，为 null。
    consistency_verified: bool | None = None


class PresetOut(BaseModel):
    """一个预设。**没有价格**：自带 Key 的调用一律按平台服务费计（`byok_unit_credits`）。"""

    preset_id: str
    label: str
    #: 空串 = 地址由用户自己填（自定义中转）
    base_url: str
    docs_url: str
    key_url: str | None
    icon: str
    protocols: list[str]
    capabilities: list[str]
    models: list[PresetModelOut]


class PresetListOut(BaseModel):
    presets: list[PresetOut]
    protocols: list[ProtocolOut]


class ConnectionModelIn(BaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    protocol: str = Field(min_length=1, max_length=32)
    #: 用户勾选"这是推理模型"。缺省 false
    reasoning: bool = False


class ConnectionModelOut(BaseModel):
    model_id: str
    protocol: str
    capability: str
    #: 推理模型（思考 token 计入输出预算）。`model_policy.no_reasoning_roles` 的环节不能用它，
    #: 解析到它时报 `provider.byok.rejected`（`reasoning_model_not_allowed`）。
    reasoning: bool = False
    #: 出图协议实测过一致性没有（ADR-039 第 7 条）。`openai_images` 一律 false（界面标
    #: "未实测"）；文本协议不出图，为 null。
    consistency_verified: bool | None = None


class ConnectionCreateIn(BaseModel):
    """新建连接。给了 `preset_id` 时，没给的 `label` / `base_url` / `models` 从预设拷。"""

    preset_id: str | None = Field(default=None, max_length=64)
    label: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, max_length=512)
    models: list[ConnectionModelIn] | None = Field(default=None, max_length=50)
    api_key: str = Field(min_length=8, max_length=512)
    enabled: bool = True


class ConnectionUpdateIn(BaseModel):
    """全部可选，没给的不动。`api_key` 不给就沿用原来那把。"""

    label: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, max_length=512)
    models: list[ConnectionModelIn] | None = Field(default=None, max_length=50)
    enabled: bool | None = None
    api_key: str | None = Field(default=None, min_length=8, max_length=512)


class ConnectionOut(BaseModel):
    """一个连接的展示态。**只有 Key 尾号**，明文永远不出服务端。"""

    id: uuid.UUID
    provider_id: str
    label: str
    preset_id: str | None
    base_url: str
    models: list[ConnectionModelOut]
    enabled: bool
    masked_key: str | None
    created_at: datetime
    updated_at: datetime


class ConnectionListOut(BaseModel):
    items: list[ConnectionOut]
    #: 每个组织最多几个连接（配置项 `org_provider_connection_limit`）
    limit: int


class ConnectionTestIn(BaseModel):
    """测试一个已保存的连接。全部可选：没给的用已保存的值（可用来测"改了还没存"的地址 / Key）。"""

    protocol: str | None = Field(default=None, max_length=32)
    model_id: str | None = Field(default=None, max_length=128)
    base_url: str | None = Field(default=None, max_length=512)
    api_key: str | None = Field(default=None, min_length=8, max_length=512)


class ConnectionDraftTestIn(BaseModel):
    """保存之前测试：参数要给全。"""

    protocol: str = Field(min_length=1, max_length=32)
    model_id: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=512)
    api_key: str = Field(min_length=8, max_length=512)
    preset_id: str | None = Field(default=None, max_length=64)


class ConnectionDefaultRefOut(BaseModel):
    capability: str


class ConnectionProjectRefOut(BaseModel):
    project_id: uuid.UUID
    name: str
    capability: str


class ConnectionReferencesOut(BaseModel):
    """谁正指着这个连接。删除连接不会自动改掉这些引用，删除确认框据此提示。"""

    defaults: list[ConnectionDefaultRefOut]
    projects: list[ConnectionProjectRefOut]
