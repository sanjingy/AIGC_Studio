"""成本估算（19_UnitEconomics.md §6）。

**这里不允许出现任何价格常量。**
单价来自 `model_pricing` 表，系数来自 `pricing_rules` 表，全部可热更新（ADR-014）。

理由是实打实的：DeepSeek 于 2026-08-17 高峰输出价上涨 350%。
把价格写进代码的实现会在那一夜毛利转负，且要改代码重新发版才能救。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.logging import get_logger
from apps.api.modules.billing import credentials
from apps.api.modules.billing import service as billing_service
from apps.api.modules.billing.models import ModelPricing

log = get_logger(__name__)

# 各任务类型默认使用的模型。真实单价查表，这里只是"用哪个模型"的映射。
_DEFAULT_MODEL = {
    "image.generate": "wan2.2-t2i-flash",
}

# 任务类型 → 它消耗的上游能力。BYOK 的折扣按能力生效（ADR-025），
# 所以必须知道这个任务花的是哪个能力的钱。
# `mock.*` 和 `timeline.render` 不打上游，故意不在表里——本地 ffmpeg
# 合成不因为用户配了 Key 就该便宜。
_CAPABILITY_OF = {
    "image.generate": "image_generation",
    # 漫剧的视频是"首帧图 → 视频"，不是文生视频
    "video.generate": "image_to_video",
    "audio.tts": "text_to_speech",
}

# Mock 任务的名义计费基数（Credits）。它们不调用上游，
# 存在的意义只是让计费链路在无真实 Provider 时也能跑通。
_MOCK_BASE_COST = 20


@dataclass(frozen=True, slots=True)
class _Shape:
    """一个任务的计费形状：算几份、折不折废片率、花的是哪个能力。

    把"几份"和"每份多少钱"拆开，是为了让 BYOK 的分支只替换后者——
    自带 Key 换的是上游单价，不改变这次任务要出几张图。
    """

    units: int
    capability: str | None
    model_id: str | None = None
    retry_rule: str | None = None
    mock: bool = False


def _shape(task_type: str, payload: dict[str, Any]) -> _Shape:
    if task_type.startswith("mock."):
        return _Shape(units=max(1, int(payload.get("steps", 1) or 1)), capability=None, mock=True)

    if task_type == "image.generate":
        return _Shape(
            units=max(1, int(payload.get("n", 1) or 1)),
            capability=_CAPABILITY_OF[task_type],
            model_id=str(payload.get("model_id") or _DEFAULT_MODEL[task_type]),
            # 废片率算进预扣：一张图平均要生成 image_retry_factor/100 次
            retry_rule="image_retry_factor",
        )

    log.warning("pricing.unknown_task_type", task_type=task_type)
    return _Shape(units=1, capability=_CAPABILITY_OF.get(task_type))


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


async def _platform_unit_price(db: AsyncSession, shape: _Shape, cfg: dict[str, int]) -> int:
    """平台档单价：`model_pricing` 的售价，已含 provider_cost 的加价。"""
    if shape.mock:
        return _MOCK_BASE_COST
    if shape.model_id is None:
        return cfg["task_cost_cap"] // 10

    unit = await _unit_price(db, shape.model_id)
    if unit is None:
        # 表里没这个模型的价，用熔断上限的十分之一兜底并告警。
        # 宁可高估拦下来，也不要低估放行——低估会让预扣不够，
        # 结算时差额由平台承担。
        unit = cfg["task_cost_cap"] // 10
        log.warning("pricing.model_not_priced", model_id=shape.model_id, fallback=unit)
    return unit


async def uses_own_key(
    db: AsyncSession, *, org_id: uuid.UUID | None, capability: str | None
) -> bool:
    """这次调用是不是走用户自己的 Key。

    org_id 缺省（内部估价、无租户上下文）时一律按平台档算——
    宁可估高，也不要在拿不准的时候给出折扣价。
    """
    if org_id is None or capability is None:
        return False
    return await credentials.has_own_key(db, org_id=org_id, capability=capability)


async def estimate(
    db: AsyncSession,
    *,
    task_type: str,
    payload: dict[str, Any],
    org_id: uuid.UUID | None = None,
) -> int:
    """估算任务成本，返回 Credits。

    Router 对外要给**区间**而非点值，但预扣按区间上限扣，
    结算时退差额——先扣多了能退，扣少了就得平台垫。

    **BYOK 分支（ADR-025）**：这个能力配了自有 Key 时，平台不再承担
    `provider_cost`，于是整条 `model_pricing` 的售价都不适用，
    换成 `pricing_rules.byok_unit_credits` 那档隐性成本
    （19_UnitEconomics.md §1.5：存储、CDN、审核、故障重跑计提）。
    不是免费——免费等于邀请用户拿自己的低价 Key 无限占用平台存储和计算，
    免费额度设计会被绕过。
    """
    cfg = await billing_service.rules(db)
    shape = _shape(task_type, payload)
    byok = await uses_own_key(db, org_id=org_id, capability=shape.capability)

    unit = cfg["byok_unit_credits"] if byok else await _platform_unit_price(db, shape, cfg)
    base = unit * shape.units
    if shape.retry_rule:
        # 废片同样要存、要审、要重跑，隐性成本一样发生 —— BYOK 也折
        base = apply_retry_factor(base, cfg[shape.retry_rule])

    if byok:
        # 不再叠 overhead_rate：那是平台档在上游成本之上加的隐性成本比例，
        # 而 byok_unit_credits 本身就是那笔隐性成本，加两遍是重复计提。
        return base
    return base + base * cfg["overhead_rate"] // 100


def apply_retry_factor(base: int, factor_x100: int) -> int:
    """把废片率折进成本。

    factor 存成整数百分比（250 = 2.5 次），避免浮点数进钱的计算。
    """
    return base * factor_x100 // 100


# ---------------------------------------------------------------- 同步 Agent 调用

#: 文本模型在 model_pricing 里的计价单位：每百万输出 token 多少 Credits。
_TOKENS_PER_UNIT = 1_000_000


async def estimate_agent_run(
    db: AsyncSession, *, budget_credits: int, org_id: uuid.UUID | None = None
) -> int:
    """一次同步 Agent 调用要预扣多少 Credits。

    取 spec 的 `budget_credits`——它本来就是"这个 Agent 单次运行的成本上限"
    （03_AgentSystem.md §6，由 runner 强制）。用它当预扣额，等于承诺
    "绝不会扣超过声明的上限"，而且不需要再引入一个新的数字：
    多一个可调的数就多一处会和真实成本对不上的地方。

    真实成本远低于这个上限，差额在 `settle` 时退回——先扣多了能退，
    扣少了就得平台垫。

    BYOK（ADR-025）：上游的钱用户自己付，平台只收隐性成本那一档。
    """
    if await uses_own_key(db, org_id=org_id, capability="text_generation"):
        cfg = await billing_service.rules(db)
        return int(cfg["byok_unit_credits"])
    return max(0, budget_credits)


async def text_run_cost(
    db: AsyncSession,
    *,
    model_id: str,
    tokens_out: int,
    org_id: uuid.UUID | None = None,
) -> int:
    """一次文本调用的实际成本，用于结算。

    只按输出 token 算：`model_pricing` 里文本模型的计价单位就是
    `mtoken_out`（每百万输出 token），表里没有输入 token 的档位，
    凭空造一个折算系数比少算这一头更糟。DeepSeek 的推理 token 计入
    输出预算，因此也自然被算了进来。

    **模型没定价时收 0 并告警。** 这是刻意的：估价那头拿不准要往高估
    （宁可拦下来），结算这头拿不准就不能往高收——把我们的配置缺失变成
    用户账单，比少收一笔钱严重得多。预扣已经把风险封顶，告警会让缺的
    那条定价被看见。
    """
    cfg = await billing_service.rules(db)
    if await uses_own_key(db, org_id=org_id, capability="text_generation"):
        return int(cfg["byok_unit_credits"])

    per_unit = await _unit_price(db, model_id)
    if per_unit is None:
        log.warning("pricing.model_not_priced", model_id=model_id, charged=0)
        return 0

    tokens = max(0, tokens_out)
    if tokens == 0:
        return 0

    # **不足 1 Credit 按 1 收，不向下取整。** 一次角色档案约 600 个输出 token，
    # 按 deepseek-chat 每百万 400 Credits 算只有 0.24——向下取整会让绝大多数
    # 文本调用收 0，那不是"便宜"，是系统性漏收。2026-08-19 真机验证时就是
    # 这么发现的：真实模型跑完，结算金额是 0。
    # 向上取整用整数写法，钱的计算里不允许浮点。
    base = -(-(per_unit * tokens) // _TOKENS_PER_UNIT)
    return base + base * cfg["overhead_rate"] // 100
