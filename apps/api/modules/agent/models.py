"""Agent 运行记录与审核门。模块私有。

职责边界（ADR-008）：
    tasks         执行状态的唯一权威
    agent_runs    推理过程，只用于审计与回放，**不参与状态判断**
    approvals     人工决策

三者不重叠。任何"从 agent_runs 推断当前进度"的代码都是错的。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.core.models import OrgEntity

AGENT_RUN_STATUSES = ("running", "succeeded", "failed")

# 默认三道阻塞门（01_ProductSpec.md §5）
GATES = ("setup", "storyboard", "final")
APPROVAL_STATUSES = ("pending", "approved", "changes_requested", "rejected")


class AgentRun(OrgEntity):
    __tablename__ = "agent_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")

    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_agent_runs_project_created", "project_id", "created_at"),)


class AgentStep(OrgEntity):
    """单次模型调用的完整记录。

    `resolved_prompt` 必须存全文（17_ConsistencyEngine.md §7）。
    没有它，"为什么这一步输出崩了"永远查不清，也没法复现。
    """

    __tablename__ = "agent_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # llm / tool / validate

    resolved_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("ix_agent_steps_run_index", "run_id", "step_index"),)


class Approval(OrgEntity):
    """审核门。用户的决策记在这里，不记在 agent_runs 里。"""

    __tablename__ = "approvals"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    gate: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        # 同一个门同时只能有一个待处理项
        Index(
            "uq_approvals_pending_gate",
            "project_id",
            "gate",
            unique=True,
            postgresql_where=mapped_column("status").in_(["pending"]),
        ),
        Index("ix_approvals_project_created", "project_id", "created_at"),
    )
