"""项目 ORM。模块私有。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import BigInteger, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.core.models import OrgEntity

# 与 01_ProductSpec.md §3 的路线枚举对齐
ROUTE_TYPES = (
    "NOVEL_TO_ANIME",
    "SCRIPT_TO_VIDEO",
    "IMAGE_TO_VIDEO",
    "VIDEO_EXPLAINER",
    "PRODUCT_VIDEO",
    "SHORT_VIDEO",
    "CUSTOM",
)

PROJECT_STATUSES = ("draft", "routing", "producing", "review", "completed", "archived")


class Project(OrgEntity):
    __tablename__ = "projects"

    owner_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    route_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")

    # Agent 编排的长期记忆（03_AgentSystem.md §3）。S7 才真正写入。
    current_state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # 成本熔断（19_UnitEconomics.md §7）。金额一律 BIGINT 最小单位。
    budget_cap_credits: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    spent_credits: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        # 列表页的主查询：本租户 + 未删除 + 按时间倒序
        Index("ix_projects_org_created", "org_id", "created_at"),
    )
