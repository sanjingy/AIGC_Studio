from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Header, Query

from apps.api.core.errors import AppError
from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.billing import pricing, service
from apps.api.modules.billing.schemas import (
    BalanceOut,
    EstimateIn,
    EstimateOut,
    TopupIn,
    TransactionOut,
)

router = APIRouter(prefix="/credits", tags=["credits"])


@router.get("/balance", response_model=BalanceOut)
async def get_balance(user: CurrentUser, db: DbSession) -> BalanceOut:
    b = await service.get_balance(db, org_id=user.org_id)
    return BalanceOut(balance=b.balance, reserved=b.reserved, total=b.total)


@router.get("/transactions", response_model=list[TransactionOut])
async def list_transactions(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
) -> list[TransactionOut]:
    parsed: datetime | None = None
    if cursor:
        try:
            parsed = datetime.fromisoformat(cursor)
        except ValueError as exc:
            raise AppError("common.validation_failed", message="cursor 格式非法") from exc

    rows = await service.list_transactions(db, org_id=user.org_id, limit=limit, cursor=parsed)
    return [TransactionOut.model_validate(r) for r in rows]


@router.post("/estimate", response_model=EstimateOut)
async def estimate_cost(payload: EstimateIn, user: CurrentUser, db: DbSession) -> EstimateOut:
    del user
    credits = await pricing.estimate(db, task_type=payload.type, payload=payload.input)
    # 区间来自 19_UnitEconomics.md §6：[×0.8, ×1.4]
    return EstimateOut(
        estimated_credits=credits,
        range_low=credits * 80 // 100,
        range_high=credits * 140 // 100,
    )


@router.post("/topup", response_model=BalanceOut)
async def topup(
    payload: TopupIn,
    user: CurrentUser,
    db: DbSession,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> BalanceOut:
    """S5 只做记账，不接支付通道。

    真实支付在 M3。现在这个端点用于把计费链路跑通，
    以及给测试账号发额度。
    """
    b = await service.topup(
        db,
        org_id=user.org_id,
        principal=payload.principal,
        bonus=payload.bonus,
        idempotency_key=idempotency_key or f"topup:{uuid.uuid4()}",
    )
    return BalanceOut(balance=b.balance, reserved=b.reserved, total=b.total)
