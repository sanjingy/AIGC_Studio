from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.api.modules.task.models import TASK_TYPES


class TaskCreateIn(BaseModel):
    type: str
    project_id: uuid.UUID | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=100, ge=1, le=1000)

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in TASK_TYPES:
            raise ValueError(f"未知的任务类型：{v}")
        return v


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    type: str
    status: str
    progress: int
    attempt: int
    max_attempts: int
    error_code: str | None
    estimated_cost: int
    actual_cost: int
    counts_as_waste: bool
    output_json: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class TaskPage(BaseModel):
    items: list[TaskOut]
    next_cursor: str | None
