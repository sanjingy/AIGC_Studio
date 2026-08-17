"""计费 ORM。模块私有。

账务模型（08_BillingCredits.md §2）：绝不只保存一个 balance 字段，
必须有不可变流水。余额是流水的投影，不是真相本身。

**核心不变量：`sum(transactions.amount) == balance + reserved`**
任何时候这条不成立，都说明账务出了 bug，必须立刻停机排查。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.core.models import BaseEntity, OrgEntity

RESOURCE_TYPES = ("AI_CREDITS", "GPU_SECONDS")

# 08_BillingCredits.md §3
TRANSACTION_TYPES = (
    "TOPUP",  # 充值本金
    "ADMIN_GRANT",  # 赠送 / 运营发放
    "RESERVE",  # 预扣：balance → reserved
    "RELEASE",  # 释放预扣：reserved → balance
    "CONSUME",  # 结算：扣掉实际成本
    "REFUND",  # 退款
    "ADJUST",  # 人工调账
    "GPU_CHARGE",
)


class CreditAccount(OrgEntity):
    """钱包。

    `balance` 是可支配余额，`reserved` 是已预扣未结算。
    两者都不允许为负——用 CHECK 约束在数据库层兜底，
    因为应用层的判断总有可能被某条新写的路径绕过。
    """

    __tablename__ = "credit_accounts"

    resource_type: Mapped[str] = mapped_column(String(16), nullable=False)
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("org_id", "resource_type", name="uq_credit_accounts_org_resource"),
        CheckConstraint("balance >= 0", name="balance_non_negative"),
        CheckConstraint("reserved >= 0", name="reserved_non_negative"),
    )


class CreditTransaction(BaseEntity):
    """不可变流水。只 INSERT，永不 UPDATE / DELETE。

    `amount` 记录的是**总资产**（balance + reserved）的变化：
        TOPUP        +N        充值
        ADMIN_GRANT  +N        赠送
        RESERVE       0        只在两个桶之间挪，总量不变
        RELEASE       0        同上
        CONSUME      -A        真正花掉的部分
        REFUND       +A
    这样 `sum(amount)` 恒等于 `balance + reserved`，对账只需一条 SQL。
    """

    __tablename__ = "credit_transactions"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("credit_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)

    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_delta: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_after: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # 赠送额度与本金分账：退款只退本金（08_BillingCredits.md §8）
    is_bonus: Mapped[bool] = mapped_column(nullable=False, default=False)

    ref_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # 防 Worker 重试造成重复扣费（08_BillingCredits.md §6）
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_credit_tx_account_created", "account_id", "created_at"),
        Index("ix_credit_tx_ref", "ref_type", "ref_id"),
    )


class PricingRule(BaseEntity):
    """可热更新的计费参数（ADR-014）。

    废片率、overhead、各级熔断阈值都放这里。
    代码里出现任何价格常量都是 bug——上游一调价就得改代码重新发版，
    而上游调价是常态（DeepSeek 2026-08-17 涨了 350%）。
    """

    __tablename__ = "pricing_rules"

    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ModelPricing(BaseEntity):
    """上游单价。带生效时间，历史订单按当时价格核算。"""

    __tablename__ = "model_pricing"

    model_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)  # token/second/image/call
    provider_cost: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 成本，最小单位
    credit_price: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 售价，Credits

    __table_args__ = (Index("ix_model_pricing_model", "model_id", "created_at"),)
