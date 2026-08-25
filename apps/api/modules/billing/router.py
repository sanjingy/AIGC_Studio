from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Header, Query, status

from apps.api.core.errors import AppError
from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.billing import credentials, pricing, service
from apps.api.modules.billing.schemas import (
    BalanceOut,
    EstimateIn,
    EstimateOut,
    ProviderCredentialIn,
    ProviderCredentialList,
    ProviderCredentialOut,
    ProviderKeyTestIn,
    ProviderKeyTestOut,
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
    credits = await pricing.estimate(
        db, task_type=payload.type, payload=payload.input, org_id=user.org_id
    )
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


# ---------------------------------------------------------------- BYOK（ADR-025）
#
# 单独一个 router 而不是挂在 /credits 下：这些不是流水，是配置。
# 路径以能力为主键——一个 org 同一个能力只有一把 Key，
# 用能力当路径参数，PUT 天然就是"新增或更换"的语义。

credentials_router = APIRouter(prefix="/provider-credentials", tags=["provider-credentials"])


def _out(view: credentials.CredentialView) -> ProviderCredentialOut:
    return ProviderCredentialOut(
        capability=view.capability,
        label=view.label,
        configured=view.configured,
        provider_id=view.provider_id,
        provider_label=view.provider_label,
        masked_key=view.masked_key,
        updated_at=view.updated_at,
    )


@credentials_router.get("", response_model=ProviderCredentialList)
async def list_credentials(user: CurrentUser, db: DbSession) -> ProviderCredentialList:
    """列出每个可配置能力的状态。已配置的只给尾号，绝不给完整 Key。"""
    views = await credentials.list_for_org(db, org_id=user.org_id)
    return ProviderCredentialList(items=[_out(v) for v in views])


@credentials_router.put("/{capability}", response_model=ProviderCredentialOut)
async def put_credential(
    capability: str,
    payload: ProviderCredentialIn,
    user: CurrentUser,
    db: DbSession,
) -> ProviderCredentialOut:
    """配置或更换某个能力的 Key。就地覆盖，同一个能力只留一把。"""
    view = await credentials.put_key(
        db,
        org_id=user.org_id,
        user_id=user.id,
        capability=capability,
        api_key=payload.api_key,
    )
    return _out(view)


@credentials_router.delete("/{capability}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(capability: str, user: CurrentUser, db: DbSession) -> None:
    """移除 Key。该能力的调用与计费自动退回平台档。"""
    await credentials.delete_key(db, org_id=user.org_id, capability=capability)


@credentials_router.post("/{capability}/test", response_model=ProviderKeyTestOut)
async def test_credential(
    capability: str,
    payload: ProviderKeyTestIn,
    user: CurrentUser,
    db: DbSession,
) -> ProviderKeyTestOut:
    """测试连接。走上游的免费端点，不产生生成费用。

    Key 不可用时这个接口仍然返回 200——那是一个正常的测试结论，
    不是调用失败。结论在 `ok` 里，原因在 `message` 里。
    """
    result = await credentials.test_key(
        db, org_id=user.org_id, capability=capability, api_key=payload.api_key
    )
    return ProviderKeyTestOut(
        ok=result.ok,
        provider_id=result.provider_id,
        message=result.message,
        error_code=result.error_code,
    )
