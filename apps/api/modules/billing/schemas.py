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
