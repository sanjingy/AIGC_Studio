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


class ProjectModelPreferenceIn(BaseModel):
    """按能力设置项目级模型覆盖（ADR-024）。

    `model_id=None` 是"清掉这个能力的偏好，回到 Gateway 的默认优先级"，
    不是"没填"——所以这个字段必须显式出现在请求体里。
    合法性（这个 id 在不在目录里）由 service 层按 `gateway/catalog.py` 校验，
    不在 schema 里再抄一份枚举。
    """

    capability: str = Field(min_length=1, max_length=64)
    model_id: str | None = Field(default=None, max_length=128)

    @field_validator("model_id")
    @classmethod
    def _trim_model(cls, v: str | None) -> str | None:
        if v is None:
            return None
        model_id = v.strip()
        # 空串按"清掉偏好"处理：前端把下拉框拨回"跟随默认"时传空串还是
        # null 取决于表单实现，两种都该得到同一个结果。
        return model_id or None


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
    # 项目级模型覆盖（ADR-024）：capability → model_id。
    # 存量项目这一列是 NULL，统一成空字典再给前端——
    # 让前端每次都写 `?? {}` 是把后端的历史包袱转嫁出去。
    model_preference: dict[str, str] = Field(default_factory=dict)
    # 上游被修订后已经过期、需要同步的阶段产出。
    # 放在项目响应上而不是只放在 revise 的返回值里：它是项目的当前状态，
    # 刷新页面后还要能读到（ADR-008，状态的唯一权威在库里）。
    stale_roles: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_validator("model_preference", mode="before")
    @classmethod
    def _no_null_preference(cls, v: object) -> object:
        return v or {}


class ProjectPage(BaseModel):
    items: list[ProjectOut]
    next_cursor: str | None
