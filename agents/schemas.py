"""Agent 的结构化输出 schema。

**Agent 的输出必须结构化，禁止让下游解析自然语言**
（03_AgentSystem.md §2）。自然语言解析在 99 次里能过，
第 100 次模型换个措辞就崩，而且崩得毫无征兆。

spec 里的 `output_schema` 就是这里的类名。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# 与 CharacterSheet.ref / SceneSheet.ref 上的 Field(pattern=...) 是同一条规则。
# 改这里就要同步改那边，否则兜底会造出一个字段校验仍然不认的 ref。
REF_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,30}$")

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


# ---------------------------------------------------------- 角色体貌受控词表
#
# 身高 / 体型 / 体态从自由文本收紧为有限词表（ADR-037 落地的第 5 项）。
#
# **为什么是 Literal 而不是一张可热更新的表**：这三组词是 Agent 的**输出契约**，
# 由 pydantic 在产出落库之前强制。换掉其中一个词就等于换掉 `CharacterSheets`
# 的 schema——要同步改提示词、改 `SCHEMA_SAMPLES`、重跑 eval，还要考虑既有
# 产出还能不能通过校验。它和"价格 / 汇率 / 废片率"那类**运营时要热更新的
# 数字**不是一类东西，和同一个文件里的 `STORYBOARD_SHOT_SIZES` / `BEING_KINDS`
# / `CAMPS` 才是一类。真正需要热更新的画风目录走 `style_catalog` 表
# （见 `apps/api/modules/consistency/models.py`），那个才是数据。
#
# 词表本身只有一条设计原则：**每个词都要能让出图模型画出不同的东西。**
# 因此刻意排除"匀称""普通""正常身材"——它们在扩散模型里等价于没写，
# 而同一个角色两次生成一次"身材匀称"一次"体型适中"，出来就是两个人。
# 这也是 `reference_embedding` 至今为空的根因之一：没有可比的结构化描述，
# 就没有可比的向量。

HEIGHT_BANDS = Literal["娇小", "偏矮", "中等身高", "偏高", "高挑", "魁梧高大"]

BODY_TYPES = Literal[
    "瘦削嶙峋",
    "纤细单薄",
    "精瘦结实",
    "健硕壮实",
    "宽肩厚背",
    "圆润丰腴",
    "肥硕臃肿",
]

POSTURES = Literal[
    "挺拔端正",
    "含胸驼背",
    "松弛慵懒",
    "紧绷戒备",
    "佝偻蜷缩",
    "轻盈灵动",
    "沉稳压场",
]

# 兜底值只服务于**存量产出的再校验**：字段级编辑（`PATCH .../outputs/characters`）
# 会拿现行 schema 去校验库里早就存下的角色档案，那些档案没有这三个字段。
# 没有默认值 = 所有存量项目的角色档案立刻变成不可编辑。
# 新产出不该依赖它们——提示词要求显式选，选不出来的要写进 `inferred`。
DEFAULT_HEIGHT: HEIGHT_BANDS = "中等身高"
DEFAULT_BODY_TYPE: BODY_TYPES = "精瘦结实"
DEFAULT_POSTURE: POSTURES = "挺拔端正"


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

    # —— 时代背景判定（ADR-037 门① 的四项之一）——
    #
    # 放在情节目录而不是场景档案：门① 在 `screenplay` **之前**，那时场景档案
    # 还不存在，而人种一旦判错，角色、服装、建筑、街景会一路错到分镜。
    # 这里给的是**系统按原文证据得出的判定**，用户在门① 可以改；
    # `era_evidence` 是它的依据，没有依据的判定等于猜。
    #
    # 默认全空，不是空串兜底成"现代中国"——**不得默认套用本国**是这条
    # 设计的全部要点。判不出来就留空，让用户在门① 显式决定。
    # 有默认值只是为了让存量产出仍能通过字段级编辑的再校验。
    era: str = Field(default="", max_length=40, description="时代背景，如 民国 / 现代 / 近未来")
    region: str = Field(default="", max_length=40, description="国别或地区，从原文内证据判定")
    ethnicity: str = Field(default="", max_length=60, description="主要人种，由时代与国别推出")
    era_evidence: str = Field(
        default="", max_length=200, description="判定依据的原文线索，没有线索就留空"
    )


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
    唯一的例外是 `build`：它现在由 `height` / `body_type` / `posture` 三个
    受控词表拼出来（`_appearance()` 负责），字段名和位置都没变，
    变的只是"值从哪来"——从模型的自由发挥变成从有限词表里选。
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
    # **不要再往这里写自由文本。** 身高/体型/体态已经收紧成下面三个受控词表，
    # `_appearance()` 会用它们拼出 `build` 存进 appearance_json。这一列保留
    # 只有一个理由：存量角色档案里有它，删掉会让所有老项目的角色产出无法
    # 通过字段级编辑的再校验（`extra="forbid"`）。新产出留空即可。
    build: str = Field(default="", max_length=60)
    outfit: str = Field(max_length=160)
    distinctive: str = Field(default="", max_length=120)

    # —— 受控词表：身高 / 体型 / 体态（ADR-037）——
    # 自由文本的体型描述是"同一个角色两次生成不是同一个人"的直接来源，
    # 也是 reference_embedding 至今为空的根因之一。默认值只为存量产出
    # 的再校验兜底，提示词要求显式选。
    height: HEIGHT_BANDS = DEFAULT_HEIGHT
    body_type: BODY_TYPES = DEFAULT_BODY_TYPE
    posture: POSTURES = DEFAULT_POSTURE

    # —— 补充字段：存进 appearance_json，暂不进 describe_character ——
    # 加进去会改变已冻结角色的描述串，要等一次显式的风格重冻结
    #
    # `nationality` / `ethnicity` 由门① 锁定的时代背景派发下来（ADR-037 第 2 条）。
    # **不得默认套用本国**：判错会让人种、服装、发型与场景建筑、街景、室内
    # 陈设全部错位，且错误一路传导到分镜，返工成本是"全部重出"。
    nationality: str = Field(default="", max_length=40)
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


# 存量数据迁移时，用描述的前若干字充当锚点/光照状态的名称。
#
# **这不是"会变的阈值"**，所以不入库：它只在一处生效——把没有名称的存量扁平
# 字符串补出一个名称来，好让它在界面上和下游引用里有个把手。既不参与任何业务
# 判定，也不随上游价格或供应商变化。它唯一的约束来自同一个文件里
# `FixedReference.name` / `LightingState.name` 的 max_length，改名称上限时才需要
# 一起看。同类先例见 `apps/api/modules/agent/anchors.py` 里的判据常量。
MIGRATED_NAME_CHARS = 12

# 存量的单个 `lighting` 字符串迁过来之后叫什么，也是模型漏填 `default_lighting`
# 时兜底状态的名字。
DEFAULT_LIGHTING_NAME = "默认"

# 数据来源。`migrated` = 由存量扁平字段自动补出来的，不是谁真的写过。
# 界面要能把它和作者写的区分开，否则用户看到一个"名称"会以为有人取过名。
ORIGINS = Literal["authored", "migrated"]


class FixedReference(_Strict):
    """一个固定参照物：名称 + 能复现的描述。

    **为什么拆两段而不是一句话。** 这两段是给两个不同的读者看的：

        name         给人、也给下游引用——用户扫一眼就知道这个场景钉死了
                     哪几样东西，界面和分镜按这个把手指过来
        description  给出图模型复现——它要的是"篷布右后角一块颜色更深的
                     方形补丁，漏下一道光缝"这种能画出来的细节

    合成一句话两头不讨好：人扫不出这个场景有哪几个锚点，模型也拿不到足够
    细节。"有补丁"是不合格的描述——它在扩散模型里等价于没写，而同一块补丁
    这镜在左下镜在右，正是空间锚点要防的东西。
    """

    name: str = Field(max_length=20, description="短名称，如 补丁船篷 / 磨白的坐板")
    description: str = Field(max_length=160, description="具体到能复现的描述，必须带方位")
    origin: ORIGINS = "authored"


class LightingState(_Strict):
    """场景的一个具名光照状态。

    **为什么光照是集合而不是一个字段。** 同一个场景在一部剧里会在不同时刻
    反复出现——渡口的清晨、正午、夜里。一个 `lighting` 字段只有两种结局：
    要么全场景共用一种光（夜戏用白天的光），要么每个镜头让模型自由发挥，
    而后者正是一致性引擎要防的东西。正确形态是：光照是一个**有限集合**，
    镜头从集合里选一个（`StoryboardShot.lighting_ref`），而不是每镜自己编。
    """

    name: str = Field(max_length=20, description="短名称，如 晨雾 / 正午 / 夜巡灯")
    description: str = Field(max_length=100, description="光线的来源与方向")
    origin: ORIGINS = "authored"


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _derived_name(description: str) -> str:
    """没有名称时从描述里截一个出来。

    截断而不是留空：留空的锚点在界面上是一行没有标题的描述，在分镜里也没法
    被引用。截出来的名字不好看，但它是个能用的把手，且**原文一个字都没丢**
    ——完整描述仍在 `description` 里，截出来的名称是副本不是替换。
    """
    return description[:MIGRATED_NAME_CHARS]


def coerce_fixed_references(value: object) -> list[dict[str, Any]]:
    """把固定参照物统一成 `{name, description, origin}` 的形状。

    接受三种输入，因为这三种都真实存在：

        新产出   [{"name": ..., "description": ...}]      原样规范化
        存量     ["铁门在画面正前方"]                       补名称，标 migrated
        半成品   只有 name 或只有 description             缺哪段补哪段

    **任何一种都不丢原文。**
    """
    if not isinstance(value, list):
        return []

    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            name = _text(item.get("name"))
            description = _text(item.get("description"))
            if not name and not description:
                continue
            origin = _text(item.get("origin"))
            if not description:
                # 只有名称：它就是全部原文，同时当描述用。
                description = name
                origin = origin or "migrated"
            if not name:
                name = _derived_name(description)
                origin = origin or "migrated"
            out.append(
                {
                    "name": name,
                    "description": description,
                    "origin": origin if origin in ("authored", "migrated") else "authored",
                }
            )
            continue

        text = _text(item)
        if not text:
            continue
        out.append({"name": _derived_name(text), "description": text, "origin": "migrated"})
    return out


def coerce_lighting_states(design: Any) -> tuple[list[dict[str, Any]], str]:
    """把光照统一成 `(状态列表, 默认状态名)`。

    存量的单个 `lighting` 字符串迁成**一个名为「默认」的状态**——既不丢掉它，
    也不给存量场景凭空编出四个时段。老数据里只有一种光，迁完仍然只有一种，
    行为与迁移前逐字相同。

    **一定会返回至少一个状态**：`SceneSheet.lighting_states` 是 `min_length=1`，
    而"没有默认状态"意味着漏填 `lighting_ref` 的镜头行为未定义——那正是这次
    改动要消灭的东西。真的一个字都没有时给一个空描述的默认状态，注入的内容
    与改动前（`lighting` 为空串）一致。
    """
    empty: list[dict[str, Any]] = [
        {"name": DEFAULT_LIGHTING_NAME, "description": "", "origin": "migrated"}
    ]
    if not isinstance(design, dict):
        return empty, DEFAULT_LIGHTING_NAME

    raw = design.get("lighting_states")
    states: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = _text(item.get("name"))
                description = _text(item.get("description"))
                origin = _text(item.get("origin"))
            else:
                name, description, origin = "", _text(item), "migrated"
            if not name and not description:
                continue
            if not name:
                name = _derived_name(description)
                origin = origin or "migrated"
            if name in seen:
                # 同名状态第二次出现永远选不到（按名字解析取第一个匹配），
                # 留在列表里只会在界面上多一个点了没用的选项。
                log.warning("agent.lighting_state_duplicate", name=name)
                continue
            seen.add(name)
            states.append(
                {
                    "name": name,
                    "description": description,
                    "origin": origin if origin in ("authored", "migrated") else "authored",
                }
            )

    if not states:
        # 存量形状：单个 `lighting` 字符串。空串也要建状态，见 docstring。
        states = [
            {
                "name": DEFAULT_LIGHTING_NAME,
                "description": _text(design.get("lighting")),
                "origin": "migrated",
            }
        ]
        seen = {DEFAULT_LIGHTING_NAME}

    default = _text(design.get("default_lighting"))
    if default not in seen:
        if default:
            log.warning("agent.default_lighting_unknown", default=default, known=sorted(seen))
        default = str(states[0]["name"])
    return states, default


class SceneSheet(_Strict):
    ref: str = Field(pattern=r"^[a-z][a-z0-9_]{1,30}$")
    name: str = Field(max_length=40)
    time_slot: str = Field(max_length=20, description="从标准时间词库中选")
    setting: str = Field(max_length=300)

    # 光照是有限集合，不是一个字段——理由见 `LightingState`。
    lighting_states: list[LightingState] = Field(
        min_length=1,
        max_length=6,
        description="这个地点在剧本里实际出现过的光照状态，各带一段描述",
    )
    # 没有显式指定光照的镜头用哪一个。必须是上面已声明的状态之一；留空或指向
    # 未知状态时由 `_accept_legacy_shape` 落到第一个状态上，绝不留成未定义。
    default_lighting: str = Field(default="", max_length=20, description="默认光照状态的名称")

    key_elements: list[str] = Field(min_length=1, max_length=12)
    camera_axis: CameraAxis
    fixed_references: list[FixedReference] = Field(
        default_factory=list,
        max_length=8,
        description="固定参照物及其位置，构成空间锚点卡的固定层",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_shape(cls, data: Any) -> Any:
        """把存量的扁平形状就地翻译成结构化形状。

        字段级编辑（`PATCH .../outputs/scenes`）会拿**现行 schema** 去校验库里
        早就存下的场景档案，而那些档案里 `lighting` 是一个字符串、
        `fixed_references` 是一串句子。没有这一步，所有存量项目的场景档案立刻
        变成不可编辑——`extra="forbid"` 会直接因为多出来的 `lighting` 报错。
        这与 `CharacterSheet.build` 保留那一列是同一类兼容措施。

        迁移脚本把库里的数据一次性改过来；这里是运行时的第二道保险，管的是
        备份恢复、以及模型偶尔照着旧样例输出的情况。
        """
        if not isinstance(data, dict):
            return data
        patched = {k: v for k, v in data.items() if k != "lighting"}
        states, default = coerce_lighting_states(data)
        patched["lighting_states"] = states
        patched["default_lighting"] = default
        if "fixed_references" in patched:
            patched["fixed_references"] = coerce_fixed_references(patched["fixed_references"])
        return patched


def fallback_character_ref(name: str, index: int) -> str:
    """给非法 ref 造一个合法替身。

    后缀取人名的哈希，不取角色在列表里的下标。下标看着更简单，但它跨次
    不稳定：同一个项目重跑角色阶段（revise 打回重做、加了一个角色、模型
    换了个排序），同一个人物这次落在第 2 位下次落在第 3 位，ref 就从
    `char_2` 变成 `char_3`。一致性引擎 `upsert_characters()` 按 ref 认人，
    换了 ref 就当成新角色——库里同一个人出现两份 CharacterProfile，
    而空出来的 `char_2` 还会被另一个人的外貌覆盖进去。
    哈希后缀让"同一个人名 → 同一个 ref"，把这条路堵死。

    人名也拿不到时（缺失或空串）才退回位置下标——此时没有任何可用于
    稳定的信息，位置是唯一剩下的东西。
    """
    key = name.strip()
    if not key:
        return f"char_{index + 1}"
    return f"char_{hashlib.blake2s(key.encode('utf-8'), digest_size=4).hexdigest()}"


class CharacterSheets(_Strict):
    characters: list[CharacterSheet] = Field(min_length=1, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def _rescue_refs(cls, data: Any) -> Any:
        """非法 ref 在字段校验之前改写成兜底 ref。

        提示词已经写明 ref 只能是小写 ASCII 标识符，但模型遇到中文、日文
        假名、生僻字人名时仍会把原名直接塞进 ref（实测"绯色冴子"三次重试
        都是同一个错）。少一个合法 ref 就让整份角色档案校验失败、角色阶段
        500、项目卡死——代价和收益完全不成比例。这里宁可给一个不好看但能用的
        ref，把失败降级成一条 warning。
        """
        if not isinstance(data, dict):
            return data
        characters = data.get("characters")
        if not isinstance(characters, list):
            return data

        def normalized(item: object) -> str | None:
            if not isinstance(item, dict):
                return None
            raw = item.get("ref")
            if not isinstance(raw, str):
                return None
            candidate = raw.strip().lower()
            return candidate if REF_PATTERN.match(candidate) else None

        # 合法 ref 先占位，兜底 ref 不能撞上它们
        taken = {ref for item in characters if (ref := normalized(item)) is not None}

        rescued: list[Any] = []
        for index, item in enumerate(characters):
            candidate = normalized(item)
            if candidate is not None:
                rescued.append(item if item.get("ref") == candidate else {**item, "ref": candidate})
                continue
            if not isinstance(item, dict):
                rescued.append(item)
                continue

            name = item.get("name")
            ref = fallback_character_ref(name if isinstance(name, str) else "", index)
            if ref in taken:
                # 同名角色出现两次才会走到这里。加下标让它们各占一个 ref，
                # 否则两个人会被一致性引擎合并成一个。
                ref = f"{ref}_{index + 1}"
            taken.add(ref)
            log.warning(
                "agent.character_ref_rescued",
                name=name,
                original_ref=item.get("ref"),
                ref=ref,
                index=index,
            )
            rescued.append({**item, "ref": ref})

        return {**data, "characters": rescued}


class SceneSheets(_Strict):
    era: str = Field(max_length=40, description="时代背景，决定角色默认人种")

    #: 全剧**唯一**的整体色调（原 Skill B4）。
    #:
    #: 它不是某个场景的光，也不是某一镜的光——那两件事分别由
    #: `SceneSheet.lighting_states` 和分镜的 `lighting_ref` 管。这一项回答的是
    #: "这部片子整体看起来是什么调子"，写进每一张场景概念图与每一个镜头，
    #: 让剪在一起的镜头像同一部片子。
    #:
    #: **可留空**：这个字段是后加的，存量项目的场景产出里没有它。留空时
    #: 提示词层回落到画风目录上的 `color_grading`（见
    #: `prompting.context._global_tone`），不编一个色调出来。
    global_tone: str = Field(default="", max_length=60)

    scenes: list[SceneSheet] = Field(min_length=1, max_length=30)


# ----------------------------------------------------------------- 分镜层


class StoryboardNode(_Strict):
    index: int = Field(ge=1)
    scene_ref: str
    summary: str = Field(max_length=40)


class StoryboardShot(_Strict):
    """一个镜号。

    **不含时长**：时长来自 TTS 的真实音频长度，是一次确定性计算。
    让模型填它只会得到一个看起来合理但对不上的数字。

    **也不含批次/段号**：ADR-034 把镜内切段从"已决定"降级为**待定**，
    按 N=1 实现（`shot_segments` / `segment_videos` 两张表不建）。
    这里曾经写着"批次由 15 秒规则切分"，那条规则出自 ADR-032 第 3 条，
    已被 ADR-034 作废——视频时长是**离散档位集合**而不是"单段最大时长"，
    平均切段几乎必然切出集合外的非法值。等到出现一个既是主力、单段上限
    又确实短于常见镜长的模型时再重新评估。
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

    # 这一镜用该场景的哪个光照状态。**必须是该场景已声明的状态之一**
    # （`SceneSheet.lighting_states`），留空 = 用该场景的 `default_lighting`。
    #
    # 只写名字不写描述：描述由系统按名字从场景档案里取，注入被引用的**那一个**
    # 状态。让分镜自己写光照描述就退回到"每镜自由发挥"，那正是这个字段要消灭的
    # 东西——同一个渡口的两个夜戏镜头会得到两种夜色。
    lighting_ref: str = Field(
        default="", max_length=20, description="引用场景已声明的光照状态名，留空用默认"
    )

    speaker_ref: str = Field(default="", max_length=32)
    dialogue: str = Field(default="", max_length=200)
    sfx: str = Field(default="", max_length=80)


class Storyboard(_Strict):
    nodes: list[StoryboardNode] = Field(min_length=1, max_length=200)
    shots: list[StoryboardShot] = Field(min_length=1, max_length=600)


# --------------------------------------------------------------- 成品提示词层
#
# ADR-036：最终喂给出图/视频模型的那段提示词**由 Agent 合成**，不再由
# `consistency/compose.py` 确定性拼装。下面四个 schema 是那四类成品词的输出
# 契约，各自对应超哥 Skill 里的一段模板（`references/` 下的 B/C 两份原文）：
#
#   CharacterPortraitPrompt  B3 角色立绘（模板 A 人类 / 模板 B 非人类）
#   SceneViewsPrompt         B5 场景概念图 2×2 四视图
#   ShotFramePrompt          单镜首帧（C4 九宫格五要素的单镜适配）
#   ShotVideoPrompt          C2 逐镜视频提示词
#
# **风格词不在这些 schema 里**。它由系统按项目锁定的画风逐字提供，Agent 被
# 要求原样抄进 `prompt`，抄漏或改写由 `prompting.rules` 的校验器判为不合格
# （ADR-036 第 2、4 条）。放进 schema 会变成"模型自己填的一个字段"，
# 那时校验的就是它自己写的东西，等于没校验。
#
# 每个 schema 除了 `prompt` 都还带几个**结构化自检字段**。它们不是装饰：
# 模型必须先把"这一格拍什么""这个角色带什么道具"想清楚才填得出来，而校验器
# 拿它们去比对 `prompt`——五要素缺一项、四格少一格，在这里就能判出来，
# 不必等图出完了用眼睛看。


class CharacterPortraitPrompt(_Strict):
    """B3 角色立绘的成品提示词。

    `template` 二选一且**不得混用**（原 Skill B3 执行规则）：人类角色走模板 A，
    动物/怪物/神兽/异形走模板 B。两套模板的要素顺序完全不同，用错一套出来的
    不是"稍差一点"，而是拿人类的五官/妆容/鞋子去套一头狼。

    `accessories` 单独成字段，是因为原 Skill 明写「关键配件：手持或佩戴，
    无则填「无」」——而实测最容易发生的退化是模型一律写"无道具"图省事，
    把角色身上那件**剧情关键**的东西（钥匙串、腰牌、眼镜）抹掉。让它显式
    填一格，校验器才能在"确实没有"和"没想过"之间分得开。
    """

    template: Literal["人类", "非人类"]
    prompt: str = Field(min_length=40, max_length=2400)
    #: 写进提示词的国籍/人种词。必须与角色档案一致，不得自行改写（原 Skill B3
    #: 执行规则最后一条）。非人类角色填物种/类别。
    subject_identity: str = Field(min_length=1, max_length=60)
    #: 关键配件。**确实没有**时填「无」，不是留空——留空分不清"没有"和"漏了"。
    accessories: str = Field(min_length=1, max_length=160)


class SceneQuadrants(_Strict):
    """B5 四视图每一格拍什么。四格机位固定，不因场景类型改变。"""

    top_left: str = Field(min_length=1, max_length=400, description="场景正中天花板垂直俯视")
    top_right: str = Field(min_length=1, max_length=400, description="沿摄影主轴平视正视图")
    bottom_left: str = Field(min_length=1, max_length=400, description="右前角朝左后角对角线")
    bottom_right: str = Field(min_length=1, max_length=400, description="右后角朝左前角")


class SceneViewsPrompt(_Strict):
    """B5 场景概念图（2×2 四视图）的成品提示词。

    `fixed_elements` 是原 Skill 的「元素锁定清单」：四个格子只是摄影机角度
    不同，场景本身的元素**完全一致**，家具朝向在四格之间不得变。先列清单再
    写四格描述，是原文规定的执行顺序，也是这张图能当空间基准的全部理由。
    """

    prompt: str = Field(min_length=80, max_length=4000)
    #: 四格共用的固定元素（地面、墙面、主体家具及其朝向、标志性道具、光源）
    fixed_elements: list[str] = Field(min_length=2, max_length=16)
    quadrants: SceneQuadrants


class ShotFramePrompt(_Strict):
    """单镜首帧的成品提示词（C4 九宫格五要素的单镜适配）。

    五要素逐个成字段而不是让模型"写在一段里就行"：原文把「身后背景」标成
    **最容易出错的要素**，因为角色朝向一变背后空间就完全不同，模型会套用一个
    固定背景。拆成字段之后，缺哪一项在 schema 层就红，不必等图。

    **这不是九宫格**。九宫格是一次出九格的排版图，需要实测切格质量才能开生产
    入口（见 `project_docs/plans/2026-09-11_...` §5 C4）。这里借的是它的五要素
    检查标准，产出仍是单镜一张。
    """

    prompt: str = Field(min_length=40, max_length=2400)
    #: 要素①：景别（特写类必须带局部说明，且局部说明**替换**"特写"二字）
    shot_size: str = Field(min_length=1, max_length=40)
    #: 要素②：非默认拍摄角度。正面/平视/居中是默认，默认就留空
    angle: str = Field(default="", max_length=60)
    #: 要素③：角色朝向。无人物出场的空镜写「无人物出场」，不留空
    facing: str = Field(min_length=1, max_length=120)
    #: 要素④：身后背景。必须从空间锚点推演，不得套用固定背景
    behind: str = Field(min_length=1, max_length=200)
    #: 要素⑤：姿态/表情 + 光影。背对/侧对镜头时不写面部表情
    pose_lighting: str = Field(min_length=1, max_length=200)


class ShotVideoPrompt(_Strict):
    """C2 逐镜视频提示词的成品。

    `cuts` 是【切镜】行，**每个切镜独立一行**（原 Skill C 阶段全局约束第 10 条）。
    拆成列表而不是让模型在 `prompt` 里换行，是因为"两个切镜挤在同一行"是原文
    点名的禁止项，而列表让它在结构上就不可能发生。

    `prompt` 是可直接投喂的代码块全文：资产标注句 → 各切镜 → 最后一行强制的
    「只添加音效和台词，禁止添加背景音乐，禁止出现字幕。」最后那行不可省略、
    不可移到代码块外侧，由校验器守着。
    """

    prompt: str = Field(min_length=40, max_length=4000)
    #: 资产标注句：@是[角色]，@是[场景]，[声音资产]，[视频版风格词]，在[场景名]，[光影基调]。
    asset_line: str = Field(min_length=1, max_length=1200)
    #: 每个切镜一条，顺序即播放顺序
    cuts: list[str] = Field(min_length=1, max_length=12)
    #: 空间锚点卡（写在代码块外侧）。同场景后续镜号人物状态无变化时可留空
    anchor_card: str = Field(default="", max_length=1200)


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
    "CharacterPortraitPrompt": CharacterPortraitPrompt,
    "SceneViewsPrompt": SceneViewsPrompt,
    "ShotFramePrompt": ShotFramePrompt,
    "ShotVideoPrompt": ShotVideoPrompt,
}


def resolve(name: str) -> type[BaseModel]:
    try:
        return SCHEMAS[name]
    except KeyError as exc:
        raise LookupError(
            f"未知的 output_schema {name!r}。可用：{', '.join(sorted(SCHEMAS))}"
        ) from exc
