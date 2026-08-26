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
from dataclasses import dataclass

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


def describe_scene(profile: SceneProfile) -> str:
    """把结构化空间信息拼成稳定的一句描述。

    **字段顺序固定**，理由和 `describe_character` 一模一样：同一个场景
    每次拼出完全相同的字符串，顺序一变模型的注意力分布就变，同一个房间
    两镜之间就会挪位置。

    顺序是"这是哪 → 什么时候 → 长什么样 → 什么光 → 摄影机站在哪 →
    哪些东西钉死在哪 → 还有什么"：先立空间，再立机位，最后才是陈设。
    `camera_axis` 和 `fixed_references` 是场景一致性的全部依据
    （`agents/schemas.py` 的 `SceneSheet`），它们必须在，不能省。
    """
    a = profile.spatial_json
    axis = a.get("camera_axis") or {}
    parts = [
        profile.name,
        a.get("time_slot", ""),
        a.get("setting", ""),
        a.get("lighting", ""),
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

    fixed = [str(f) for f in (a.get("fixed_references") or []) if str(f).strip()]
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
    """
    segments: list[str] = []

    if characters:
        segments.append("；".join(describe_character(c) for c in characters))

    if scene is not None and (scene_text := describe_scene(scene)):
        segments.append(scene_text)

    if cleaned := strip_style_words(content):
        segments.append(cleaned)

    style_bits = [
        style.positive_tokens,
        style.render_mode,
        style.line_weight,
        style.color_grading,
    ]
    segments.append("，".join(b for b in style_bits if b))

    return Composed(
        prompt="。".join(s for s in segments if s),
        negative_prompt=style.negative_tokens,
        # 项目级基准 seed + 镜号偏移。同一项目重跑同一镜得到同样的图，
        # 便于对比"改了提示词到底有没有变好"。
        seed=(style.seed_base + shot_index) if style.seed_base else None,
        character_ids=[str(c.id) for c in characters],
        scene_id=str(scene.id) if scene is not None else None,
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
            "，".join(
                b for b in (style.positive_tokens, style.render_mode, style.line_weight) if b
            ),
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
    """
    return "。".join(
        [
            describe_scene(profile),
            "场景概念图，按上述摄影主轴取景，固定参照物位置严格保持，空间关系清晰完整",
            "，".join(
                b for b in (style.positive_tokens, style.render_mode, style.line_weight) if b
            ),
        ]
    )
