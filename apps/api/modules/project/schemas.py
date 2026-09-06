from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.api.modules.project.models import ADAPTATION_MODES, ROUTE_TYPES


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


class ProjectStateOut(BaseModel):
    """项目的编排状态。

    在这条接口之前，`current_state_json` **没有任何 GET 接口**：它是
    ADR-008 里"状态的唯一权威"，却只在 `advance` / 审核决议的响应里带一次，
    刷新就没了。后果是前端只能从 `agent_runs` 反推阶段，而 ADR-029 的
    字段级 Patch 明明写进了 `current_state_json`——保存成功、刷新回旧值。

    所以这里把它整份透出。**只读**：写路径仍然只有编排器和 content 模块，
    这条不接受任何写入。

    `stage` 由 `agent.service.current_stage` 计算（含旧阶段名翻译），
    不是直接返回 JSONB 里那个字符串——库里存量项目还带着 `story` /
    `visual` 这类旧值。
    """

    project_id: uuid.UUID
    stage: str
    # 整份编排状态：source、router、五个阶段产出、stale_roles 都在里面。
    # 不裁剪成"只给产出"——裁剪等于在这里再定义一次哪些键算产出，
    # 而那张表在 orchestrator 里已经有一份了。
    current_state_json: dict[str, Any]
    stale_roles: list[str]
    # 用来判断本地缓存新不新。项目行的 updated_at，不是某个阶段的。
    updated_at: datetime


class StyleOptionOut(BaseModel):
    """画风目录里的一条。

    三套描述词也一并给出：门① 上用户选的是"画成什么样"，而唯一能让他
    判断的就是这三段词本身。只给一个名字等于让他盲选。
    """

    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    description: str
    character_tokens: str
    scene_tokens: str
    video_tokens: str


class ProjectLockVariablesIn(BaseModel):
    """门① 的四项决定里，落在这张表上的三项。

    每个字段都可以不传，含义是"这次不动它"——用户在门① 上通常只改一两项，
    要求前端把全部字段读出来再整份传回去，两个标签页同开就会互相覆盖
    （与 `ProjectModelPreferenceIn` 是同一条理由）。
    """

    style_key: str | None = Field(default=None, max_length=64)
    era: str | None = Field(default=None, max_length=40)
    region: str | None = Field(default=None, max_length=40)
    ethnicity: str | None = Field(default=None, max_length=60)
    adaptation_mode: str | None = Field(default=None, max_length=16)

    @field_validator("adaptation_mode")
    @classmethod
    def _known_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ADAPTATION_MODES:
            raise ValueError(f"未知的改编模式：{v}")
        return v


class ProjectLockVariablesOut(BaseModel):
    """项目级锁定变量的当前值 + 可选项。

    可选项（画风目录、改编模式）跟着一起给：这条接口的消费者就是门① 的
    确认界面，它需要的是"现在是什么"和"能改成什么"两件事。分成两条接口
    只会让界面多一次请求，且两次之间目录可能已经变了。
    """

    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    style_key: str
    era: str
    region: str
    ethnicity: str
    era_evidence: str
    adaptation_mode: str
    origin: str
    confirmed_at: datetime | None
    anchors_confirmed_at: datetime | None
    # 迁移补的、且从没被用户确认过。界面必须如实标注"历史项目，未经确认"——
    # 不标的话用户会以为这些值是他自己选的（ADR-037 第 6 条）。
    legacy_unconfirmed: bool

    style_options: list[StyleOptionOut] = Field(default_factory=list)
    adaptation_options: list[str] = Field(default_factory=list)


class ProjectPage(BaseModel):
    items: list[ProjectOut]
    next_cursor: str | None
