"""提示词合成。

这段代码决定每一镜的画面由谁说了算。规则只有一条：
画面内容由 Agent 提供，风格由系统注入，两者不能混。

第 1 镜写"电影感光影"、第 20 镜写"柔和布光"，出来就是两部片子——
这是画风漂移的头号来源（17_ConsistencyEngine.md §4）。
"""

from __future__ import annotations

import uuid

from apps.api.modules.consistency.compose import (
    compose_shot,
    describe_character,
    reference_portrait_prompt,
    strip_style_words,
)
from apps.api.modules.consistency.models import CharacterProfile, StyleProfile

APPEARANCE = {
    "age_range": "30 出头",
    "hair": "黑色短发，右侧偏分",
    "eyes": "深褐色",
    "face": "瘦削，左眉有疤",
    "build": "偏瘦",
    "outfit": "深灰长风衣",
    "distinctive": "银色怀表",
}


def _style(**over: object) -> StyleProfile:
    base = {
        "org_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "base_model": "wan2.2-t2i-flash",
        "positive_tokens": "日式动画风格，赛璐璐上色",
        "negative_tokens": "真人照片，畸变，水印",
        "color_grading": "低饱和，冷调",
        "line_weight": "中等线宽",
        "render_mode": "赛璐璐",
        "seed_base": 100_000,
    }
    base.update(over)
    return StyleProfile(**base)  # type: ignore[arg-type]


def _character(ref: str = "lin_shu") -> CharacterProfile:
    c = CharacterProfile(
        org_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        ref=ref,
        name="林舒",
        appearance_json=dict(APPEARANCE),
    )
    c.id = uuid.uuid4()
    return c


# ------------------------------------------------------------------ 风格剥离


def test_agent_style_words_are_stripped() -> None:
    """Agent 自己写的风格词必须被丢掉。"""
    content = "站在码头，cinematic lighting, 8k, masterpiece, 电影感"
    out = strip_style_words(content)
    for word in ("cinematic", "8k", "masterpiece", "电影感"):
        assert word not in out
    assert "站在码头" in out, "画面内容不能被误伤"


def test_stripping_cleans_up_leftover_punctuation() -> None:
    out = strip_style_words("正对镜头，photorealistic，, 神情警觉")
    assert ",," not in out
    assert "，，" not in out
    assert out.startswith("正对镜头")


def test_stripping_keeps_pure_content() -> None:
    content = "侧脸看向海面，雨水顺着下颌线滑落"
    assert strip_style_words(content) == content


# ------------------------------------------------------------------ 角色描述


def test_character_description_is_deterministic() -> None:
    """同一角色每次必须拼出**完全相同**的字符串。

    字段顺序一变，模型的注意力分布就变，出来的脸也跟着变。
    """
    c = _character()
    assert describe_character(c) == describe_character(c)


def test_character_description_includes_all_fields() -> None:
    text = describe_character(_character())
    for value in APPEARANCE.values():
        assert value in text


def test_missing_fields_do_not_leave_empty_separators() -> None:
    c = _character()
    c.appearance_json = {**APPEARANCE, "distinctive": ""}
    text = describe_character(c)
    assert "，，" not in text
    assert not text.endswith("，")


# ------------------------------------------------------------------ 合成


def test_style_is_appended_by_system() -> None:
    """风格词由系统追加，且在末尾。

    放最后是有意的：多数扩散模型对靠后的 token 更敏感，
    即便 Agent 的描述里漏掉一个风格词也压不过系统设定。
    """
    style = _style()
    out = compose_shot(content="站在集装箱前", style=style, characters=[_character()], shot_index=1)
    assert out.prompt.endswith(style.color_grading)
    assert style.positive_tokens in out.prompt
    assert out.negative_prompt == style.negative_tokens


def test_composition_order_is_character_content_style() -> None:
    out = compose_shot(
        content="站在集装箱前", style=_style(), characters=[_character()], shot_index=1
    )
    i_char = out.prompt.index("林舒")
    i_content = out.prompt.index("站在集装箱前")
    i_style = out.prompt.index("日式动画风格")
    assert i_char < i_content < i_style


def test_seed_is_deterministic_per_shot() -> None:
    """同一项目同一镜号必须得到同样的 seed。

    这样"改了提示词到底有没有变好"才有可比性。
    """
    style = _style(seed_base=100_000)
    a = compose_shot(content="x", style=style, characters=[], shot_index=7)
    b = compose_shot(content="x", style=style, characters=[], shot_index=7)
    c = compose_shot(content="x", style=style, characters=[], shot_index=8)
    assert a.seed == b.seed == 100_007
    assert c.seed == 100_008


def test_no_seed_when_style_has_no_base() -> None:
    out = compose_shot(content="x", style=_style(seed_base=0), characters=[], shot_index=1)
    assert out.seed is None


def test_agent_style_words_cannot_override_system_style() -> None:
    """Agent 想指定另一种画风也不行。"""
    out = compose_shot(
        content="站在码头，oil painting，watercolor",
        style=_style(),
        characters=[],
        shot_index=1,
    )
    assert "oil painting" not in out.prompt
    assert "watercolor" not in out.prompt
    assert "赛璐璐" in out.prompt


def test_character_ids_are_recorded() -> None:
    a, b = _character("a"), _character("b")
    out = compose_shot(content="x", style=_style(), characters=[a, b], shot_index=1)
    assert out.character_ids == [str(a.id), str(b.id)]


# ------------------------------------------------------------------ 基准立绘


def test_reference_portrait_is_neutral() -> None:
    """基准立绘必须中性。

    它是后续所有镜头的比对基准，任何戏剧化光影和构图都会污染它，
    让相似度失去意义。
    """
    prompt = reference_portrait_prompt(_character(), _style())
    for required in ("中性表情", "中性光照", "纯色背景"):
        assert required in prompt
    # 不带色调分级——那是镜头级的，不该进基准
    assert "低饱和，冷调" not in prompt
