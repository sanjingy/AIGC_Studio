from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BalanceOut(BaseModel):
    balance: int
    reserved: int
    total: int


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    amount: int
    reserved_delta: int
    balance_after: int
    reserved_after: int
    is_bonus: bool
    ref_type: str | None
    ref_id: uuid.UUID | None
    note: str | None
    created_at: datetime


class TopupIn(BaseModel):
    """S5 只做记账。真实支付通道在 M3 接入。"""

    principal: int = Field(gt=0, le=1_000_000)
    bonus: int = Field(default=0, ge=0, le=1_000_000)


class EstimateIn(BaseModel):
    type: str
    input: dict[str, object] = Field(default_factory=dict)


class EstimateOut(BaseModel):
    estimated_credits: int
    # Router 对外给区间，见 19_UnitEconomics.md §6
    range_low: int
    range_high: int


# ---------------------------------------------------------------- BYOK（ADR-025）


class ProviderCredentialOut(BaseModel):
    """一个能力的配置状态。

    **这里出现的每个字段都会到浏览器里**，所以只放识别用的尾号，
    绝不放完整 Key，也不放解密后的任何片段。
    """

    capability: str
    label: str
    configured: bool
    provider_id: str
    provider_label: str
    # 形如 `sk-••••••••••••a91f`。未配置时为 null；
    # 已配置但密文解不开（密钥轮换过）时也是 null。
    masked_key: str | None
    updated_at: datetime | None


class ProviderCredentialList(BaseModel):
    items: list[ProviderCredentialOut]


class ProviderCredentialIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)


class ProviderKeyTestIn(BaseModel):
    """测试连接。

    `api_key` 为空表示测已保存的那把——保存之后明文前端就拿不回来了，
    但用户仍然需要能确认"是不是 Key 过期了"。
    """

    api_key: str | None = Field(default=None, min_length=8, max_length=512)


class ProviderKeyTestOut(BaseModel):
    ok: bool
    provider_id: str
    # 成功/失败的具体原因，已脱敏。失败时前端直接展示这句，
    # 不要替换成"连接失败"——用户要的正是这句里的原因。
    message: str
    error_code: str | None = None
