"""Agent 的结构化输出 schema。

**Agent 的输出必须结构化，禁止让下游解析自然语言**
（03_AgentSystem.md §2）。自然语言解析在 99 次里能过，
第 100 次模型换个措辞就崩，而且崩得毫无征兆。

spec 里的 `output_schema` 就是这里的类名。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROUTE_TYPES = Literal[
    "NOVEL_TO_ANIME",
    "SCRIPT_TO_VIDEO",
    "IMAGE_TO_VIDEO",
    "VIDEO_EXPLAINER",
    "PRODUCT_VIDEO",
    "SHORT_VIDEO",
    "CUSTOM",
]

SHOT_SIZES = Literal["远景", "全景", "中景", "近景", "特写", "过肩"]


class _Strict(BaseModel):
    # 多余字段直接报错。模型幻想出一个字段时要立刻发现，
    # 而不是静默丢掉然后在下游表现为"数据莫名其妙缺了"。
    model_config = ConfigDict(extra="forbid")


class RouterDecision(_Strict):
    route: ROUTE_TYPES
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(max_length=500)

    # 生产参数。金额由系统按这些参数算，Router 不输出任何金额——
    # 让模型估价必然不准，不准的估价会变成错误的预扣。
    estimated_duration_seconds: int = Field(ge=5, le=3600)
    estimated_shots: int = Field(ge=1, le=1000)
    tier: Literal["economy", "standard", "premium"] = "standard"

    requires_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=300)


class StoryAct(_Strict):
    index: int = Field(ge=1)
    title: str = Field(max_length=60)
    summary: str = Field(max_length=600)
    mood: str = Field(max_length=40)


class StoryOutline(_Strict):
    title: str = Field(max_length=80)
    logline: str = Field(max_length=200)
    acts: list[StoryAct] = Field(min_length=1, max_length=12)
    central_conflict: str = Field(max_length=300)


class CharacterDesign(_Strict):
    """外貌字段拆开而不是一段自由文本。

    一致性引擎要拿这些字段去构造条件化提示词，
    一段散文没法稳定地提取出"发色是什么"。
    """

    ref: str = Field(pattern=r"^[a-z][a-z0-9_]{1,30}$", description="镜头里引用它的 id")
    name: str = Field(max_length=40)
    age_range: str = Field(max_length=20)
    hair: str = Field(max_length=80)
    eyes: str = Field(max_length=60)
    face: str = Field(max_length=100)
    build: str = Field(max_length=60)
    outfit: str = Field(max_length=160)
    distinctive: str = Field(default="", max_length=120)


class SceneDesign(_Strict):
    ref: str = Field(pattern=r"^[a-z][a-z0-9_]{1,30}$")
    name: str = Field(max_length=40)
    setting: str = Field(max_length=300)
    lighting: str = Field(max_length=100)


class ShotDesign(_Strict):
    index: int = Field(ge=1)
    scene_ref: str
    character_refs: list[str] = Field(default_factory=list, max_length=6)
    shot_size: SHOT_SIZES
    # 只描述画面内容。风格词由系统统一注入——
    # Agent 自行编写风格词是画风漂移的头号来源
    # （17_ConsistencyEngine.md §4）。
    content: str = Field(max_length=300)
    dialogue: str = Field(default="", max_length=200)


class VisualPlan(_Strict):
    characters: list[CharacterDesign] = Field(min_length=1, max_length=20)
    scenes: list[SceneDesign] = Field(min_length=1, max_length=30)
    shots: list[ShotDesign] = Field(min_length=1, max_length=400)


class DirectorPlan(_Strict):
    next_role: Literal["story", "visual", "media", "qa", "done"]
    gate: Literal["setup", "storyboard", "final"] | None = None
    reason: str = Field(max_length=300)


class QAIssue(_Strict):
    severity: Literal["info", "warn", "error"]
    code: str = Field(max_length=60)
    message: str = Field(max_length=300)


class QAReport(_Strict):
    passed: bool
    issues: list[QAIssue] = Field(default_factory=list)


SCHEMAS: dict[str, type[BaseModel]] = {
    "RouterDecision": RouterDecision,
    "StoryOutline": StoryOutline,
    "VisualPlan": VisualPlan,
    "DirectorPlan": DirectorPlan,
    "QAReport": QAReport,
}


def resolve(name: str) -> type[BaseModel]:
    try:
        return SCHEMAS[name]
    except KeyError as exc:
        raise LookupError(
            f"未知的 output_schema {name!r}。可用：{', '.join(sorted(SCHEMAS))}"
        ) from exc
