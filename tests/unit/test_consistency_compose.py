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
    describe_fixed_reference,
    describe_scene,
    reference_portrait_prompt,
    reference_scene_prompt,
    resolve_lighting,
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
        # 三套描述词各注入各的（ADR-036 第 3 条）。故意写成三段互不包含的词，
        # 断言"用错了哪一套"时才能分辨得出来——三套写成一样等于没测。
        "character_tokens": "日式动画风格，赛璐璐上色",
        "scene_tokens": "日式动画背景美术，空场景无人物",
        "video_tokens": "日式动画风格，24fps 电影帧率",
        "negative_tokens": "真人照片，畸变，水印",
        "color_grading": "低饱和，冷调",
        "line_weight": "中等线宽",
        "render_mode": "赛璐璐",
        "seed_base": 100_000,
    }
    base.update(over)
    return StyleProfile(**base)  # type: ignore[arg-type]


# 光照给两个状态、锚点给两段式：这是 `_spatial()` 落库之后的真实形状。
# 两个状态是必须的——只给一个的话"注入被引用的那一个、不注入另一个"
# 这条断言就退化成"注入了唯一的那一个"，测不出任何东西。
SPATIAL = {
    "time_slot": "上午",
    "setting": "爬满爬山虎的水泥墙围出的院落，锈迹斑斑的滑动铁门",
    "lighting_states": [
        {
            "name": "上午",
            "description": "均匀自然日光自左上方射入，阴影短小",
            "origin": "authored",
        },
        {
            "name": "夜巡灯",
            "description": "门柱顶灯自上方直射，围墙外一片死黑",
            "origin": "authored",
        },
    ],
    "default_lighting": "上午",
    "camera_axis": {
        "position": "铁门外的路面",
        "facing": "朝向建筑正面",
        "far_end": "红砖三层建筑的正门石阶",
    },
    "fixed_references": [
        {
            "name": "锈迹铁门",
            "description": "画面正前方的双开滑动铁门，右扇下缘锈穿一个巴掌大的洞",
            "origin": "authored",
        },
        {
            "name": "门柱铜牌",
            "description": "铁门右侧砖柱上齐胸高的白底黑字铜牌，右下角螺丝缺一颗",
            "origin": "authored",
        },
    ],
    "key_elements": ["铁门", "红砖建筑"],
}

# 存量形状：迁移之前落库的样子。留着它是为了证明**运行时**也扛得住——
# 迁移把库里的数据改过来了，但备份恢复、以及模型照着旧样例输出时还会
# 出现这个形状，那时不该表现为"光照凭空消失"。
LEGACY_SPATIAL = {
    "time_slot": "上午",
    "setting": "爬满爬山虎的水泥墙围出的院落",
    "lighting": "上午均匀自然日光",
    "camera_axis": {"position": "铁门外的路面", "facing": "朝向建筑正面", "far_end": "石阶"},
    "fixed_references": ["铁门在画面正前方", "门柱牌子在铁门右侧"],
    "key_elements": ["铁门"],
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
    assert style.character_tokens in out.prompt
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
    refs = SPATIAL["fixed_references"]
    assert isinstance(refs, list)
    for ref in refs:
        assert isinstance(ref, dict)
        # 名称和描述都要在：名称是名词（模型才知道画的是什么东西），
        # 描述是能复现的细节。少哪一半这条锚点都不成立。
        assert ref["name"] in text, f"固定参照物名称 {ref['name']} 没进描述"
        assert ref["description"] in text, f"固定参照物 {ref['name']} 的描述没进描述"
    assert SPATIAL["setting"] in text


def test_scene_missing_fields_do_not_leave_empty_separators() -> None:
    sc = _scene()
    sc.spatial_json = {
        **SPATIAL,
        "lighting_states": [{"name": "默认", "description": "", "origin": "migrated"}],
        "default_lighting": "默认",
        "fixed_references": [],
        "camera_axis": {"position": "门内", "facing": "", "far_end": ""},
    }
    text = describe_scene(sc)
    assert "，，" not in text
    assert not text.endswith("，")
    assert "门内" in text


# ------------------------------------------------------------------ 光照状态


def test_shot_gets_only_the_referenced_lighting_state() -> None:
    """引用了哪个状态就注入哪个，**另一个不能出现**。

    这是这次改动的全部价值所在。把全部状态一起塞进去，模型看到的是
    互相矛盾的指令（"上午"和"夜巡灯"同时成立），结果只会是它自己挑一个
    ——那和改动之前的"每镜自由发挥"一模一样。
    """
    text = describe_scene(_scene(), "夜巡灯")
    assert "门柱顶灯自上方直射" in text
    assert "均匀自然日光" not in text, "没被引用的那个状态漏进提示词了"


def test_unknown_lighting_ref_degrades_to_default() -> None:
    """引用了这个场景没声明过的状态 → 落到默认状态，不报错。

    降级而不是报错的理由见 `compose.resolve_lighting`：引用跨两份 Agent
    产出，pydantic 层判不了；而为一镜写错名字让 600 镜的分镜表整份失败，
    代价完全不成比例。降级后的行为**正是这个字段存在之前的行为**。
    """
    text = describe_scene(_scene(), "黄昏")
    assert "均匀自然日光" in text, "没落到默认状态上"
    assert "门柱顶灯" not in text


def test_empty_lighting_ref_uses_default() -> None:
    """留空 = 用默认状态。分镜提示词明说"拿不准就留空"，这条路必须是安全的。"""
    assert describe_scene(_scene(), "") == describe_scene(_scene(), "上午")


def test_single_state_scene_always_resolves_to_that_state() -> None:
    """只声明一个状态的场景：填什么、不填什么，都得到那一个。

    室内不分时的场景就是这种。它必须**永远解析得出一个状态**——
    "没有默认状态"意味着漏填的镜头行为未定义，那正是这次要消灭的东西。
    """
    sc = _scene()
    only = {"name": "常态", "description": "顶灯与台灯为主，整体偏暗", "origin": "authored"}
    sc.spatial_json = {**SPATIAL, "lighting_states": [only], "default_lighting": "常态"}
    for ref in ("常态", "", "根本不存在的状态"):
        assert resolve_lighting(sc.spatial_json, ref)["name"] == "常态"
        assert only["description"] in describe_scene(sc, ref)


def test_default_lighting_pointing_nowhere_falls_back_to_first_state() -> None:
    """场景自己的 `default_lighting` 指向一个不存在的状态时也不能悬空。

    模型漏填、或者用户在字段级编辑里把某个状态改了名，都会造出这种数据。
    """
    sc = _scene()
    sc.spatial_json = {**SPATIAL, "default_lighting": "已经被改名的状态"}
    assert resolve_lighting(sc.spatial_json, "")["name"] == "上午"


def test_composed_reports_which_lighting_state_was_actually_used() -> None:
    """`Composed.lighting_state` 带回实际用的名字——降级要能被发现。

    合成这一层是纯函数，拿不到 project_id / shot_index，所以不在这里记
    日志；把实际用的名字带回去，`render` 那一层一比就知道发生了降级。
    """
    out = compose_shot(
        content="站在铁门外",
        style=_style(),
        characters=[],
        shot_index=1,
        scene=_scene(),
        lighting_ref="黄昏",
    )
    assert out.lighting_state == "上午", "降级之后没有把真正用的状态带回去"

    ok = compose_shot(
        content="站在铁门外",
        style=_style(),
        characters=[],
        shot_index=1,
        scene=_scene(),
        lighting_ref="夜巡灯",
    )
    assert ok.lighting_state == "夜巡灯"


def test_shot_without_scene_reports_no_lighting_state() -> None:
    """没有场景就谈不上光照状态，不能凭空报一个名字出来。"""
    out = compose_shot(content="特写", style=_style(), characters=[], shot_index=1)
    assert out.lighting_state == ""


def test_scene_reference_prompt_uses_the_default_state() -> None:
    """场景基准图用默认状态。

    这张图是同一场景后续所有镜头的空间基准，用哪种光当基准不该由某一镜
    决定——默认状态就是场景档案自己选出来的那一个。
    """
    prompt = reference_scene_prompt(_scene(), _style())
    assert "均匀自然日光" in prompt
    assert "门柱顶灯" not in prompt


# ------------------------------------------------------------------ 结构化锚点


def test_fixed_reference_shows_name_and_description() -> None:
    """名称是名词、描述是细节，两段都要进提示词。

    只给描述会让"绿锈斑驳，缆绳打着水手结"悬在半空没有主语；
    只给名称就退回到"有一个铜铃"，在扩散模型里等价于没写。
    """
    text = describe_fixed_reference(
        {"name": "船头铜铃", "description": "拳头大的旧铜铃挂在篷杆前端，绿锈斑驳"}
    )
    assert text == "船头铜铃（拳头大的旧铜铃挂在篷杆前端，绿锈斑驳）"


def test_migrated_fixed_reference_is_not_said_twice() -> None:
    """存量锚点的名称是描述的前若干字，拼一起会把同一句话说两遍。

    "铁门在画面正前（铁门在画面正前方）"不报错，只是让模型把注意力
    分给一句废话——和摄影主轴那条"朝向朝向建筑正面"是同一类问题。
    """
    text = describe_fixed_reference(
        {"name": "铁门在画面正前", "description": "铁门在画面正前方", "origin": "migrated"}
    )
    assert text == "铁门在画面正前方"


# --------------------------------------------------------------- 存量形状兜底


def test_legacy_flat_spatial_still_yields_lighting_and_anchors() -> None:
    """没被迁移到的存量行（备份恢复）不能表现为"光照和锚点凭空消失"。

    迁移把库里的数据改过来了，但这一层仍要扛得住旧形状：少一段光照描述
    不报错，只会让这一镜的光由模型自由发挥，而那是查起来最费劲的一类问题。
    """
    sc = _scene()
    sc.spatial_json = dict(LEGACY_SPATIAL)
    text = describe_scene(sc)
    assert "上午均匀自然日光" in text, "旧的 lighting 字段没被读到"
    assert "铁门在画面正前方" in text, "旧的扁平锚点没进描述"


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
    assert _style().scene_tokens in prompt, "场景版风格词必须由系统注入"


def test_reference_scene_prompt_uses_the_scene_token_set() -> None:
    """场景基准图注入的是**场景版**风格词，不是人物版。

    这条断言反转了它的前身（`test_reference_scene_prompt_does_not_forbid_people`）。
    那一版的理由是"场景档案里可能本来就写着门口站着门卫，强加无人物会跟
    设计意图打架"。**ADR-036 第 3 条推翻了它**：场景参考图要的就是空场景，
    因为它是同一场景后续所有镜头的空间基准——基准图里画进一个人，
    那个人就会被当成这个空间的一部分带进每一镜。

    "画面里有没有人"改由镜头层决定：`compose_shot` 看这一镜有没有角色，
    有人用人物版、空镜用场景版。基准图不承担这件事。
    """
    prompt = reference_scene_prompt(_scene(), _style())
    style = _style()
    assert style.scene_tokens in prompt
    assert style.character_tokens not in prompt, "场景基准图不该带人物质感词"


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
