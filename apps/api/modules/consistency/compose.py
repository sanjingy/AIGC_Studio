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

from apps.api.modules.consistency.models import CharacterProfile, StyleProfile

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


def compose_shot(
    *,
    content: str,
    style: StyleProfile,
    characters: list[CharacterProfile],
    shot_index: int,
) -> Composed:
    """合成一个镜头的最终提示词。

    结构固定为：角色 → 画面 → 风格。
    风格放在最后：多数扩散模型对靠后的 token 权重更敏感，
    这样即便 Agent 的画面描述里漏掉一个风格词，也压不过系统的设定。
    """
    segments: list[str] = []

    if characters:
        segments.append("；".join(describe_character(c) for c in characters))

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
