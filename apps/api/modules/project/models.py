"""项目 ORM。模块私有。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, Text, Uuid
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


# 门① 要用户一次定下的四件事里，有三件在这张表上（第四件是情节目录，
# 它已经是 `current_state_json["plot_index"]` 的产出，不重复存一份）。
#
# ADR-037 第 2 条把画风、时代背景/人种、改编模式合并成同一道门，理由是
# 它们**在信息上是同时可决的**——都只依赖原文与情节目录，不依赖彼此。
ADAPTATION_MODES = ("adapt", "rewrite")

# 这一行是怎么来的。三个值，含义互斥：
#
#   detected  系统按原文证据判定后写下的一版，等着用户在门① 确认或改
#   confirmed 用户在门① 亲自确认过（`confirmed_at` 同时非空）
#   migrated  ADR-037 上线时由迁移补的。这类项目**已经越过门① 的位置**，
#             按 ADR-037 第 6 条不得被退回去重新确认，所以它们的画风与
#             锚点是按缺省值补齐的，从来没有人看过一眼。
#
# 判据必须能区分"用户确认过的"和"迁移补的"，否则界面没法如实标注
# "历史项目，未经确认"，而用户会以为这些值是他自己选的。
LOCK_ORIGINS = ("detected", "confirmed", "migrated")


class ProjectLockVariables(OrgEntity):
    """项目级锁定变量：一次锁定、后面所有阶段都要读的全局量。

    **不放进 `current_state_json`**，尽管那里是编排状态的唯一权威（ADR-008）。
    两者的生命周期不同：`current_state_json` 是"跑到哪了 + 每一步的产出"，
    会被打回、被重跑、被字段级编辑整份换掉；锁定变量是"这个项目是什么"，
    一旦确认就要在所有重跑之间存活。塞进同一份 JSONB 里，一次
    `changes_requested` 重跑就可能把用户确认过的画风连带冲掉，而且
    "确认过没有"这件事没有地方记时间和人。

    也**不做成 `projects` 的几列**：这里要记确认时间、确认人、来源三样
    审计信息，加到 `projects` 上会让那张表变成一个什么都往上挂的宽表，
    而这几列只有编排门用得上。
    """

    __tablename__ = "project_lock_variables"

    # 一个项目一行。唯一索引不是优化，是约束：两行锁定变量意味着
    # "这个项目的画风是什么"有两个答案。
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    # 指向 `style_catalog.key`。**不加外键**：与 `selected_skill_id` 的写法
    # 一致，跨模块的引用完整性由 service 层保证，数据库层不把两个模块焊死。
    style_key: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # 时代背景与国籍人种。空串 = 还没判定，**不是**"就按本国算"——
    # ADR-037 明写判定结果不得默认套用本国，所以这里没有兜底值可写。
    era: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    region: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    ethnicity: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    # 判定依据的原文线索。没有依据的判定等于猜，用户在门① 要能看见它。
    era_evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # 改编 / 洗稿。整条流水线上**唯一一个用户必须做的分支选择**，
    # 其余步骤都是自动的。决定 `screenplay` 阶段跑哪种改编。
    adaptation_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="adapt")

    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="detected")

    # 门①。非空 = 用户亲自确认过这四件事。
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # 门③。非空 = 用户一次性确认过全部空间锚点卡。
    # 与门① 分开记：两道门问的是完全不同的东西，合成一个时间戳就分不清
    # "画风确认过但锚点没有"这种正常中间态。
    anchors_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("uq_project_lock_variables_project", "project_id", unique=True),)

    @property
    def legacy_unconfirmed(self) -> bool:
        """迁移补的、且从没被用户确认过。界面要如实标注"历史项目，未经确认"。"""
        return self.origin == "migrated" and self.confirmed_at is None
