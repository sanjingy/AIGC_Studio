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
