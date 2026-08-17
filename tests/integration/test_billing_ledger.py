"""账务不变量。

这组用例守的是钱。任何一条挂了都意味着用户白花钱或平台白亏钱，
且账务错误往往在很久以后对账时才被发现，届时已经无法追溯。

核心不变量：**sum(transactions.amount) == balance + reserved**

注册会发体验额度，所以断言一律基于**基线增量**而非绝对值——
写死绝对值的话，以后调一次体验额度就要改十几处断言。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.db import session_scope
from apps.api.core.errors import AppError, Disposition
from apps.api.modules.billing import service as billing

pytestmark = pytest.mark.integration

C = "/api/v1/credits"


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


async def _api_balance(client: AsyncClient) -> dict[str, int]:
    return dict((await client.get(f"{C}/balance")).json())


async def _fund(client: AsyncClient, principal: int, bonus: int = 0) -> None:
    r = await client.post(
        f"{C}/topup",
        json={"principal": principal, "bonus": bonus},
        headers={"Idempotency-Key": f"t-{uuid.uuid4()}"},
    )
    assert r.status_code == 200, r.text


async def _drain(db: AsyncSession, org_id: uuid.UUID) -> None:
    """把余额清零，让后续断言可以用绝对值。

    用一笔 ADJUST 而不是直接改 balance 字段——流水必须能解释余额，
    绕过流水改余额会让对账不平。
    """
    from apps.api.modules.billing import repository as repo

    acc = await repo.lock_account(db, org_id=org_id)
    if acc.balance:
        await repo.append(
            db,
            account=acc,
            tx_type="ADJUST",
            amount=-acc.balance,
            reserved_delta=0,
            idempotency_key=f"drain:{uuid.uuid4()}",
            note="测试清零",
        )
    await db.commit()


# ------------------------------------------------------------------ 体验额度


async def test_new_org_gets_welcome_grant(alice: AsyncClient, db: AsyncSession) -> None:
    """注册即发体验额度（19_UnitEconomics.md §5）。

    必须标为 bonus——那不是用户付的钱，退款时不该退。
    也绝不能大到够生成一整部片子，否则 CAC 会从 ¥100 变成 ¥3000。
    """
    cfg = await billing.rules(db)
    assert (await _api_balance(alice))["balance"] == cfg["welcome_grant"]

    txs = (await alice.get(f"{C}/transactions")).json()
    grants = [t for t in txs if t["type"] == "ADMIN_GRANT"]
    assert len(grants) == 1
    assert grants[0]["is_bonus"] is True


async def test_welcome_grant_is_not_repeated(alice: AsyncClient, db: AsyncSession) -> None:
    org = await _org(alice)
    before = (await billing.get_balance(db, org_id=org)).balance
    assert await billing.grant_welcome_credits(db, org_id=org) == 0
    assert (await billing.get_balance(db, org_id=org)).balance == before


# ------------------------------------------------------------------ 充值


async def test_topup_records_principal_and_bonus_separately(alice: AsyncClient) -> None:
    """本金与赠送分账。混在一起的话退款金额永远算不对。"""
    before = (await _api_balance(alice))["balance"]
    await _fund(alice, principal=29800, bonus=2200)

    txs = (await alice.get(f"{C}/transactions")).json()
    principal = [t for t in txs if t["type"] == "TOPUP"]
    assert len(principal) == 1
    assert principal[0]["amount"] == 29800
    assert principal[0]["is_bonus"] is False

    topup_bonus = [t for t in txs if t["type"] == "ADMIN_GRANT" and t["amount"] == 2200]
    assert len(topup_bonus) == 1
    assert topup_bonus[0]["is_bonus"] is True

    assert (await _api_balance(alice))["balance"] == before + 32000


async def test_topup_is_idempotent(alice: AsyncClient) -> None:
    before = (await _api_balance(alice))["balance"]
    key = f"t-{uuid.uuid4()}"
    body = {"principal": 1000}
    await alice.post(f"{C}/topup", json=body, headers={"Idempotency-Key": key})
    await alice.post(f"{C}/topup", json=body, headers={"Idempotency-Key": key})

    assert (await _api_balance(alice))["balance"] == before + 1000


# ------------------------------------------------------------------ 预扣生命周期


async def test_reserve_moves_balance_to_reserved(alice: AsyncClient, db: AsyncSession) -> None:
    org = await _org(alice)
    await _drain(db, org)
    await _fund(alice, 1000)

    await billing.reserve(db, org_id=org, amount=300, task_id=uuid.uuid4())

    b = await billing.get_balance(db, org_id=org)
    assert b.balance == 700
    assert b.reserved == 300
    assert b.total == 1000, "预扣只是挪桶，总资产不该变"


async def test_settle_charges_actual_and_refunds_difference(
    alice: AsyncClient, db: AsyncSession
) -> None:
    """预扣 300 实花 120，差额 180 必须退回可用余额。"""
    org = await _org(alice)
    await _drain(db, org)
    await _fund(alice, 1000)
    tid = uuid.uuid4()

    await billing.reserve(db, org_id=org, amount=300, task_id=tid)
    await billing.settle(db, org_id=org, task_id=tid, actual_cost=120)

    b = await billing.get_balance(db, org_id=org)
    assert b.reserved == 0
    assert b.balance == 880
    assert b.total == 880


async def test_release_returns_everything(alice: AsyncClient, db: AsyncSession) -> None:
    org = await _org(alice)
    await _drain(db, org)
    await _fund(alice, 1000)
    tid = uuid.uuid4()

    await billing.reserve(db, org_id=org, amount=300, task_id=tid)
    await billing.release(db, org_id=org, task_id=tid)

    b = await billing.get_balance(db, org_id=org)
    assert b.balance == 1000
    assert b.reserved == 0


async def test_settle_caps_at_reserved_amount(alice: AsyncClient, db: AsyncSession) -> None:
    """实际成本超过预扣时按预扣封顶，超出部分平台承担。

    宁可少收也不能让余额变负——负余额会引出一堆更麻烦的问题。
    """
    org = await _org(alice)
    await _drain(db, org)
    await _fund(alice, 1000)
    tid = uuid.uuid4()

    await billing.reserve(db, org_id=org, amount=200, task_id=tid)
    await billing.settle(db, org_id=org, task_id=tid, actual_cost=999)

    b = await billing.get_balance(db, org_id=org)
    assert b.balance == 800
    assert b.reserved == 0
    assert b.balance >= 0


async def test_settle_is_idempotent(alice: AsyncClient, db: AsyncSession) -> None:
    """Worker 重试导致的重复结算不能扣两次。"""
    org = await _org(alice)
    await _drain(db, org)
    await _fund(alice, 1000)
    tid = uuid.uuid4()

    await billing.reserve(db, org_id=org, amount=300, task_id=tid)
    for _ in range(3):
        await billing.settle(db, org_id=org, task_id=tid, actual_cost=100)

    assert (await billing.get_balance(db, org_id=org)).balance == 900


async def test_reserve_is_idempotent(alice: AsyncClient, db: AsyncSession) -> None:
    org = await _org(alice)
    await _drain(db, org)
    await _fund(alice, 1000)
    tid = uuid.uuid4()

    await billing.reserve(db, org_id=org, amount=300, task_id=tid)
    await billing.reserve(db, org_id=org, amount=300, task_id=tid)

    assert (await billing.get_balance(db, org_id=org)).reserved == 300, "重复预扣不能累加"


async def test_retry_uses_separate_reservation(alice: AsyncClient, db: AsyncSession) -> None:
    """重试要能重新预扣。

    幂等键不带 attempt 的话，第二次预扣会被第一次挡掉——
    任务照跑但一分钱没扣，重试越多亏越狠。
    """
    org = await _org(alice)
    await _drain(db, org)
    await _fund(alice, 1000)
    tid = uuid.uuid4()

    await billing.reserve(db, org_id=org, amount=100, task_id=tid, attempt=0)
    await billing.settle(db, org_id=org, task_id=tid, actual_cost=100, attempt=0)

    await billing.reserve(db, org_id=org, amount=100, task_id=tid, attempt=1)

    b = await billing.get_balance(db, org_id=org)
    assert b.reserved == 100, "第二次尝试必须能独立预扣"
    assert b.balance == 800


# ------------------------------------------------------------------ 熔断


async def test_insufficient_balance_rejected(alice: AsyncClient, db: AsyncSession) -> None:
    org = await _org(alice)
    await _drain(db, org)
    await _fund(alice, 100)

    with pytest.raises(AppError) as exc:
        await billing.reserve(db, org_id=org, amount=500, task_id=uuid.uuid4())
    assert exc.value.code == "billing.credit.insufficient"

    assert (await billing.get_balance(db, org_id=org)).balance == 100, "失败不能改动余额"


async def test_task_cost_cap_blocks_runaway(alice: AsyncClient, db: AsyncSession) -> None:
    """单任务成本熔断。

    来自真实事故：把"生成 5 秒"写成"生成 5 分钟"，
    配合自动重试能在半小时里烧掉四位数。
    """
    org = await _org(alice)
    cfg = await billing.rules(db)
    # 充值单笔上限 1_000_000，分两笔，确保余额绝不是熔断原因
    await _fund(alice, 1_000_000)
    await _fund(alice, 1_000_000)

    with pytest.raises(AppError) as exc:
        await billing.reserve(db, org_id=org, amount=cfg["task_cost_cap"] + 1, task_id=uuid.uuid4())
    assert exc.value.code == "billing.task_cap.exceeded"


async def test_project_budget_cap_blocks(alice: AsyncClient, db: AsyncSession) -> None:
    org = await _org(alice)
    await _fund(alice, 100_000)

    with pytest.raises(AppError) as exc:
        await billing.reserve(
            db,
            org_id=org,
            amount=500,
            task_id=uuid.uuid4(),
            project_budget_cap=1000,
            project_spent=1000,  # 已用满，130% 宽容度也不够
        )
    assert exc.value.code == "billing.budget.exceeded"


async def test_negative_reserve_rejected(alice: AsyncClient, db: AsyncSession) -> None:
    org = await _org(alice)
    with pytest.raises(AppError):
        await billing.reserve(db, org_id=org, amount=-100, task_id=uuid.uuid4())


# ------------------------------------------------------------------ 并发与对账


async def test_concurrent_reserves_cannot_overspend(alice: AsyncClient) -> None:
    """10 个并发预扣，每个 300，余额只有 1000。

    最多只能成功 3 个。读-改-写不加行锁的话这里必然超卖。
    """
    org = await _org(alice)
    async with session_scope() as s:
        await _drain(s, org)
    await _fund(alice, 1000)

    async def try_reserve() -> bool:
        async with session_scope() as s:
            try:
                await billing.reserve(s, org_id=org, amount=300, task_id=uuid.uuid4())
            except AppError:
                return False
        return True

    succeeded = sum(await asyncio.gather(*(try_reserve() for _ in range(10))))

    async with session_scope() as s:
        b = await billing.get_balance(s, org_id=org)

    assert succeeded == 3, f"应恰好成功 3 次，实际 {succeeded}"
    assert b.reserved == 900
    assert b.balance == 100
    assert b.balance >= 0, "余额绝不能为负"


async def test_ledger_always_balances(alice: AsyncClient, db: AsyncSession) -> None:
    """跑一串混合操作后，流水总和必须等于账户总额。

    这条不成立就是账务 bug，应该告警停机而不是等用户投诉。
    """
    org = await _org(alice)
    await _fund(alice, 5000, bonus=500)

    for i in range(5):
        tid = uuid.uuid4()
        await billing.reserve(db, org_id=org, amount=200, task_id=tid)
        if i % 2 == 0:
            await billing.settle(db, org_id=org, task_id=tid, actual_cost=150)
        else:
            await billing.release(db, org_id=org, task_id=tid)

    ok, ledger, actual = await billing.audit(db, org_id=org)
    assert ok, f"账不平：流水 {ledger} vs 账户 {actual}"


async def test_disposition_drives_settlement(alice: AsyncClient, db: AsyncSession) -> None:
    """处置方式来自错误目录，不在计费里另写一套判断。"""
    org = await _org(alice)
    await _drain(db, org)
    await _fund(alice, 3000)

    keep, released, refunded = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for tid in (keep, released, refunded):
        await billing.reserve(db, org_id=org, amount=300, task_id=tid)

    await billing.finalize_by_disposition(
        db, org_id=org, task_id=keep, disposition=Disposition.KEEP, actual_cost=300
    )
    await billing.finalize_by_disposition(
        db, org_id=org, task_id=released, disposition=Disposition.RELEASE, actual_cost=0
    )
    await billing.finalize_by_disposition(
        db, org_id=org, task_id=refunded, disposition=Disposition.REFUND, actual_cost=0
    )

    b = await billing.get_balance(db, org_id=org)
    assert b.balance == 2700, "只有 KEEP 那一笔真扣了钱"
    assert b.reserved == 0


async def test_balances_are_isolated_across_orgs(alice: AsyncClient, bob: AsyncClient) -> None:
    before_bob = (await _api_balance(bob))["balance"]
    before_alice = (await _api_balance(alice))["balance"]

    await _fund(alice, 5000)

    assert (await _api_balance(alice))["balance"] == before_alice + 5000
    assert (await _api_balance(bob))["balance"] == before_bob, "不能影响其他租户"


async def test_credits_require_auth(client: AsyncClient) -> None:
    assert (await client.get(f"{C}/balance")).status_code == 401
    assert (await client.get(f"{C}/transactions")).status_code == 401
