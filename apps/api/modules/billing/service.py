"""计费模块的唯一对外入口。

生命周期（08_BillingCredits.md §2）：

    reserve  ──成功──> settle   实际成本 ≤ 预扣，差额退回
             ──失败──> release  预扣原样退回

三条不可让步的规则：
1. **改余额必先加行锁**，读-改-写不加锁在并发下必然丢更新
2. **每笔都要幂等键**，Worker 重试不能重复扣费
3. **余额不能为负**，应用层判断 + 数据库 CHECK 双保险
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError, Disposition
from apps.api.core.logging import get_logger
from apps.api.modules.billing import repository as repo
from apps.api.modules.billing.models import CreditAccount, CreditTransaction

log = get_logger(__name__)

MAX_PAGE_SIZE = 100

# 熔断默认值（19_UnitEconomics.md §7）。真实值走 pricing_rules，可热更新。
DEFAULT_RULES: dict[str, int] = {
    "task_cost_cap": 2_000,  # 单任务上限 ¥20
    "org_daily_cap": 50_000,  # 单租户日上限 ¥500
    "project_budget_multiplier": 130,  # 项目预算宽容度，百分比
    "image_retry_factor": 250,  # 图片废片率 ×100
    "video_retry_factor": 150,  # 视频废片率 ×100
    "overhead_rate": 3,  # 隐性成本占比，百分比
    # 自带 Key 时每个计费单位收的隐性成本（ADR-025）。
    # 19_UnitEconomics.md §1.5 是 ¥3–9/片（存储 + CDN + 审核 + 故障重跑计提），
    # 一部片约 204 张图，取中位摊到每张约 ¥0.03 = 3 Credits。
    # 与其它系数一样，真实值走 pricing_rules 热更新，这里只是缺配时的兜底。
    "byok_unit_credits": 3,
    # 注册体验额度（19_UnitEconomics.md §5）：够走完故事→角色→分镜，
    # 外加 1 个镜头试片。按 5% 转化率算 CAC 约 ¥100，可接受。
    # 绝不能大到够生成一整部片子——那样 CAC 会变成 ¥3000。
    "welcome_grant": 500,
}


def _key(action: str, task_id: uuid.UUID, attempt: int) -> str:
    """幂等键带尝试次数。

    不带的话重试会被上一次的预扣幂等挡掉，任务照跑但一分钱没扣——
    重试次数越多亏得越狠。
    """
    return f"{action}:{task_id}:{attempt}"


@dataclass(frozen=True, slots=True)
class Balance:
    balance: int
    reserved: int

    @property
    def total(self) -> int:
        return self.balance + self.reserved


async def get_balance(db: AsyncSession, *, org_id: uuid.UUID) -> Balance:
    acc = await repo.get_or_create_account(db, org_id=org_id)
    await db.commit()
    return Balance(balance=acc.balance, reserved=acc.reserved)


async def rules(db: AsyncSession) -> dict[str, int]:
    """计费参数。库里没配的用默认值兜底，不让缺一条配置就整个链路瘫痪。"""
    return {**DEFAULT_RULES, **(await repo.get_rules(db))}


# ---------------------------------------------------------------- 充值


async def topup(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    principal: int,
    bonus: int = 0,
    idempotency_key: str,
    note: str | None = None,
) -> Balance:
    """充值。本金与赠送**分两条流水**记账。

    不分账的话退款时算不清该退多少——用户充 298 送 22，
    退款只能退 298，混在一起就变成退 320。
    """
    if principal <= 0:
        raise AppError("common.validation_failed", message="充值金额必须为正")

    if existing := await repo.find_by_idempotency_key(db, key=idempotency_key):
        acc = await repo.lock_account(db, org_id=org_id)
        await db.commit()
        log.info("billing.topup_idempotent_hit", tx_id=str(existing.id))
        return Balance(balance=acc.balance, reserved=acc.reserved)

    acc = await repo.lock_account(db, org_id=org_id)
    await repo.append(
        db,
        account=acc,
        tx_type="TOPUP",
        amount=principal,
        reserved_delta=0,
        idempotency_key=idempotency_key,
        note=note,
    )
    if bonus > 0:
        await repo.append(
            db,
            account=acc,
            tx_type="ADMIN_GRANT",
            amount=bonus,
            reserved_delta=0,
            is_bonus=True,
            idempotency_key=f"{idempotency_key}:bonus",
            note="充值赠送",
        )
    await db.commit()
    return Balance(balance=acc.balance, reserved=acc.reserved)


async def grant_welcome_credits(db: AsyncSession, *, org_id: uuid.UUID) -> int:
    """新租户的体验额度。

    走 ADMIN_GRANT 并标记 is_bonus——它不是用户付的钱，退款时不该退。
    幂等键绑 org_id，重复调用不会重复发放。
    """
    cfg = await rules(db)
    amount = cfg["welcome_grant"]
    if amount <= 0:
        return 0

    key = f"welcome:{org_id}"
    if await repo.find_by_idempotency_key(db, key=key):
        return 0

    acc = await repo.lock_account(db, org_id=org_id)
    await repo.append(
        db,
        account=acc,
        tx_type="ADMIN_GRANT",
        amount=amount,
        reserved_delta=0,
        is_bonus=True,
        idempotency_key=key,
        note="注册体验额度",
    )
    await db.commit()
    return amount


# ---------------------------------------------------------------- 预扣 / 结算


async def reserve(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    amount: int,
    task_id: uuid.UUID,
    attempt: int = 0,
    project_budget_cap: int | None = None,
    project_spent: int = 0,
    ref_type: str = "task",
) -> CreditTransaction:
    """预扣。余额不足或触发熔断则抛错，任务不该开始。

    先扣后跑，不是跑完再扣——跑完再扣的话，余额不足时钱已经花在上游了。

    `task_id` 是这笔账挂靠的业务对象 id，`ref_type` 说明它是哪一种对象。
    绝大多数消费来自 `tasks`，但不是全部：资产库里"一段描述生成角色档案"
    是一次同步的真实 LLM 调用，它没有 task 行，账要挂在那条档案上。
    流水里指向一个不存在的 task 会让对账查不下去，所以类型必须记对。
    """
    if amount < 0:
        raise AppError("common.validation_failed", message="预扣金额不能为负")

    key = _key("reserve", task_id, attempt)
    if existing := await repo.find_by_idempotency_key(db, key=key):
        return existing  # 幂等：重复预扣直接返回

    cfg = await rules(db)
    acc = await repo.lock_account(db, org_id=org_id)

    _check_caps(
        amount=amount,
        account=acc,
        cfg=cfg,
        project_budget_cap=project_budget_cap,
        project_spent=project_spent,
        daily_spent=await repo.spent_since(db, account_id=acc.id, hours=24),
    )

    tx = await repo.append(
        db,
        account=acc,
        tx_type="RESERVE",
        amount=0,  # 只在 balance 与 reserved 之间挪，总资产不变
        reserved_delta=amount,
        idempotency_key=key,
        ref_type=ref_type,
        ref_id=task_id,
    )
    await db.commit()
    return tx


async def settle(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    task_id: uuid.UUID,
    actual_cost: int,
    attempt: int = 0,
    ref_type: str = "task",
) -> None:
    """结算。扣掉实际成本，预扣的差额退回可用余额。

    实际成本高于预扣时按预扣封顶——超出部分平台自己承担。
    宁可少收也不能让用户余额变负，那会引出一堆更麻烦的问题。
    """
    reservation = await repo.find_by_idempotency_key(db, key=_key("reserve", task_id, attempt))
    if reservation is None:
        log.warning("billing.settle_without_reserve", task_id=str(task_id))
        return

    key = _key("settle", task_id, attempt)
    if await repo.find_by_idempotency_key(db, key=key):
        return  # 幂等

    held = reservation.reserved_delta
    charged = max(0, min(actual_cost, held))
    if charged < actual_cost:
        log.warning(
            "billing.cost_overrun",
            task_id=str(task_id),
            reserved=held,
            actual=actual_cost,
        )

    acc = await repo.lock_account(db, org_id=org_id)
    await repo.append(
        db,
        account=acc,
        tx_type="CONSUME",
        amount=-charged,
        reserved_delta=-held,
        idempotency_key=key,
        ref_type=ref_type,
        ref_id=task_id,
        note=f"reserved={held} actual={actual_cost}",
    )
    await db.commit()


async def release(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    task_id: uuid.UUID,
    attempt: int = 0,
    ref_type: str = "task",
) -> None:
    """释放预扣，一分不扣。用于任务失败且责任不在用户的情况。"""
    reservation = await repo.find_by_idempotency_key(db, key=_key("reserve", task_id, attempt))
    if reservation is None:
        return

    key = _key("release", task_id, attempt)
    if await repo.find_by_idempotency_key(db, key=key):
        return

    acc = await repo.lock_account(db, org_id=org_id)
    await repo.append(
        db,
        account=acc,
        tx_type="RELEASE",
        amount=0,
        reserved_delta=-reservation.reserved_delta,
        idempotency_key=key,
        ref_type=ref_type,
        ref_id=task_id,
    )
    await db.commit()


async def finalize_by_disposition(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    task_id: uuid.UUID,
    disposition: Disposition,
    actual_cost: int,
    attempt: int = 0,
) -> None:
    """按错误处置方式收尾。

    处置方式直接来自错误目录（21_ErrorTaxonomy.md §2），
    不在这里另写一套 if/else——两处规则迟早会不一致，
    而不一致的后果是"某类失败漏了退款"。
    """
    if disposition is Disposition.KEEP:
        await settle(db, org_id=org_id, task_id=task_id, actual_cost=actual_cost, attempt=attempt)
    else:
        # RELEASE 和 REFUND 对预扣阶段是同一个动作：原样退回。
        # 两者的区别在已结算之后，那属于售后流程。
        await release(db, org_id=org_id, task_id=task_id, attempt=attempt)


# ---------------------------------------------------------------- 熔断


def _check_caps(
    *,
    amount: int,
    account: CreditAccount,
    cfg: dict[str, int],
    project_budget_cap: int | None,
    project_spent: int,
    daily_spent: int,
) -> None:
    """成本熔断（19_UnitEconomics.md §7）。

    顺序有讲究：先挡明显异常的参数错误，再挡预算，最后才看余额。
    这样用户看到的错误提示最贴近真实原因。
    """
    if amount > cfg["task_cost_cap"]:
        # 来自真实事故：把"生成 5 秒"写成"生成 5 分钟"，
        # 配合自动重试能在半小时里烧掉四位数。
        raise AppError(
            "billing.task_cap.exceeded",
            message=f"单任务成本 {amount} 超过上限 {cfg['task_cost_cap']}",
            detail={"amount": amount, "cap": cfg["task_cost_cap"]},
        )

    if project_budget_cap is not None:
        allowed = project_budget_cap * cfg["project_budget_multiplier"] // 100
        if project_spent + amount > allowed:
            raise AppError(
                "billing.budget.exceeded",
                message=f"项目预算 {allowed}，已用 {project_spent}，本次 {amount}",
                detail={"budget": allowed, "spent": project_spent, "requested": amount},
            )

    if daily_spent + amount > cfg["org_daily_cap"]:
        raise AppError(
            "billing.daily_cap.exceeded",
            message=f"日消费上限 {cfg['org_daily_cap']}，已用 {daily_spent}",
            detail={"cap": cfg["org_daily_cap"], "spent": daily_spent},
        )

    if account.balance < amount:
        raise AppError(
            "billing.credit.insufficient",
            message=f"余额 {account.balance} 不足以预扣 {amount}",
            detail={"balance": account.balance, "required": amount},
        )


# ---------------------------------------------------------------- 查询与对账


async def list_transactions(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    limit: int = 50,
    cursor: object = None,
) -> list[CreditTransaction]:
    acc = await repo.get_or_create_account(db, org_id=org_id)
    await db.commit()
    return await repo.list_transactions(
        db,
        account_id=acc.id,
        limit=max(1, min(limit, MAX_PAGE_SIZE)),
        cursor=cursor,  # type: ignore[arg-type]
    )


async def audit(db: AsyncSession, *, org_id: uuid.UUID) -> tuple[bool, int, int]:
    """对账。返回 (是否平账, 流水总和, 账户总额)。

    这条不成立就是账务 bug，应该立刻告警而不是等用户投诉。
    """
    acc = await repo.get_or_create_account(db, org_id=org_id)
    await db.commit()
    ledger = await repo.sum_amount(db, account_id=acc.id)
    actual = acc.balance + acc.reserved
    return ledger == actual, ledger, actual


async def safe_topup(db: AsyncSession, **kwargs: object) -> Balance:
    """topup 的并发安全包装：唯一约束撞车时按幂等处理。"""
    try:
        return await topup(db, **kwargs)  # type: ignore[arg-type]
    except IntegrityError:
        await db.rollback()
        return await get_balance(db, org_id=kwargs["org_id"])  # type: ignore[arg-type]
