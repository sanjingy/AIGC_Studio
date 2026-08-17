"""计费数据访问。模块私有。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.billing.models import (
    CreditAccount,
    CreditTransaction,
    PricingRule,
)


async def get_or_create_account(
    db: AsyncSession, *, org_id: uuid.UUID, resource_type: str = "AI_CREDITS"
) -> CreditAccount:
    """取账户，没有就建。

    用 ON CONFLICT DO NOTHING 而不是先查后插——两个并发请求同时给新用户
    建账户时，先查后插会有一个撞唯一约束然后整个事务炸掉。
    """
    stmt = (
        pg_insert(CreditAccount)
        .values(org_id=org_id, resource_type=resource_type, balance=0, reserved=0)
        .on_conflict_do_nothing(index_elements=["org_id", "resource_type"])
    )
    await db.execute(stmt)
    return (
        await db.execute(
            select(CreditAccount).where(
                CreditAccount.org_id == org_id,
                CreditAccount.resource_type == resource_type,
            )
        )
    ).scalar_one()


async def lock_account(
    db: AsyncSession, *, org_id: uuid.UUID, resource_type: str = "AI_CREDITS"
) -> CreditAccount:
    """加行锁读取账户。

    **所有改动余额的操作都必须先走这里。**
    读-改-写不加锁 = 并发下必然丢更新 = 用户白花钱或平台白亏钱。
    """
    await get_or_create_account(db, org_id=org_id, resource_type=resource_type)
    return (
        await db.execute(
            select(CreditAccount)
            .where(
                CreditAccount.org_id == org_id,
                CreditAccount.resource_type == resource_type,
            )
            .with_for_update()
        )
    ).scalar_one()


async def find_by_idempotency_key(db: AsyncSession, *, key: str) -> CreditTransaction | None:
    return (
        await db.execute(select(CreditTransaction).where(CreditTransaction.idempotency_key == key))
    ).scalar_one_or_none()


async def append(
    db: AsyncSession,
    *,
    account: CreditAccount,
    tx_type: str,
    amount: int,
    reserved_delta: int,
    idempotency_key: str,
    is_bonus: bool = False,
    ref_type: str | None = None,
    ref_id: uuid.UUID | None = None,
    note: str | None = None,
) -> CreditTransaction:
    """写一条流水并同步更新账户。

    account 必须已经通过 lock_account 加过锁。
    """
    # balance 的变化 = 总资产变化 - 预扣变化
    account.balance += amount - reserved_delta
    account.reserved += reserved_delta

    row = CreditTransaction(
        account_id=account.id,
        type=tx_type,
        amount=amount,
        reserved_delta=reserved_delta,
        balance_after=account.balance,
        reserved_after=account.reserved,
        is_bonus=is_bonus,
        ref_type=ref_type,
        ref_id=ref_id,
        idempotency_key=idempotency_key,
        note=note,
    )
    db.add(row)
    await db.flush()
    return row


async def list_transactions(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    limit: int,
    cursor: datetime | None,
) -> list[CreditTransaction]:
    stmt = select(CreditTransaction).where(CreditTransaction.account_id == account_id)
    if cursor:
        stmt = stmt.where(CreditTransaction.created_at < cursor)
    return list(
        (
            await db.execute(stmt.order_by(CreditTransaction.created_at.desc()).limit(limit))
        ).scalars()
    )


async def sum_amount(db: AsyncSession, *, account_id: uuid.UUID) -> int:
    """流水总和。用于对账：必须恒等于 balance + reserved。"""
    return (
        await db.execute(
            select(func.coalesce(func.sum(CreditTransaction.amount), 0)).where(
                CreditTransaction.account_id == account_id
            )
        )
    ).scalar_one()


async def spent_since(db: AsyncSession, *, account_id: uuid.UUID, hours: int) -> int:
    """近 N 小时的实际消耗（正数）。用于日消费熔断。"""
    since = datetime.now(UTC) - timedelta(hours=hours)
    total = (
        await db.execute(
            select(func.coalesce(func.sum(CreditTransaction.amount), 0)).where(
                CreditTransaction.account_id == account_id,
                CreditTransaction.type == "CONSUME",
                CreditTransaction.created_at >= since,
            )
        )
    ).scalar_one()
    return -int(total)  # CONSUME 是负数，取消耗量


# ------------------------------------------------------------------ 计费参数


async def get_rules(db: AsyncSession) -> dict[str, int]:
    rows = (await db.execute(select(PricingRule))).scalars()
    return {r.key: r.value for r in rows}
