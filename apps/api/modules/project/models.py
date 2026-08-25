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

    # 用户选中的 Skill（ADR-026）。指向 org_skills.id，但**不加外键**：
    # 与 owner_user_id 的写法一致，跨模块的引用完整性由 service 层保证，
    # 数据库层不把两个模块的表焊死。选了也暂时不生效——运行时未接线，
    # 界面必须如实标注。
    selected_skill_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)

    # 项目级模型覆盖（ADR-024）：capability → model_id。
    # Agent 仍然只声明 capability，覆盖发生在这一层，不在 Agent 层。
    # 可空：存量项目迁移后是 NULL，读的时候一律 `... or {}`。
    model_preference: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, default=dict
    )

    # 成本熔断（19_UnitEconomics.md §7）。金额一律 BIGINT 最小单位。
    budget_cap_credits: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    spent_credits: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        # 列表页的主查询：本租户 + 未删除 + 按时间倒序
        Index("ix_projects_org_created", "org_id", "created_at"),
    )

    @property
    def stale_roles(self) -> list[str]:
        """上游被改过、因而停留在旧版本的阶段产出。

        记账由 Agent 编排写入（`modules/agent/orchestrator.py` 的过期记账）。
        这里只做透出：读一个已经在内存里的 JSONB 键，不额外查库，
        也不反过来依赖 agent 模块——真相仍只有 `current_state_json` 一处。
        """
        raw = (self.current_state_json or {}).get("stale_roles")
        if not isinstance(raw, list):
            return []
        return [r for r in raw if isinstance(r, str)]
