"""提示词合成。

这是整个一致性引擎里最关键的一段代码，因为它决定了每一镜的画面
由谁说了算。规则只有一条：

    画面内容  ← Agent 提供
    角色描述  ← 角色资产包（冻结的结构化字段）
    风格描述  ← 系统统一注入

**Agent 写的任何风格词都会被丢弃。** 这不是不信任 Agent，
而是画风漂移的头号来源就是每一镜的风格描述略有不同
（17_ConsistencyEngine.md §4）。第 1 镜写"电影感光影"、
第 20 镜写"柔和布光"，出来就是两部片子。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from apps.api.modules.consistency.models import CharacterProfile, SceneProfile, StyleProfile

# 这些词一旦出现在 Agent 的画面描述里就剥掉。
# 不是穷举——穷举做不到——而是挡住最常见的一批，
# 配合"风格由系统追加"形成兜底：即便漏了一两个，
# 系统注入的风格词在提示词末尾，权重更高。
_STYLE_WORDS = re.compile(
    r"(cinematic|photorealistic|8k|4k|hdr|masterpiece|best quality|highly detailed"
    r"|studio lighting|soft lighting|dramatic lighting|bokeh|depth of field"
    r"|anime style|oil painting|watercolor|concept art|trending on artstation"
    r"|电影感|写实|高清|大师级|最高画质|精细|柔光|硬光|景深|油画|水彩|概念图)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Composed:
    prompt: str
    negative_prompt: str
    seed: int | None
    character_ids: list[str]
    # 这一镜用了哪个场景档案。没有场景信息时为 None——见 compose_shot。
    scene_id: str | None = None
    # 这一镜**实际**用了哪个光照状态的名字。调用方拿它和分镜表上写的
    # `lighting_ref` 一比，就知道有没有发生降级（引用了不存在的状态）。
    # 不在这里记日志：这一层是纯函数，而 project_id / shot_index 这些
    # 真正有用的上下文只有 render 那一层才有。
    lighting_state: str = ""


# 三套描述词的用途（ADR-036 第 3 条）。**不得混用。**
#
#   character  人物质感。角色立绘、以及画面里有人的镜头图
#   scene      **空**场景。场景概念图、以及没有任何角色出场的空镜
#   video      帧率与运动质感。只给视频提示词用（M2），画静态图时是噪声
#
# "画面里有没有人"是一条能从数据判出来的客观判据，不需要再让谁去选：
# `compose_shot` 拿到的 `characters` 非空就是有人。判错的代价不对称——
# 给空镜带上人物质感词只是浪费几个 token，给场景参考图带上人物质感词
# 会让模型往空场景里画人，而那张图是同一场景后续所有镜头的空间基准。
StyleSurface = Literal["character", "scene", "video"]

_SURFACE_FIELD: dict[str, str] = {
    "character": "character_tokens",
    "scene": "scene_tokens",
    "video": "video_tokens",
}


def style_tokens_for(style: StyleProfile, surface: StyleSurface) -> str:
    """这一类产物该注入哪一套风格词，拼成一段。

    渲染方式与线宽两项三套共用：它们描述的是"这部片子怎么画"，与画的是人
    还是空房间无关，拆开只会让同一个项目的三类产物看起来不像一套。

    **色调分级不在这里**，它由调用方决定加不加：镜头图要（成片的色调统一
    靠它），基准立绘和场景概念图不要（那两张是比对基准，带上戏剧化的色调
    会污染基准，让相似度失去意义）。这条区分在这次改动之前就有，
    拆三套没有改变它。
    """
    tokens = str(getattr(style, _SURFACE_FIELD[surface], "") or "")
    return "，".join(b for b in (tokens, style.render_mode, style.line_weight) if b)


def strip_style_words(content: str) -> str:
    """剥掉 Agent 自行编写的风格词。

    只清洗，不报错——Agent 偶尔带一两个风格词是常态，
    为此让整个镜头失败不划算。
    """
    cleaned = _STYLE_WORDS.sub("", content)
    # 清完会留下连续的分隔符。**必须同时处理全角**——
    # 中文提示词里 "，，" 才是常见情况，只清半角等于没清。
    cleaned = re.sub(r"[,，、]\s*(?=[,，、])", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" ,，、")


def describe_character(profile: CharacterProfile) -> str:
    """把结构化外貌拼成稳定的一句描述。

    **字段顺序固定**。同样的角色每次拼出完全相同的字符串，
    顺序一变模型的注意力分布就变，出来的脸也会跟着变。
    """
    a = profile.appearance_json
    parts = [
        profile.name,
        a.get("age_range", ""),
        a.get("hair", ""),
        a.get("eyes", ""),
        a.get("face", ""),
        a.get("build", ""),
        a.get("outfit", ""),
        a.get("distinctive", ""),
    ]
    return "，".join(p for p in parts if p)


def _labelled(label: str, value: object) -> str:
    """给摄影主轴的一段加上说明词，但不重复它。

    `CameraAxis.facing` 的字段说明就是"朝向"，Agent 十有八九会把
    "朝向建筑正面"整句写进去——再前缀一次就成了"朝向朝向建筑正面"。
    提示词里的这种重复不会报错，只会让模型多一次注意力分配在废话上。
    """
    text = str(value).strip()
    if not text:
        return ""
    return text if text.startswith(label) else f"{label}{text}"


def resolve_lighting(spatial: Mapping[str, Any], lighting_ref: str = "") -> dict[str, str]:
    """这一镜到底用哪个光照状态。**永远返回一个状态，不返回 None。**

    解析顺序：显式引用 → 场景声明的默认状态 → 列表里的第一个。

    **非法引用降级到默认，不报错。** 三条理由：

    1. 引用跨两份 Agent 产出。`Storyboard` 校验时手里没有 `SceneSheets`，
       pydantic 层根本判不了这件事；要判就得把两个 schema 绑在一起。
    2. 代价不对称。一个 600 镜的分镜表因为某一镜写了"黄昏"而场景声明的是
       "傍晚"就整份失败，返工成本是重跑一次分镜（几分钟 + 一笔 Credits）；
       降级的代价是这一镜用默认光照，那**正是这个字段存在之前的行为**。
       仓库里同类判断已经有两处先例：未知 `character_refs` 跳过、未知
       `scene_ref` 退回无场景，都写着"为它整镜失败不划算"。
    3. 降级必须留痕，否则查不出来。所以 `Composed.lighting_state` 带回实际
       用的名字，`render` 那一层比对后写 warning——那里才有 project_id 和
       shot_index。

    `spatial` 收 `Mapping` 而不是 `SceneProfile`：`reference_scene_prompt` 和
    `describe_scene` 都只需要 `spatial_json`，收窄参数能让它单独被测。
    """
    states = [s for s in (spatial.get("lighting_states") or []) if isinstance(s, dict)]
    if not states:
        # 没有状态列表 = 这份 spatial_json 还是扁平形状（备份恢复、或者某行
        # 漏掉了迁移）。退回读旧的 `lighting` 字段，而不是让光照凭空消失——
        # 少一段光照描述不会报错，只会让这一镜的光由模型自由发挥，
        # 而那是查起来最费劲的一类问题。
        return {"name": "", "description": str(spatial.get("lighting", "") or "")}

    by_name = {str(s.get("name", "")).strip(): s for s in states}
    for candidate in (str(lighting_ref).strip(), str(spatial.get("default_lighting", "")).strip()):
        if candidate and (hit := by_name.get(candidate)) is not None:
            return {
                "name": str(hit.get("name", "")),
                "description": str(hit.get("description", "")),
            }
    first = states[0]
    return {"name": str(first.get("name", "")), "description": str(first.get("description", ""))}


def describe_fixed_reference(item: Any) -> str:
    """一个固定参照物拼成一段。

    形状是"名称（描述）"而不是只给描述：名称多半是个名词（"船头铜铃"），
    模型需要它才知道画的是**什么东西**，描述给的是这东西长什么样、在哪。
    只给描述会让"绿锈斑驳，缆绳打着水手结"悬在半空没有主语。

    存量的扁平字符串在 `agents/schemas.coerce_fixed_references` 里已经被补出
    了名称（名称是描述的前若干字），此时名称和描述前缀重复，拼出来是
    "铁门在画面正前（铁门在画面正前方）"——所以这里对"名称是描述的前缀"
    这种情况只取描述，避免把同一句话说两遍。
    """
    if not isinstance(item, dict):
        return str(item).strip()
    name = str(item.get("name", "")).strip()
    description = str(item.get("description", "")).strip()
    if not description:
        return name
    if not name or description.startswith(name):
        return description
    return f"{name}（{description}）"


def describe_scene(profile: SceneProfile, lighting_ref: str = "") -> str:
    """把结构化空间信息拼成稳定的一句描述。

    **字段顺序固定**，理由和 `describe_character` 一模一样：同一个场景
    每次拼出完全相同的字符串，顺序一变模型的注意力分布就变，同一个房间
    两镜之间就会挪位置。

    顺序是"这是哪 → 什么时候 → 长什么样 → 什么光 → 摄影机站在哪 →
    哪些东西钉死在哪 → 还有什么"：先立空间，再立机位，最后才是陈设。
    `camera_axis` 和 `fixed_references` 是场景一致性的全部依据
    （`agents/schemas.py` 的 `SceneSheet`），它们必须在，不能省。

    光照那一格注入的是**被引用的那一个状态的描述**，不是全部状态。把三种
    光一起塞进去，模型看到的是互相矛盾的指令（"晨雾"和"夜巡灯"同时成立），
    结果只会是它自己挑一个——那和没有这个字段一样。

    只注入描述、不注入状态名：名称是给人和给引用用的把手（"晨雾"），
    描述才是给模型的光线指令（"雾中散射光，无明确方向，对比度极低"）。
    这一点与固定参照物相反，那里名称是个名词，模型需要它才知道画什么。
    """
    a = profile.spatial_json
    axis = a.get("camera_axis") or {}
    parts = [
        profile.name,
        a.get("time_slot", ""),
        a.get("setting", ""),
        resolve_lighting(a, lighting_ref)["description"],
    ]
    if isinstance(axis, dict):
        # 摄影主轴拼成一句而不是三段：它描述的是同一件事——机位怎么摆。
        # 拆成三段会让模型把"远景末端"当成另一个画面元素。
        axis_bits = [
            _labelled("摄影机位于", axis.get("position", "")),
            _labelled("朝向", axis.get("facing", "")),
            _labelled("远景末端是", axis.get("far_end", "")),
        ]
        if axis_text := "，".join(b for b in axis_bits if b):
            parts.append(axis_text)

    fixed = [t for f in (a.get("fixed_references") or []) if (t := describe_fixed_reference(f))]
    if fixed:
        parts.append("固定参照物：" + "；".join(fixed))

    elements = [str(e) for e in (a.get("key_elements") or []) if str(e).strip()]
    if elements:
        parts.append("；".join(elements))

    return "，".join(p for p in parts if p)


def compose_shot(
    *,
    content: str,
    style: StyleProfile,
    characters: list[CharacterProfile],
    shot_index: int,
    scene: SceneProfile | None = None,
    lighting_ref: str = "",
) -> Composed:
    """合成一个镜头的最终提示词。

    结构固定为：角色 → 场景 → 画面 → 风格。
    场景插在角色之后画面之前：它是这一镜的空间背景，先有人和地方，
    再说这一刻发生了什么，最后才是画风——这也是视觉描述的自然顺序。
    风格仍然放在最后：多数扩散模型对靠后的 token 权重更敏感，
    这样即便 Agent 的画面描述里漏掉一个风格词，也压不过系统的设定。

    `scene` **可选**。分镜表没写 `scene_ref`、写了但档案里没有、或者项目
    根本还没跑到场景阶段时，就退回到"角色 → 画面 → 风格"的旧行为。
    做成硬要求会让所有存量项目突然出不了镜头图，而镜头出图在没有场景
    档案的年代本来就跑得好好的——多一份空间锚点是改进，不是新的前置条件。

    `lighting_ref` 同理可空：留空、或者指向一个该场景没声明过的状态，都落到
    该场景的默认光照上（见 `resolve_lighting`）。实际用了哪个由
    `Composed.lighting_state` 带回去，调用方一比就知道有没有降级。
    """
    segments: list[str] = []
    lighting_state = ""

    if characters:
        segments.append("；".join(describe_character(c) for c in characters))

    if scene is not None:
        lighting_state = resolve_lighting(scene.spatial_json, lighting_ref)["name"]
        if scene_text := describe_scene(scene, lighting_ref):
            segments.append(scene_text)

    if cleaned := strip_style_words(content):
        segments.append(cleaned)

    # 有人用人物版、空镜用场景版。色调分级只在镜头级追加——见 style_tokens_for。
    style_text = style_tokens_for(style, "character" if characters else "scene")
    segments.append("，".join(b for b in (style_text, style.color_grading) if b))

    return Composed(
        prompt="。".join(s for s in segments if s),
        negative_prompt=style.negative_tokens,
        # 项目级基准 seed + 镜号偏移。同一项目重跑同一镜得到同样的图，
        # 便于对比"改了提示词到底有没有变好"。
        seed=(style.seed_base + shot_index) if style.seed_base else None,
        character_ids=[str(c.id) for c in characters],
        scene_id=str(scene.id) if scene is not None else None,
        lighting_state=lighting_state,
    )


def reference_portrait_prompt(profile: CharacterProfile, style: StyleProfile) -> str:
    """基准立绘的提示词。

    刻意用中性表情、中性光照、纯色背景：
    这张图是后续所有镜头的一致性基准，任何戏剧化的光影和构图
    都会污染基准，让相似度比对失去意义。
    """
    return "。".join(
        [
            describe_character(profile),
            "正面全身立绘，中性表情，中性光照，纯色背景，无道具，站姿自然",
            style_tokens_for(style, "character"),
        ]
    )


def reference_scene_prompt(profile: SceneProfile, style: StyleProfile) -> str:
    """场景基准参考图的提示词。

    克制原则照抄 `reference_portrait_prompt`：这张图是同一场景后续所有
    镜头的空间基准，不该被戏剧化的构图污染。但**克制不等于加约束**——
    这里刻意不写"无人物"之类的话：场景档案里可能本来就写着"门口站着
    门卫"，强加一条否定会跟设计意图打架，而基准图要的是空间对不对，
    不是画面里有没有人。

    唯一不能省的是摄影主轴：`describe_scene` 已经把它和固定参照物拼进去
    了，这里再点一次"按上述摄影主轴取景"，让它成为构图指令而不只是描述。
    没有这一句，模型每次自己挑一个角度，这张图就当不了基准。

    光照用**默认状态**（不传 `lighting_ref`）：这张图是空间基准，同一场景的
    多种光照共用它，用哪一种当基准都不该由某一镜决定。默认状态就是场景档案
    自己选出来的那一个。
    """
    return "。".join(
        [
            describe_scene(profile),
            "场景概念图，按上述摄影主轴取景，固定参照物位置严格保持，空间关系清晰完整",
            style_tokens_for(style, "scene"),
        ]
    )
