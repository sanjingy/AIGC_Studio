from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AdvanceIn(BaseModel):
    user_input: str = Field(default="", max_length=20_000)


class AdvanceOut(BaseModel):
    stage: str
    ran_role: str | None
    gate_opened: str | None
    blocked: bool
    output: dict[str, Any] | None


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    gate: str
    status: str
    payload_json: dict[str, Any]
    comment: str | None
    created_at: datetime
    resolved_at: datetime | None


class ApprovalDecisionIn(BaseModel):
    decision: Literal["approved", "changes_requested", "rejected"]
    comment: str | None = Field(default=None, max_length=1000)


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: str
    role: str
    status: str
    model_id: str | None
    tokens_in: int
    tokens_out: int
    attempts: int
    error_code: str | None
    output_json: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None


class ReviseIn(BaseModel):
    target_role: Literal["plot_index", "screenplay", "characters", "scenes", "storyboard"]
    instruction: str = Field(min_length=1, max_length=2000)


class ReviseOut(BaseModel):
    target_role: str
    revision: int
    # 实际改了什么，由后端 diff 得出——不是模型自述。
    # 模型说只改了一处、diff 说改了七处时，以 diff 为准。
    changed_fields: list[str]
    output: dict[str, Any]
    # 基于旧版生成的下游产出，需要重新生成才会同步
    stale_roles: list[str]


class ConversationMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    target_role: str
    author: str
    text: str
    revision: int
    changed_fields: list[str]
    # assistant 消息指向产出这一版的那次运行，用于回看提示词与原始输出
    run_id: uuid.UUID | None
    created_at: datetime


class AgentSpecOut(BaseModel):
    id: str
    name: str
    description: str
    role: str
    source: str
    tools: list[str]
    max_steps: int
    budget_credits: int


class RegistryOut(BaseModel):
    agents: list[AgentSpecOut]
    errors: dict[str, str]
