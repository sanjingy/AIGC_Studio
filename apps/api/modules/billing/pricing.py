"""成本估算（19_UnitEconomics.md §6）。

**这里不允许出现任何价格常量。**
所有单价来自 model_pricing 表，所有系数来自 pricing_rules 表，
全部可热更新（ADR-014）。

理由是实打实的：DeepSeek 于 2026-08-17 高峰输出价上涨 350%。
把价格写进代码的实现会在那一夜毛利转负，而且要改代码重新发版才能救。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.billing import service as billing_service

# 各任务类型的计费基数（Credits）。
# S6 接入真实 Provider 后改为查 model_pricing，这里只服务 Mock 任务。
_MOCK_BASE_COST = {
    "mock.echo": 20,
    "mock.fail": 20,
}


async def estimate(db: AsyncSession, *, task_type: str, payload: dict[str, Any]) -> int:
    """估算任务成本，返回 Credits。

    Router 对外要给**区间**而非点值，但预扣按区间上限扣，
    结算时退差额——先扣多了能退，扣少了就得平台垫。
    """
    cfg = await billing_service.rules(db)

    base = _MOCK_BASE_COST.get(task_type)
    if base is None:
        # 未知类型给一个保守值，让熔断有机会拦住而不是直接放行
        base = cfg["task_cost_cap"] // 10

    if task_type.startswith("mock."):
        steps = int(payload.get("steps", 1) or 1)
        base *= max(1, steps)

    overhead = base * cfg["overhead_rate"] // 100
    return base + overhead


def apply_retry_factor(base: int, factor_x100: int) -> int:
    """把废片率折进成本。

    factor 存成整数百分比（250 = 2.5 次），避免浮点数进钱的计算。
    """
    return base * factor_x100 // 100
