"""任务 ORM。模块私有。

`tasks.status` 是全系统执行状态的唯一权威（ADR-008）。
Agent 的推理过程、审核决策都记在别处，但"这件事跑到哪一步了"只认这张表。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, Sequence, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.core.models import OrgEntity

_OUTBOX_SEQ = Sequence("outbox_events_seq")

TASK_TYPES = (
    "mock.echo",  # S4 用于打通链路
    "mock.fail",  # S4 用于验证失败路径
    "image.generate",  # S6 起为真实出图
    "video.generate",
    "audio.tts",
    "timeline.render",
)

# 状态机（09_Database.md §8）：
#   queued → running → succeeded
#                    → failed      （可重试则回 queued）
#   queued/running → cancelled
TASK_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

# 只允许这些跃迁。写在这里而不是散在 service 里，
# 是为了让"能不能从 X 到 Y"有一处唯一答案。
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset({"succeeded", "failed", "cancelled", "queued"}),
    "succeeded": frozenset(),
    "failed": frozenset({"queued"}),  # 重试
    "cancelled": frozenset(),
}


class Task(OrgEntity):
    __tablename__ = "tasks"

    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # S6 接入真实 Provider 后回填
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # 金额一律 BIGINT 最小单位。S5 接 Ledger 后才真正写入。
    estimated_cost: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_cost: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    actual_cost: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # 防重复扣费的关键（08_BillingCredits.md §6）：Worker 重试复用同一个 key
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 是否计入废片率。只有真消耗了上游算力却没产出可用结果才算，
    # 是 19_UnitEconomics.md 成本模型的数据来源。
    counts_as_waste: Mapped[bool] = mapped_column(nullable=False, default=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_tasks_org_project_created", "org_id", "project_id", "created_at"),
        # 队列扫描：按状态 + 优先级 + 入队顺序
        Index("ix_tasks_status_priority", "status", "priority", "created_at"),
        Index("ix_tasks_created_at", "created_at"),
    )


class OutboxEvent(OrgEntity):
    """事务性发件箱（ADR-019）。

    状态变更与事件写入在同一个事务里，中继再异步投递到 Redis Stream。
    直接在提交后投递会出现"事务回滚了但事件发了"或"事务成功了但事件丢了"，
    两种都是用户可见的严重不一致。
    """

    __tablename__ = "outbox_events"

    # 独立序列保证投递顺序与产生顺序一致。
    # 不能靠 created_at 排序——同一毫秒内的多条事件顺序不确定；
    # 也不能靠 UUID 主键——它不单调。
    seq: Mapped[int] = mapped_column(
        BigInteger,
        _OUTBOX_SEQ,
        server_default=_OUTBOX_SEQ.next_value(),
        nullable=False,
        unique=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 中继只扫未投递的，用部分索引避免全表扫
        Index(
            "ix_outbox_pending",
            "seq",
            postgresql_where=mapped_column("published_at").is_(None),
        ),
    )
