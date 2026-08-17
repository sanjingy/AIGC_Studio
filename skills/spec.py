"""Skill 声明式定义。

Skill = 生产模板，不是提示词集合（ADR-006）。它回答的是
"这条生产线由哪些阶段组成、哪些阶段停下来等人、每个阶段的产出落到哪里"。

**和 Agent 一样，Skill 只能是声明，不能是代码**（ADR-020）。
理由完全相同：别人给的 Skill 会跑在我们的服务器上、花用户的 Credits、
读用户的项目数据，还会往用户的本地磁盘写文件。允许它执行任意代码
等于把服务器交出去，允许它自由拼接路径等于把用户的磁盘交出去。

所以一个 Skill 就是这份 spec：阶段序列 + 门 + 审查策略 + 导出布局。
每个确定性阶段只能引用 `KNOWN_HANDLERS` 里已经由平台实现的处理器，
每个导出路径都要过 `_safe_relative_path`。spec 本身没有任何逃逸手段。
"""

from __future__ import annotations

import re
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents.schemas import ROUTE_TYPES
from agents.spec import CAPABILITIES, ROLES

# 确定性处理器白名单。Skill 只能引用平台已实现的，不能凭空声明一个。
# 这和 AgentSpec 的工具白名单是同一条约束：spec 描述"用哪个能力"，
# 不描述"能力是什么"。
KNOWN_HANDLERS = (
    "script.estimate_production",  # 情节目录 → 场景数/字数 → 规模与报价
    "tts.synthesize_lines",  # 台词 → 真实音频时长（ADR-011 音频优先）
    "consistency.lock_style",  # 锁定项目级风格，之后冻结
    "consistency.compose_portrait_prompts",
    "consistency.compose_scene_prompts",
    "consistency.compose_shot_prompts",
    "consistency.freeze_characters",  # 角色资产包冻结
    "storyboard.assign_durations",  # 按真实音频时长回填每镜时长
    "storyboard.batch_shots",  # 15 秒批次切分
    "storyboard.resolve_anchors",  # 空间锚点卡：固定层 + 动态层推算
    "timeline.assemble",  # 音频优先装配
    "timeline.render",  # ffmpeg
    "export.sync_local",  # 镜像到用户指定的本地目录
)

# 阻塞门的全集。门开得越多用户流失越快，所以这里是一份**收敛的清单**，
# 不是"想加就加"。新增一道门要有 ADR。
KNOWN_GATES = ("script", "assets", "anchor", "storyboard", "final")

STAGE_KINDS = ("agent", "compute", "generate", "gate")

# 审查模式（用户在项目级选，Skill 声明默认值和是否允许全自动）
REVIEW_MODES = ("manual", "assisted", "auto")

_SKILL_ID_RE = re.compile(r"^skill\.[a-z][a-z0-9_]*(\.[a-z0-9_]+)*\.v\d+$")
_STAGE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

# 导出目录名允许中文——用户要打开这个文件夹自己看，`03_assets` 比
# `stage_3` 好认。但字符集必须收窄，且不允许任何形式的向上穿越。
_PATH_SEGMENT_RE = re.compile(r"^[\w一-鿿][\w一-鿿\-. ]{0,39}$")
_MAX_EXPORT_DEPTH = 3


def _safe_relative_path(value: str) -> str:
    """校验导出子目录。

    Skill 声明的路径会被拼到用户指定的本地目录下并真的写文件。
    一个 `../../.ssh` 就能让第三方 Skill 往用户主目录写东西，
    所以这里按段白名单校验，不做"替换掉危险字符"式的清洗——
    清洗永远漏，白名单不会。
    """
    if not value:
        return value
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"导出路径必须是相对路径：{value!r}")
    if "\\" in value:
        raise ValueError(f"导出路径统一用 /，不要用反斜杠：{value!r}")

    segments = [s for s in value.split("/") if s]
    if not segments:
        raise ValueError(f"导出路径为空：{value!r}")
    if len(segments) > _MAX_EXPORT_DEPTH:
        raise ValueError(f"导出路径最多 {_MAX_EXPORT_DEPTH} 层：{value!r}")

    for seg in segments:
        if seg in (".", ".."):
            raise ValueError(f"导出路径不允许 . 或 ..：{value!r}")
        if not _PATH_SEGMENT_RE.match(seg):
            raise ValueError(f"导出路径含不允许的字符：{seg!r}")

    return "/".join(segments)


class ReviewPolicy(BaseModel):
    """出图/出视频的审查策略。

    三档的区别只在"谁来判不合格"，重生逻辑是同一套。
    """

    model_config = ConfigDict(extra="forbid")

    default: Literal["manual", "assisted", "auto"] = "assisted"

    # 允不允许用户切到全自动。阈值没标定完之前，某些阶段应该是 false——
    # 全自动跑在一个不可信的阈值上，等于自动烧钱。
    allow_auto: bool = True

    metric: str = Field(default="", max_length=40, description="auto 档的判定指标")

    # **阈值只写键名，不写数值。** 数值进库，改数不改码（ADR-014 同源理由）。
    # 废片率和相似度阈值都还没标定完，写死在 YAML 里等于把待定的数字
    # 冻进了发布物。
    threshold_key: str = Field(default="", max_length=60)

    # 自动重试的硬上限。一张图无限重试就是无限烧钱。
    max_attempts: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def _auto_needs_a_judge(self) -> ReviewPolicy:
        if self.allow_auto and not (self.metric and self.threshold_key):
            raise ValueError("允许全自动就必须声明 metric 和 threshold_key，否则没有判定依据")
        if self.default == "auto" and not self.allow_auto:
            raise ValueError("默认值是 auto 却不允许 auto")
        if self.default == "assisted" and not self.metric:
            # 半自动 = AI 先筛、人只看筛出来的。没有指标就没有"筛"，
            # 它会退化成一个名字好听的手动模式。
            raise ValueError("assisted 需要 metric，否则没有可筛的依据")
        return self


class Stage(BaseModel):
    """生产线上的一步。

    四种 kind 各自需要不同的字段，用一个扁平模型 + 校验器表达，
    而不是四个类——YAML 作者读到的是一张表，不该被类型体操绊住。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["agent", "compute", "generate", "gate"]

    # kind=agent
    role: str = ""
    agent: str = Field(default="", description="钉死到具体 Agent id；留空则按 role 取默认")

    # kind=compute
    handler: str = ""

    # kind=generate
    capability: str = ""
    review: ReviewPolicy | None = None

    # kind=gate
    gate: str = ""
    required: bool = Field(
        default=False,
        description="true 表示用户不能关掉这道门",
    )

    # 通用
    revisable: bool = Field(
        default=False,
        description="产出后允许用户用聊天修订。只对 agent 阶段有意义",
    )
    export: str = Field(default="", description="产出落到本地目录的哪个子目录")

    @field_validator("id")
    @classmethod
    def _valid_stage_id(cls, v: str) -> str:
        if not _STAGE_ID_RE.match(v):
            raise ValueError(f"阶段 id 必须是小写下划线短标识，收到 {v!r}")
        return v

    @field_validator("export")
    @classmethod
    def _valid_export(cls, v: str) -> str:
        return _safe_relative_path(v)

    @model_validator(mode="after")
    def _fields_match_kind(self) -> Stage:
        if self.kind == "agent":
            if not self.role:
                raise ValueError(f"阶段 {self.id!r}：agent 阶段必须声明 role")
            if self.role not in ROLES:
                raise ValueError(f"阶段 {self.id!r}：未知 role {self.role!r}，可选 {ROLES}")
        elif self.kind == "compute":
            if self.handler not in KNOWN_HANDLERS:
                raise ValueError(
                    f"阶段 {self.id!r}：未注册的处理器 {self.handler!r}。"
                    f"处理器必须由平台实现，spec 不能凭空声明"
                )
        elif self.kind == "generate":
            if self.capability not in CAPABILITIES:
                raise ValueError(f"阶段 {self.id!r}：未知能力 {self.capability!r}")
            if self.review is None:
                raise ValueError(f"阶段 {self.id!r}：生成阶段必须声明 review 策略")
        elif self.kind == "gate" and self.gate not in KNOWN_GATES:
            raise ValueError(
                f"阶段 {self.id!r}：未知的门 {self.gate!r}，可选 {KNOWN_GATES}。新增门需要 ADR"
            )

        # 字段串味检查：写错 kind 时要立刻报错，而不是静默忽略半份配置
        wrong = {
            "agent": ("handler", "capability", "gate"),
            "compute": ("role", "agent", "capability", "gate"),
            "generate": ("role", "agent", "handler", "gate"),
            "gate": ("role", "agent", "handler", "capability"),
        }[self.kind]
        for field in wrong:
            if getattr(self, field):
                raise ValueError(f"阶段 {self.id!r}：kind={self.kind} 不该声明 {field}")

        if self.kind != "generate" and self.review is not None:
            raise ValueError(f"阶段 {self.id!r}：只有 generate 阶段有 review")
        if self.kind == "gate" and self.export:
            raise ValueError(f"阶段 {self.id!r}：门不产出内容，不该声明 export")
        if self.revisable and self.kind != "agent":
            raise ValueError(
                f"阶段 {self.id!r}：只有 agent 阶段可以聊天修订。"
                f"生成阶段的返工走 review，确定性阶段没有可修订的东西"
            )
        return self


class InputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Literal["required", "optional", "forbidden"] = "optional"
    files: Literal["required", "optional", "forbidden"] = "optional"
    accept_extensions: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("accept_extensions")
    @classmethod
    def _lower_dotted(cls, v: list[str]) -> list[str]:
        for ext in v:
            if not re.match(r"^\.[a-z0-9]{1,8}$", ext):
                raise ValueError(f"扩展名必须形如 .txt，收到 {ext!r}")
        return v

    @model_validator(mode="after")
    def _extensions_need_files(self) -> InputSpec:
        if self.accept_extensions and self.files == "forbidden":
            raise ValueError("声明了 accept_extensions 却禁止上传文件")
        return self


class ModelPolicy(BaseModel):
    """用户能改哪些模型，以及哪些组合不许出现。

    ADR-002 说 Agent 不选模型——这条不变。用户的选择落在**项目级**，
    按 capability 覆盖，仍由 Gateway 解析成具体模型。
    """

    model_config = ConfigDict(extra="forbid")

    user_selectable: list[str] = Field(default_factory=list, max_length=8)

    # 这些 role 禁用推理模型。deepseek-v4-* 的思考 token 计入输出预算，
    # 用在分类和结构化抽取上会返回空内容且不报错——踩过两次。
    no_reasoning_roles: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("user_selectable")
    @classmethod
    def _known_capabilities(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - set(CAPABILITIES))
        if unknown:
            raise ValueError(f"未知能力 {unknown}")
        return v

    @field_validator("no_reasoning_roles")
    @classmethod
    def _known_roles(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - set(ROLES))
        if unknown:
            raise ValueError(f"未知 role {unknown}")
        return v


class ConsistencyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_tier: Literal["L0", "L1", "L2", "L3"] = "L1"
    protagonist_tier: Literal["L0", "L1", "L2", "L3"] = "L1"


class ExportPolicy(BaseModel):
    """本地资产目录。

    对象存储仍是唯一真相，本地目录是镜像（ADR-022）。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    layout_version: int = Field(default=1, ge=1, le=99)


class SkillSpec(BaseModel):
    """一条生产线的完整定义。

    平台内置的和用户上传的走同一套 schema 与同一套校验。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="形如 skill.novel_to_anime.v1")
    name: str = Field(min_length=1, max_length=80)
    description: str = ""

    status: Literal["draft", "active", "deprecated"] = "draft"
    route: str

    input: InputSpec = Field(default_factory=InputSpec)
    stages: list[Stage] = Field(min_length=1, max_length=40)

    model_policy: ModelPolicy = Field(default_factory=ModelPolicy)
    consistency: ConsistencyPolicy = Field(default_factory=ConsistencyPolicy)
    export: ExportPolicy = Field(default_factory=ExportPolicy)

    source: Literal["builtin", "custom"] = "builtin"

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _SKILL_ID_RE.match(v):
            raise ValueError(f"Skill id 必须形如 skill.novel_to_anime.v1，收到 {v!r}")
        return v

    @field_validator("route")
    @classmethod
    def _known_route(cls, v: str) -> str:
        routes = get_args(ROUTE_TYPES)
        if v not in routes:
            raise ValueError(f"未知路线 {v!r}，可选：{', '.join(routes)}")
        return v

    @model_validator(mode="after")
    def _pipeline_is_sane(self) -> SkillSpec:
        seen: set[str] = set()
        for stage in self.stages:
            if stage.id in seen:
                raise ValueError(f"阶段 id 重复：{stage.id!r}")
            seen.add(stage.id)

        gates = [s for s in self.stages if s.kind == "gate"]
        if not any(g.required for g in gates):
            # 一条全自动、任何一步都不给人看的生产线，出了问题用户
            # 只能在成片阶段才发现，那时候钱已经花完了。
            raise ValueError("至少要有一道 required 的门")

        dup_gates = [g.gate for g in gates if [x.gate for x in gates].count(g.gate) > 1]
        if dup_gates:
            raise ValueError(f"同一道门声明了多次：{sorted(set(dup_gates))}")

        return self

    def stage(self, stage_id: str) -> Stage:
        for s in self.stages:
            if s.id == stage_id:
                return s
        raise LookupError(f"Skill {self.id!r} 里没有阶段 {stage_id!r}")

    def agent_ids(self) -> list[str]:
        """本 Skill 钉死引用的 Agent id。"""
        return [s.agent for s in self.stages if s.kind == "agent" and s.agent]

    def export_dirs(self) -> list[str]:
        return sorted({s.export for s in self.stages if s.export})

    def effective_gates(self, *, review_mode: str = "assisted") -> list[str]:
        """按审查模式算出实际会停下来的门。

        全自动模式下只剩 required 的门——这就是"门的数量是策略，不是常量"。
        """
        if review_mode == "auto":
            return [s.gate for s in self.stages if s.kind == "gate" and s.required]
        return [s.gate for s in self.stages if s.kind == "gate"]


def load_spec(raw: dict[str, Any], *, source: str = "builtin") -> SkillSpec:
    data = dict(raw)
    data["source"] = source
    return SkillSpec.model_validate(data)
