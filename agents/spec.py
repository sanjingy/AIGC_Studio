"""Agent 声明式定义。

**外部 Agent 只能是声明，不能是代码。**

别人给的 Agent 会跑在你的服务器上、花你的 Credits、能读你的项目数据。
允许它执行任意代码等于把服务器交出去
（13_CodexDevelopmentGuide.md §2 明令禁止）。

所以一个 Agent 就是这份 spec：提示词 + 输出 schema + 工具白名单 +
模型能力需求 + 步数与预算上限。运行时由平台的 runner 解释执行，
四重约束都在 runner 里强制，spec 本身没有任何逃逸手段。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 能力枚举来自 05_ModelRuntimeSpec.md §1。
# Agent 只声明"要什么能力"，不指定具体模型（ADR-002）。
CAPABILITIES = (
    "text_generation",
    "vision_understanding",
    "image_generation",
    "image_editing",
    "text_to_video",
    "image_to_video",
    "text_to_speech",
    "speech_to_text",
)

# 工具白名单的全集（03_AgentSystem.md §5）。
# spec 里只能引用这里已有的，不能凭空造一个。
KNOWN_TOOLS = (
    "create_character",
    "update_character",
    "create_scene",
    "create_shot",
    "update_shot",
    "request_approval",
    "search_asset",
    "create_task",
)

# Agent 角色的全集。Skill 层要按 role 校验阶段声明，
# 需要一份能被程序读到的清单——Literal 的成员没法直接遍历。
# 两者一致性由 tests/unit/test_agent_spec_sandbox.py 守住。
ROLES = ("router", "director", "story", "visual", "media", "qa")

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*\.v\d+$")


class AgentSpec(BaseModel):
    """一个 Agent 的完整定义。

    平台内置的和用户上传的走**同一套 schema 与同一套校验**——
    内置 Agent 不享受任何特权，这样才能保证外部 Agent 的约束是真实有效的，
    而不是"内置走后门、外部走检查"。
    """

    model_config = ConfigDict(extra="forbid")  # 多写字段直接报错，不静默忽略

    id: str = Field(description="形如 story.suspense.v1")
    name: str = Field(min_length=1, max_length=80)
    description: str = ""

    # 继承内置 Agent：只覆盖需要改的字段，其余沿用
    extends: str | None = None

    role: Literal["router", "director", "story", "visual", "media", "qa"]
    capability: str = "text_generation"

    prompt: str = Field(default="", description="系统提示词，支持 {变量} 占位")
    output_schema: str = Field(default="", description="输出必须匹配的 schema 名")

    tools: list[str] = Field(default_factory=list)

    # 可靠性约束（03_AgentSystem.md §6）。这四个是硬上限，
    # 用户 spec 只能调低不能调高——runner 会取两者的较小值。
    max_steps: int = Field(default=4, ge=1, le=20)
    timeout_seconds: int = Field(default=120, ge=5, le=900)
    max_output_tokens: int = Field(default=4096, ge=64, le=32_000)
    budget_credits: int = Field(default=200, ge=0, le=10_000)

    # 结构化输出校验失败时的重试次数
    schema_retries: int = Field(default=2, ge=0, le=5)

    source: Literal["builtin", "custom"] = "builtin"

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"Agent id 必须形如 story.suspense.v1，收到 {v!r}")
        return v

    @field_validator("capability")
    @classmethod
    def _known_capability(cls, v: str) -> str:
        if v not in CAPABILITIES:
            raise ValueError(f"未知能力 {v!r}，可选：{', '.join(CAPABILITIES)}")
        return v

    @field_validator("tools")
    @classmethod
    def _known_tools(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - set(KNOWN_TOOLS))
        if unknown:
            raise ValueError(
                f"引用了未注册的工具 {unknown}。工具必须由平台实现，"
                f"spec 不能凭空声明。可用：{', '.join(KNOWN_TOOLS)}"
            )
        return v

    @model_validator(mode="after")
    def _needs_schema_when_producing(self) -> AgentSpec:
        # Router / Director 的输出要被下游程序消费，必须结构化。
        # 让下游解析自然语言是 03_AgentSystem.md §2 明确禁止的。
        if self.role in ("router", "director") and not self.output_schema:
            raise ValueError(f"{self.role} 的输出必须声明 output_schema")
        return self

    def merged_with(self, base: AgentSpec) -> AgentSpec:
        """在 base 之上应用本 spec 的覆盖。

        约束只能收紧不能放宽：用户 spec 想把 max_steps 从 4 调到 100 是不行的。
        """
        data: dict[str, Any] = base.model_dump()
        mine = self.model_dump(exclude_unset=True)

        for tighten in ("max_steps", "timeout_seconds", "max_output_tokens", "budget_credits"):
            if tighten in mine:
                mine[tighten] = min(mine[tighten], data[tighten])

        # 工具只能是 base 的子集，不能新增
        if "tools" in mine:
            extra = sorted(set(mine["tools"]) - set(data["tools"]))
            if extra:
                raise ValueError(f"继承时不能新增工具：{extra}")

        data.update(mine)
        data["source"] = self.source
        return AgentSpec.model_validate(data)
