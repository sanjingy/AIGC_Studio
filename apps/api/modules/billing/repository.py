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
    ProviderCredential,
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


# ------------------------------------------------------------ BYOK 凭证


async def get_credential(
    db: AsyncSession, *, org_id: uuid.UUID, capability: str, include_deleted: bool = False
) -> ProviderCredential | None:
    """取某个 org 某个能力的凭证。

    `include_deleted` 只给 upsert 用：唯一约束 `(org_id, capability)`
    **不排除软删行**，所以"换 Key"必须找到那一行原地改，
    做成"软删旧的再插新的"会直接撞约束。
    """
    stmt = select(ProviderCredential).where(
        ProviderCredential.org_id == org_id,
        ProviderCredential.capability == capability,
    )
    if not include_deleted:
        stmt = stmt.where(ProviderCredential.deleted_at.is_(None))
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_credentials(db: AsyncSession, *, org_id: uuid.UUID) -> list[ProviderCredential]:
    return list(
        (
            await db.execute(
                select(ProviderCredential)
                .where(
                    ProviderCredential.org_id == org_id,
                    ProviderCredential.deleted_at.is_(None),
                )
                .order_by(ProviderCredential.capability)
            )
        ).scalars()
    )


async def upsert_credential(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    capability: str,
    provider_id: str,
    key_encrypted: str,
    created_by: uuid.UUID,
) -> ProviderCredential:
    """写入或就地更新凭证。软删过的行会被复活，不是插新行。"""
    row = await get_credential(db, org_id=org_id, capability=capability, include_deleted=True)
    if row is None:
        row = ProviderCredential(
            org_id=org_id,
            capability=capability,
            provider_id=provider_id,
            key_encrypted=key_encrypted,
            created_by=created_by,
        )
        db.add(row)
    else:
        row.provider_id = provider_id
        row.key_encrypted = key_encrypted
        row.created_by = created_by
        row.deleted_at = None
        await db.flush()
        # `updated_at` 是服务端 onupdate 算的：UPDATE 不带 RETURNING，
        # 这个属性在 flush 之后就是过期状态。等调用方去读它会触发一次
        # 同步 refresh，在 async 上下文里直接抛 MissingGreenlet，
        # 把"保存成功"变成一个 500。这里显式刷一次，把 IO 留在 async 里。
        await db.refresh(row)
        return row
    await db.flush()
    return row


async def soft_delete_credential(db: AsyncSession, *, row: ProviderCredential) -> None:
    """软删。密文一并清空——留着它没有任何用途，只是多一份可被翻出来的材料。"""
    row.deleted_at = datetime.now(UTC)
    row.key_encrypted = ""
    await db.flush()
