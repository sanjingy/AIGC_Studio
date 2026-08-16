from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.api.modules.project.models import ROUTE_TYPES


class ProjectCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    route_type: str | None = None
    budget_cap_credits: int | None = Field(default=None, ge=0)

    @field_validator("title")
    @classmethod
    def _trim(cls, v: str) -> str:
        title = v.strip()
        if not title:
            raise ValueError("标题不能为空")
        return title

    @field_validator("route_type")
    @classmethod
    def _known_route(cls, v: str | None) -> str | None:
        if v is not None and v not in ROUTE_TYPES:
            raise ValueError(f"未知的创作路线：{v}")
        return v


class ProjectUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    route_type: str | None = None
    budget_cap_credits: int | None = Field(default=None, ge=0)

    @field_validator("route_type")
    @classmethod
    def _known_route(cls, v: str | None) -> str | None:
        if v is not None and v not in ROUTE_TYPES:
            raise ValueError(f"未知的创作路线：{v}")
        return v


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    owner_user_id: uuid.UUID
    title: str
    route_type: str | None
    status: str
    budget_cap_credits: int | None
    spent_credits: int
    created_at: datetime
    updated_at: datetime


class ProjectPage(BaseModel):
    items: list[ProjectOut]
    next_cursor: str | None
