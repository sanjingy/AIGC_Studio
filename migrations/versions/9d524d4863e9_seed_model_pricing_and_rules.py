"""seed model pricing and rules

价格数据入库，代码里不出现任何价格常量（ADR-014）。

单价采集自 19_UnitEconomics.md（2026-08-16 采集），
金额一律 Credits 最小单位：1 Credit = ¥0.01。

上游调价时改这张表，不改代码、不发版——
DeepSeek 于 2026-08-17 高峰输出价上涨 350%，就是靠这条设计扛住的。

Revision ID: 9d524d4863e9
Revises: 3df43da2941d
Create Date: 2026-08-17

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d524d4863e9"
down_revision: str | None = "3df43da2941d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (model_id, unit, provider_cost, credit_price)
# provider_cost = 我们付给上游的；credit_price = 卖给用户的。
# 两者分离是硬要求（08_BillingCredits.md §4），毛利目标 ≥45%。
_PRICING = [
    # --- 文本。按百万 token 计价，换算成 Credits ---
    # DeepSeek 2026-08-17 新价，高峰时段
    ("deepseek-chat", "mtoken_out", 200, 400),
    ("deepseek-v4-flash", "mtoken_out", 900, 1800),
    ("deepseek-v4-pro", "mtoken_out", 2700, 5400),
    # --- 图像。按张计价 ---
    # 万相 flash 约 ¥0.2/张 = 20 Credits
    ("wan2.2-t2i-flash", "image", 20, 40),
    ("wan2.2-t2i-plus", "image", 60, 120),
]

_RULES = [
    ("task_cost_cap", 2_000, "单任务成本上限 ¥20，防参数写错烧钱"),
    ("org_daily_cap", 50_000, "单租户日消费上限 ¥500"),
    ("project_budget_multiplier", 130, "项目预算宽容度，百分比"),
    ("image_retry_factor", 250, "图片废片率 ×100，待 M1 验证切片回填"),
    ("video_retry_factor", 150, "视频废片率 ×100，待 M1 验证切片回填"),
    ("overhead_rate", 3, "隐性成本占比，百分比"),
    ("welcome_grant", 500, "注册体验额度 ¥5"),
]


def upgrade() -> None:
    pricing = sa.table(
        "model_pricing",
        sa.column("model_id", sa.String),
        sa.column("unit", sa.String),
        sa.column("provider_cost", sa.BigInteger),
        sa.column("credit_price", sa.BigInteger),
    )
    op.bulk_insert(
        pricing,
        [
            {
                "model_id": m,
                "unit": u,
                "provider_cost": cost,
                "credit_price": price,
            }
            for m, u, cost, price in _PRICING
        ],
    )

    rules = sa.table(
        "pricing_rules",
        sa.column("key", sa.String),
        sa.column("value", sa.BigInteger),
        sa.column("note", sa.Text),
    )
    op.bulk_insert(
        rules,
        [{"key": k, "value": v, "note": n} for k, v, n in _RULES],
    )


def downgrade() -> None:
    keys = ", ".join(f"'{k}'" for k, _, _ in _RULES)
    models = ", ".join(f"'{m}'" for m, _, _, _ in _PRICING)
    op.execute(f"DELETE FROM pricing_rules WHERE key IN ({keys})")
    op.execute(f"DELETE FROM model_pricing WHERE model_id IN ({models})")
