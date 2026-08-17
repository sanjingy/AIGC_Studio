"""成本估算（19_UnitEconomics.md §6）。

**这里不允许出现任何价格常量。**
单价来自 `model_pricing` 表，系数来自 `pricing_rules` 表，全部可热更新（ADR-014）。

理由是实打实的：DeepSeek 于 2026-08-17 高峰输出价上涨 350%。
把价格写进代码的实现会在那一夜毛利转负，且要改代码重新发版才能救。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.logging import get_logger
from apps.api.modules.billing import service as billing_service
from apps.api.modules.billing.models import ModelPricing

log = get_logger(__name__)

# 各任务类型默认使用的模型。真实单价查表，这里只是"用哪个模型"的映射。
_DEFAULT_MODEL = {
    "image.generate": "wan2.2-t2i-flash",
}

# Mock 任务的名义计费基数（Credits）。它们不调用上游，
# 存在的意义只是让计费链路在无真实 Provider 时也能跑通。
_MOCK_BASE_COST = 20


async def _unit_price(db: AsyncSession, model_id: str) -> int | None:
    row = (
        await db.execute(
            select(ModelPricing.credit_price)
            .where(ModelPricing.model_id == model_id)
            .order_by(ModelPricing.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return int(row) if row is not None else None


async def estimate(db: AsyncSession, *, task_type: str, payload: dict[str, Any]) -> int:
    """估算任务成本，返回 Credits。

    Router 对外要给**区间**而非点值，但预扣按区间上限扣，
    结算时退差额——先扣多了能退，扣少了就得平台垫。
    """
    cfg = await billing_service.rules(db)

    if task_type.startswith("mock."):
        steps = max(1, int(payload.get("steps", 1) or 1))
        base = _MOCK_BASE_COST * steps
    elif task_type == "image.generate":
        model = str(payload.get("model_id") or _DEFAULT_MODEL[task_type])
        unit = await _unit_price(db, model)
        if unit is None:
            # 表里没这个模型的价，用熔断上限的十分之一兜底并告警。
            # 宁可高估拦下来，也不要低估放行——低估会让预扣不够，
            # 结算时差额由平台承担。
            unit = cfg["task_cost_cap"] // 10
            log.warning("pricing.model_not_priced", model_id=model, fallback=unit)
        n = max(1, int(payload.get("n", 1) or 1))
        # 废片率算进预扣：一张图平均要生成 image_retry_factor/100 次
        base = apply_retry_factor(unit * n, cfg["image_retry_factor"])
    else:
        base = cfg["task_cost_cap"] // 10
        log.warning("pricing.unknown_task_type", task_type=task_type, fallback=base)

    return base + base * cfg["overhead_rate"] // 100


def apply_retry_factor(base: int, factor_x100: int) -> int:
    """把废片率折进成本。

    factor 存成整数百分比（250 = 2.5 次），避免浮点数进钱的计算。
    """
    return base * factor_x100 // 100
