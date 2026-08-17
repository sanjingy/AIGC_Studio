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

# 分镜层用更细的景别词库。与上面的 SHOT_SIZES 暂时并存：
# 那个属于 visual.default.v1（一个 Agent 干完角色+场景+分镜的旧路径），
# 等它退役后合并。现在改它会动到已有的 VisualPlan 产出。
STORYBOARD_SHOT_SIZES = Literal[
    "极近特写",
    "特写",
    "近景胸像",
    "中景腰部",
    "中全景",
    "全景",
    "大全景",
    "航拍俯瞰",
]

CAMPS = Literal["正派", "反派", "中立", "未知"]

BEING_KINDS = Literal["人类", "动物", "怪物", "神兽", "异形", "机械生命"]

# 剧本节拍的类型。把"△动作独占一行"这类排版铁律变成结构，
# 而不是让下游去正则匹配 △ 符号——排版是渲染层的事，不是数据。
BEAT_KINDS = Literal["action", "dialogue", "vo", "sfx"]


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


# ----------------------------------------------------------------- 文本层


class CharacterAlias(_Strict):
    """角色的全部称呼。

    原著里同一个人会有真名、昵称、游戏 ID、群名片、代号。
    没有这张映射表，台词归属会按上下文猜，猜错就是"张三说了李四的话"。
    """

    name: str = Field(max_length=40)
    aliases: list[str] = Field(default_factory=list, max_length=12)


class PlotNode(_Strict):
    index: int = Field(ge=1)
    summary: str = Field(max_length=40)


class PlotIndex(_Strict):
    """情节目录：改编前先把原文拆成节点，改编后逐节点回查。

    这是"改编结果与原著不搭架"的解药——先固定一份可核对的清单，
    后面每一节都要能指回来。核对由规则做，不靠模型自述。

    **不输出任何秒数或集数**：时长由 TTS 真实音频决定，
    集数由确定性代码按时长切。模型只提供字数这类可数事实。
    """

    genre: str = Field(max_length=40)
    logline: str = Field(max_length=200)
    synopsis: str = Field(max_length=600)
    central_conflict: str = Field(max_length=300)

    characters: list[CharacterAlias] = Field(min_length=1, max_length=30)
    nodes: list[PlotNode] = Field(min_length=1, max_length=200)

    scene_count: int = Field(ge=1, le=500)
    dialogue_chars: int = Field(ge=0, le=500_000, description="台词总字数，供估算用")


class Beat(_Strict):
    """剧本的一个节拍。

    动作、台词、独白、音效各占一条，不混写在一段里。
    """

    kind: BEAT_KINDS
    character_ref: str = Field(default="", max_length=32)
    emotion: str = Field(default="", max_length=40)
    text: str = Field(max_length=300)


class ScriptScene(_Strict):
    id: str = Field(pattern=r"^\d+-\d+$", description="形如 1-1")
    location: str = Field(max_length=60)
    time_mood: str = Field(max_length=60)
    character_refs: list[str] = Field(default_factory=list, max_length=12)
    beats: list[Beat] = Field(min_length=1, max_length=80)
    hook: str = Field(default="", max_length=200)


class Episode(_Strict):
    index: int = Field(ge=1)
    title: str = Field(max_length=60)
    scenes: list[ScriptScene] = Field(min_length=1, max_length=40)


class NodeCoverage(_Strict):
    """情节节点 → 落在哪一场。

    模型自己写"✅ 已覆盖"是没有约束力的，它可以照写不误。
    落成结构化引用，QA 就能真去查那一场存不存在。
    """

    node_index: int = Field(ge=1)
    scene_id: str = Field(default="", pattern=r"^(\d+-\d+)?$")
    merged_into: int | None = Field(default=None, ge=1)


class Screenplay(_Strict):
    title: str = Field(max_length=80)
    synopsis: str = Field(max_length=600)
    episodes: list[Episode] = Field(min_length=1, max_length=30)
    node_coverage: list[NodeCoverage] = Field(min_length=1, max_length=200)


# ----------------------------------------------------------------- 视觉资产层


class CharacterSheet(_Strict):
    """角色视觉档案。

    比 CharacterDesign 多出阵营、性格与即时状态。
    `present_state` 优先级最高：角色背景再惨，本段剧情里若是高光时刻，
    立绘就该挺拔有神——立绘展示的是最具代表性的状态，不是最狼狈的状态。

    外貌前 7 个字段的**名字和顺序**与 consistency.compose.describe_character
    对齐，改名或调序会让同一个角色每次拼出不同的描述串，脸就跟着变。
    """

    ref: str = Field(pattern=r"^[a-z][a-z0-9_]{1,30}$")
    name: str = Field(max_length=40)
    kind: BEING_KINDS = "人类"
    camp: CAMPS = "未知"

    identity: str = Field(max_length=120)
    relations: str = Field(default="", max_length=200)

    personality: list[str] = Field(min_length=1, max_length=4)
    power_position: str = Field(default="", max_length=40)
    arc_stage: str = Field(default="", max_length=40)
    present_state: str = Field(default="", max_length=120)

    # —— 与 describe_character 对齐的 7 个字段，顺序不要动 ——
    age_range: str = Field(max_length=20)
    hair: str = Field(max_length=80)
    eyes: str = Field(max_length=60)
    face: str = Field(max_length=100)
    build: str = Field(max_length=60)
    outfit: str = Field(max_length=160)
    distinctive: str = Field(default="", max_length=120)

    # —— 补充字段：存进 appearance_json，暂不进 describe_character ——
    # 加进去会改变已冻结角色的描述串，要等一次显式的风格重冻结
    ethnicity: str = Field(default="", max_length=40)
    skin: str = Field(default="", max_length=60)
    shoes: str = Field(default="", max_length=80)
    accessories: str = Field(default="", max_length=120)

    inferred: list[str] = Field(
        default_factory=list, max_length=20, description="哪些字段是推断的，不是原文写的"
    )


class CameraAxis(_Strict):
    """场景的摄影主轴：概念图正视图的唯一依据。

    没有它，四视图里的"正面"就由模型每次自己挑，
    同一个场景两次生成会朝两个方向。
    """

    position: str = Field(max_length=80, description="摄影机站位")
    facing: str = Field(max_length=80, description="朝向")
    far_end: str = Field(max_length=80, description="远景末端")


class SceneSheet(_Strict):
    ref: str = Field(pattern=r"^[a-z][a-z0-9_]{1,30}$")
    name: str = Field(max_length=40)
    time_slot: str = Field(max_length=20, description="从标准时间词库中选")
    setting: str = Field(max_length=300)
    lighting: str = Field(max_length=100)
    key_elements: list[str] = Field(min_length=1, max_length=12)
    camera_axis: CameraAxis
    fixed_references: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="固定参照物及其位置，构成空间锚点卡的固定层",
    )


class CharacterSheets(_Strict):
    characters: list[CharacterSheet] = Field(min_length=1, max_length=30)


class SceneSheets(_Strict):
    era: str = Field(max_length=40, description="时代背景，决定角色默认人种")
    scenes: list[SceneSheet] = Field(min_length=1, max_length=30)


# ----------------------------------------------------------------- 分镜层


class StoryboardNode(_Strict):
    index: int = Field(ge=1)
    scene_ref: str
    summary: str = Field(max_length=40)


class StoryboardShot(_Strict):
    """一个镜号。

    **不含时长和批次**：时长来自 TTS 的真实音频长度，批次由 15 秒规则切分，
    两者都是确定性计算。让模型填这两个数只会得到看起来合理但对不上的数字。
    """

    index: int = Field(ge=1)
    node_index: int = Field(ge=1, description="对应哪个分镜节点，用于覆盖核验")
    scene_ref: str
    character_refs: list[str] = Field(default_factory=list, max_length=8)

    shot_size: STORYBOARD_SHOT_SIZES
    angle: str = Field(default="", max_length=40, description="非默认角度才写，正面平视居中省略")
    camera_move: str = Field(default="", max_length=40)

    # 只写画面内容。风格词由系统统一注入——
    # Agent 自行编写风格词是画风漂移的头号来源。
    content: str = Field(max_length=300)

    speaker_ref: str = Field(default="", max_length=32)
    dialogue: str = Field(default="", max_length=200)
    sfx: str = Field(default="", max_length=80)


class Storyboard(_Strict):
    nodes: list[StoryboardNode] = Field(min_length=1, max_length=200)
    shots: list[StoryboardShot] = Field(min_length=1, max_length=600)


SCHEMAS: dict[str, type[BaseModel]] = {
    "RouterDecision": RouterDecision,
    "StoryOutline": StoryOutline,
    "VisualPlan": VisualPlan,
    "DirectorPlan": DirectorPlan,
    "QAReport": QAReport,
    "PlotIndex": PlotIndex,
    "Screenplay": Screenplay,
    "CharacterSheets": CharacterSheets,
    "SceneSheets": SceneSheets,
    "Storyboard": Storyboard,
}


def resolve(name: str) -> type[BaseModel]:
    try:
        return SCHEMAS[name]
    except KeyError as exc:
        raise LookupError(
            f"未知的 output_schema {name!r}。可用：{', '.join(sorted(SCHEMAS))}"
        ) from exc
