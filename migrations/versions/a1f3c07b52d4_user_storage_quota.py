"""user storage quota rule and owner index

资产库配额（1 GiB / 用户）走 `pricing_rules`，不写成 Python 常量。

它和废片率、熔断阈值是同一类东西：一个会变的运营数字。
写进代码就意味着"调一次容量要发一次版"，而容量一定会随成本结构调整
（19_UnitEconomics.md：对象存储成本占比会随成片量线性上升）。

同时给 assets 补一个 (org_id, owner_user_id, created_at) 索引：
配额汇总与"我的资产库"列表都按这三列查，缺索引时随着资产表变大
每次上传都要全表扫一遍。

Revision ID: a1f3c07b52d4
Revises: 62c955d6f428
Create Date: 2026-08-19

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f3c07b52d4"
down_revision: str | None = "62c955d6f428"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 1 GiB = 1073741824 字节。单位是字节而不是 MB——
# 和 assets.size_bytes 同单位，比较时不需要任何换算，也就不会换算错。
_RULES = [
    ("user_storage_quota_bytes", 1_073_741_824, "单用户资产库容量上限，字节（1 GiB）"),
]


def upgrade() -> None:
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

    op.create_index(
        "ix_assets_org_owner_created",
        "assets",
        ["org_id", "owner_user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_assets_org_owner_created", table_name="assets")
    keys = ", ".join(f"'{k}'" for k, _, _ in _RULES)
    op.execute(f"DELETE FROM pricing_rules WHERE key IN ({keys})")
