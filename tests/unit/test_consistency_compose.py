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
    describe_scene,
    reference_portrait_prompt,
    reference_scene_prompt,
    strip_style_words,
)
from apps.api.modules.consistency.models import CharacterProfile, SceneProfile, StyleProfile

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


SPATIAL = {
    "time_slot": "上午",
    "setting": "爬满爬山虎的水泥墙围出的院落，锈迹斑斑的滑动铁门",
    "lighting": "上午均匀自然日光",
    "camera_axis": {
        "position": "铁门外的路面",
        "facing": "朝向建筑正面",
        "far_end": "红砖三层建筑的正门石阶",
    },
    "fixed_references": ["铁门在画面正前方", "门柱牌子在铁门右侧"],
    "key_elements": ["铁门", "红砖建筑"],
}


def _scene(ref: str = "gate") -> SceneProfile:
    sc = SceneProfile(
        org_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        ref=ref,
        name="资料馆门口",
        spatial_json={**SPATIAL, "camera_axis": dict(SPATIAL["camera_axis"])},  # type: ignore[dict-item]
    )
    sc.id = uuid.uuid4()
    return sc


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


# ------------------------------------------------------------------ 场景描述


def test_scene_description_is_deterministic() -> None:
    """同一场景每次必须拼出**完全相同**的字符串。

    和角色同理：字段顺序一变，同一个房间在两镜之间就会挪位置。
    """
    sc = _scene()
    assert describe_scene(sc) == describe_scene(sc)


def test_scene_description_carries_camera_axis_and_fixed_references() -> None:
    """摄影主轴和固定参照物必须进描述——它们是场景一致性的全部依据。

    只存进数据库不用上，场景出图看着和角色出图一样，但没有任何
    一致性保证，这条链路就白做了。
    """
    text = describe_scene(_scene())
    axis = SPATIAL["camera_axis"]
    assert isinstance(axis, dict)
    for value in axis.values():
        assert value in text, f"摄影主轴的 {value} 没进描述"
    for ref in SPATIAL["fixed_references"]:
        assert ref in text, f"固定参照物 {ref} 没进描述"
    assert SPATIAL["setting"] in text
    assert SPATIAL["lighting"] in text


def test_scene_missing_fields_do_not_leave_empty_separators() -> None:
    sc = _scene()
    sc.spatial_json = {
        **SPATIAL,
        "lighting": "",
        "fixed_references": [],
        "camera_axis": {"position": "门内", "facing": "", "far_end": ""},
    }
    text = describe_scene(sc)
    assert "，，" not in text
    assert not text.endswith("，")
    assert "门内" in text


# ------------------------------------------------------------------ 场景 → 镜头


def test_composition_order_is_character_scene_content_style() -> None:
    """结构固定为 角色 → 场景 → 画面 → 风格。"""
    out = compose_shot(
        content="站在集装箱前",
        style=_style(),
        characters=[_character()],
        shot_index=1,
        scene=_scene(),
    )
    i_char = out.prompt.index("林舒")
    i_scene = out.prompt.index("铁门外的路面")
    i_content = out.prompt.index("站在集装箱前")
    i_style = out.prompt.index("日式动画风格")
    assert i_char < i_scene < i_content < i_style


def test_shot_without_scene_keeps_old_behaviour() -> None:
    """不传场景时必须完全等同于加场景之前的行为。

    镜头出图在场景档案存在之前就跑通了。场景做成硬要求会让所有
    存量项目突然出不了图——多一份空间锚点是改进，不是新门槛。
    """
    kwargs: dict[str, object] = {
        "content": "站在集装箱前",
        "style": _style(),
        "characters": [_character()],
        "shot_index": 1,
    }
    without = compose_shot(**kwargs)  # type: ignore[arg-type]
    explicit_none = compose_shot(**kwargs, scene=None)  # type: ignore[arg-type]
    assert without.prompt == explicit_none.prompt
    assert without.scene_id is None
    assert "铁门" not in without.prompt


def test_scene_id_is_recorded_on_composition() -> None:
    sc = _scene()
    out = compose_shot(content="x", style=_style(), characters=[], shot_index=1, scene=sc)
    assert out.scene_id == str(sc.id)


def test_agent_style_words_in_shot_do_not_survive_scene_injection() -> None:
    """加了场景段之后，风格剥离仍然只作用在 Agent 的画面内容上。"""
    out = compose_shot(
        content="站在码头，oil painting",
        style=_style(),
        characters=[],
        shot_index=1,
        scene=_scene(),
    )
    assert "oil painting" not in out.prompt
    assert "赛璐璐" in out.prompt


# ------------------------------------------------------------------ 场景参考图


def test_reference_scene_prompt_anchors_space() -> None:
    """基准参考图必须把摄影主轴变成构图指令，不只是描述。

    没有这一句，模型每次自己挑一个角度，这张图当不了基准。
    """
    prompt = reference_scene_prompt(_scene(), _style())
    assert "摄影主轴" in prompt
    assert "固定参照物" in prompt
    assert "铁门外的路面" in prompt
    assert _style().positive_tokens in prompt, "风格词必须由系统注入"


def test_reference_scene_prompt_does_not_forbid_people() -> None:
    """克制不等于加约束。

    场景档案里可能本来就写着"门口站着门卫"，强加"无人物"会跟设计意图
    打架，而基准图要的是空间对不对，不是画面里有没有人。
    """
    prompt = reference_scene_prompt(_scene(), _style())
    for forbidden in ("无人物", "无人", "空无一人"):
        assert forbidden not in prompt


def test_camera_axis_label_is_not_duplicated() -> None:
    """`facing` 的字段说明就是"朝向"，Agent 多半会连"朝向"一起写进值里。

    再前缀一次就是"朝向朝向建筑正面"——不报错，只是让模型把注意力
    分给一句废话。
    """
    sc = _scene()
    sc.spatial_json = {
        **SPATIAL,
        "camera_axis": {
            "position": "铁门外的路面",
            "facing": "朝向建筑正面",
            "far_end": "红砖三层建筑的正门石阶",
        },
    }
    text = describe_scene(sc)
    assert "朝向朝向" not in text
    assert "朝向建筑正面" in text


def test_camera_axis_label_is_added_when_missing() -> None:
    """值里没带说明词时，说明词还是要加——否则就是三个孤立的名词。"""
    sc = _scene()
    sc.spatial_json = {
        **SPATIAL,
        "camera_axis": {"position": "路面", "facing": "建筑正面", "far_end": "石阶"},
    }
    text = describe_scene(sc)
    assert "摄影机位于路面" in text
    assert "朝向建筑正面" in text
    assert "远景末端是石阶" in text
